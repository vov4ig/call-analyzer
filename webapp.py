"""Local web UI for the offline call-analysis pipeline.

Upload an audio/video file -> run_call.py (ASR + alignment + diarization)
-> analyze_call.py (Ollama / qwen2.5:7b) -> formatted report in the browser.

Stdlib only: no extra packages, no internet.

    python webapp.py                 # http://127.0.0.1:8080
    python webapp.py --open          # also open the browser
    python webapp.py --host 0.0.0.0  # allow LAN access
    python webapp.py --token SECRET  # require ?token=SECRET (LAN safety)
"""
import argparse
import html
import io
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cc_paths

BASE_DIR = Path(__file__).resolve().parent
ROOT = cc_paths.HOME
WEB_DIR = cc_paths.WEB
JOBS_DIR = cc_paths.JOBS
META_DIR = cc_paths.META
LOG_DIR = cc_paths.LOGS
PY = sys.executable
RUN_CALL = str(BASE_DIR / "run_call.py")
ANALYZE_CALL = str(BASE_DIR / "analyze_call.py")
OLLAMA = cc_paths.ollama_url()
OLLAMA_MODELS = str(cc_paths.ollama_models())

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
             ".wma", ".amr", ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
MAX_UPLOAD = 2 * 1024 ** 3          # 2 GB
LANGS = ["ru", "en", "uk", "de", "fr", "es", "it", "pl", "tr", "kk"]

STAGES = [
    ("transcribe", "Распознавание речи"),
    ("align", "Выравнивание слов"),
    ("diarize", "Разделение говорящих"),
    ("analyze", "Анализ ИИ"),
]
STAGE_LABEL = dict(STAGES)
STAGE_LABEL["queued"] = "В очереди"
STAGE_LABEL["done"] = "Готово"

JOBS = {}
JOBS_LOCK = threading.Lock()
WORK_QUEUE = queue.Queue()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def log_line(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "webapp.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def ollama_status(timeout=2):
    try:
        with urllib.request.urlopen(OLLAMA + "/api/version", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return True, data.get("version", "?")
    except Exception as exc:
        return False, str(exc)


def fmt_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return ("%d %s" % (n, unit)) if unit == "Б" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def lan_ips():
    """Best-effort list of this machine's LAN IPv4 addresses (no network needed)."""
    ips = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 80))     # UDP connect sends no packets
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def safe_name(name, fallback="audio"):
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name)
    if not name or name in (".", ".."):
        name = fallback
    return name[:180]


def md_to_html(text):
    """Tiny markdown renderer: headings, bullets, bold, `code`, paragraphs."""
    def inline(s):
        s = html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        return s

    out, para, in_ul = [], [], False

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            flush_para()
            close_ul()
            continue
        if line.startswith("## "):
            flush_para(); close_ul()
            out.append("<h2>" + inline(line[3:]) + "</h2>")
        elif line.startswith("# "):
            flush_para(); close_ul()
            out.append("<h1>" + inline(line[2:]) + "</h1>")
        elif re.match(r"^\s*[-*]\s+", line):
            flush_para()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + inline(re.sub(r"^\s*[-*]\s+", "", line)) + "</li>")
        else:
            close_ul()
            para.append(line)
    flush_para()
    close_ul()
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, job_id, name, opts):
        self.id = job_id
        self.name = name
        self.opts = opts
        self.state = "queued"
        self.stage = "queued"
        self.progress = 2
        self.error = None
        self.log = []
        self.metrics = {}
        self.report_html = ""
        self.dialogue = ""
        self.created = time.time()
        self.started = None
        self.finished = None
        self.lock = threading.Lock()

    def update(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def add_log(self, line):
        with self.lock:
            self.log.append(line)
            if len(self.log) > 500:
                del self.log[:-500]

    def dir(self):
        return JOBS_DIR / self.id

    def snapshot(self):
        with self.lock:
            elapsed = ((self.finished or time.time()) - (self.started or self.created))
            files = list_files(self.dir())
            return {
                "id": self.id,
                "name": self.name,
                "state": self.state,
                "stage": self.stage,
                "stage_label": STAGE_LABEL.get(self.stage, self.stage),
                "progress": self.progress,
                "error": self.error,
                "log": self.log[-80:],
                "metrics": dict(self.metrics),
                "report_html": self.report_html,
                "dialogue": self.dialogue,
                "files": files,
                "created": self.created,
                "elapsed": round(elapsed, 1),
                "options": self.opts,
            }

    def save_meta(self):
        meta = self.snapshot()
        meta.pop("report_html", None)
        meta.pop("log", None)
        meta["log_tail"] = self.log[-40:]
        try:
            META_DIR.mkdir(parents=True, exist_ok=True)
            (META_DIR / (self.id + ".json")).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def list_files(job_dir):
    labels = {".json": "JSON (сегменты)", ".txt": "Диалог (TXT)",
              ".srt": "Субтитры (SRT)", ".report.md": "Отчёт (Markdown)"}
    result = []
    for p in sorted(job_dir.glob("*")):
        if not p.is_file():
            continue
        if p.name == "meta.json":          # legacy job metadata, not a transcript
            try:
                if "segments" not in json.loads(p.read_text(encoding="utf-8")):
                    continue
            except (OSError, ValueError):
                continue
        suffix = ".report.md" if p.name.endswith(".report.md") else p.suffix.lower()
        if suffix in labels:
            result.append({"name": p.name, "label": labels[suffix],
                           "size": fmt_size(p.stat().st_size)})
    return result


def read_meta(job_id):
    """Snapshot for a job that is not in memory (server restarted)."""
    job_dir = JOBS_DIR / job_id
    meta_path = META_DIR / (job_id + ".json")
    if not meta_path.exists():
        meta_path = job_dir / "meta.json"      # older layouts
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta.setdefault("metrics", {})
    meta.setdefault("dialogue", "")
    meta["files"] = list_files(job_dir)
    meta["log"] = meta.pop("log_tail", [])
    report = next(iter(sorted(job_dir.glob("*.report.md"))), None)
    meta["report_html"] = md_to_html(report.read_text(encoding="utf-8")) if report else ""
    if meta["state"] == "running":
        meta["state"] = "error"
        meta["error"] = meta.get("error") or "Сервер был перезапущен во время обработки"
    meta.setdefault("elapsed", 0)
    return meta


def recent_jobs(limit=20):
    if not JOBS_DIR.exists():
        return []
    items = []
    for p in sorted(JOBS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        meta = read_meta(p.name)
        if not meta:
            continue
        items.append({k: meta.get(k) for k in
                      ("id", "name", "state", "stage", "progress", "created", "error")})
        if len(items) >= limit:
            break
    return items


def update_from_line(job, line):
    m = re.search(r"ASR done in ([\d.]+)s: (\d+) segments", line)
    if m:
        job.metrics.update(asr_sec=float(m.group(1)), segments=int(m.group(2)))
        job.update(stage="align", progress=45)
        return
    m = re.search(r"alignment done in ([\d.]+)s", line)
    if m:
        job.metrics["align_sec"] = float(m.group(1))
        job.update(stage="diarize", progress=62)
        return
    if "WARNING: alignment skipped" in line:
        job.metrics["align_skipped"] = True
        job.update(stage="diarize", progress=62)
        return
    m = re.search(r"diarization done in ([\d.]+)s: (\d+) turns, (\d+) speakers", line)
    if m:
        job.metrics.update(diar_sec=float(m.group(1)), turns=int(m.group(2)),
                           speakers=int(m.group(3)))
        job.update(progress=72)
        return
    if "ASR large-v3" in line:
        job.update(stage="transcribe", progress=12)
        return
    m = re.search(r"\[([\d.]+)s\] saved -> ", line)
    if m:
        job.metrics["analyze_sec"] = float(m.group(1))
        return
    if "RUN_CALL_DONE" in line:
        job.update(stage="analyze", progress=76)
        return


def run_pipeline(job):
    job_dir = job.dir()
    job_dir.mkdir(parents=True, exist_ok=True)
    audio = job_dir / job.name

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    try:
        ffmpeg_bin = cc_paths.find_ffmpeg()
    except SystemExit as exc:
        raise RuntimeError(str(exc))
    env["PATH"] = str(Path(ffmpeg_bin).parent) + os.pathsep + env.get("PATH", "")
    env["OLLAMA_MODELS"] = OLLAMA_MODELS

    ok, ver = ollama_status()
    if not ok:
        raise RuntimeError(
            "Ollama не отвечает на 127.0.0.1:11434. Запустите Ollama "
            "(ollama app.exe) и повторите — анализ делает локальная модель qwen2.5:7b.")

    opts = job.opts
    cmd = [PY, RUN_CALL, str(audio), "--lang", opts["lang"]]
    if opts.get("speakers"):
        cmd += ["--num-speakers", str(opts["speakers"])]
    if not opts.get("align", True):
        cmd += ["--no-align"]

    job.update(state="running", stage="transcribe", progress=8,
               started=time.time(), error=None)
    job.save_meta()
    log_line("job %s: start (%s, lang=%s, speakers=%s)" %
             (job.id, audio.name, opts["lang"], opts.get("speakers") or "auto"))

    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        if not line:
            continue
        job.add_log(line)
        update_from_line(job, line)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError("Транскрипция завершилась с ошибкой (код %s). См. лог." % rc)

    json_path = None
    for candidate in sorted(job_dir.glob("*.json")):
        try:
            probe = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(probe, dict) and "segments" in probe:
            json_path = candidate
            break
    if json_path is None:
        raise RuntimeError("Не найден результат транскрипции (.json)")

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        job.metrics["duration"] = float(data.get("duration", 0))
        job.metrics["speakers"] = len(data.get("speakers", []))
        job.dialogue = (job_dir / (json_path.stem + ".txt")).read_text(
            encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass

    job.update(stage="analyze", progress=78)
    job.save_meta()
    log_line("job %s: analyze" % job.id)
    proc = subprocess.Popen([PY, ANALYZE_CALL, str(json_path)], cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    for line in proc.stdout:
        line = line.rstrip("\r\n")
        if not line:
            continue
        job.add_log(line)
        update_from_line(job, line)
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError("Анализ ИИ завершился с ошибкой (код %s). См. лог." % rc)

    report = next(iter(sorted(job_dir.glob("*.report.md"))), None)
    if report is None:
        raise RuntimeError("Не найден файл отчёта (.report.md)")
    job.update(report_html=md_to_html(report.read_text(encoding="utf-8")),
               state="done", stage="done", progress=100, finished=time.time())
    job.save_meta()
    log_line("job %s: done in %.1fs" % (job.id, job.finished - job.started))


def worker():
    while True:
        job = WORK_QUEUE.get()
        if job is None:
            return
        try:
            run_pipeline(job)
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            job.update(state="error", error=msg, finished=time.time())
            job.add_log("ERROR: " + msg)
            job.save_meta()
            log_line("job %s: ERROR %s" % (job.id, msg))
            traceback.print_exc()
        finally:
            WORK_QUEUE.task_done()


def prune_jobs(keep=40):
    if not JOBS_DIR.exists():
        return
    dirs = [p for p in JOBS_DIR.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        try:
            (META_DIR / (old.name + ".json")).unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# multipart upload (streaming, stdlib only)
# --------------------------------------------------------------------------- #
class MultipartError(Exception):
    pass


class BodyReader:
    def __init__(self, rfile, length):
        self.rfile = rfile
        self.left = length
        self.buf = b""

    def _fill(self, n=65536):
        if self.left <= 0:
            return False
        chunk = self.rfile.read(min(n, self.left))
        if not chunk:
            raise MultipartError("Неожиданный конец данных")
        self.left -= len(chunk)
        self.buf += chunk
        return True

    def read_line(self):
        while b"\r\n" not in self.buf:
            if not self._fill(4096):
                raise MultipartError("Неожиданный конец заголовков")
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line

    def read_exact(self, n):
        while len(self.buf) < n:
            if not self._fill():
                raise MultipartError("Неожиданный конец данных")
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_part(self, sink, delim):
        keep = len(delim) - 1
        while True:
            idx = self.buf.find(delim)
            if idx != -1:
                sink.write(self.buf[:idx])
                self.buf = self.buf[idx + len(delim):]
                return
            if len(self.buf) > keep:
                sink.write(self.buf[:-keep])
                self.buf = self.buf[-keep:]
            if not self._fill():
                raise MultipartError("Файл не завершён")


def parse_disposition(value):
    name = filename = None
    m = re.search(r'name="([^"]*)"', value)
    if m:
        name = m.group(1)
    m = re.search(r'filename="([^"]*)"', value)
    if m:
        filename = m.group(1)
    return name, filename


def receive_upload(handler):
    ctype = handler.headers.get("Content-Type", "")
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if "multipart/form-data" not in ctype or not m:
        raise MultipartError("Ожидался multipart/form-data")
    boundary = m.group(1).encode("utf-8")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise MultipartError("Некорректный Content-Length")
    if length <= 0:
        raise MultipartError("Пустой запрос")
    if length > MAX_UPLOAD:
        raise MultipartError("Файл больше %s" % fmt_size(MAX_UPLOAD))

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    reader = BodyReader(handler.rfile, length)
    start = reader.read_line().strip()
    if start != b"--" + boundary:
        raise MultipartError("Некорректная граница multipart")

    fields, audio_name, audio_size = {}, None, 0
    delim = b"\r\n--" + boundary

    while True:
        headers = {}
        while True:
            line = reader.read_line()
            if not line:
                break
            key, _, val = line.partition(b":")
            headers[key.strip().lower()] = val.strip()
        disposition = headers.get(b"content-disposition", b"").decode("utf-8", "replace")
        field, filename = parse_disposition(disposition)

        if filename is None:
            sink = io.BytesIO()
            reader.read_part(sink, delim)
            fields[field] = sink.getvalue().decode("utf-8", "replace").strip()
        else:
            orig = safe_name(filename)
            ext = Path(orig).suffix.lower()
            if ext not in AUDIO_EXT:
                raise MultipartError("Неподдерживаемый формат %s. Разрешены: %s" %
                                     (orig, ", ".join(sorted(AUDIO_EXT))))
            audio_name = safe_name(orig)
            target = job_dir / audio_name
            with open(target, "wb") as fh:
                reader.read_part(fh, delim)
            audio_size = target.stat().st_size
            if audio_size == 0:
                raise MultipartError("Загруженный файл пуст")
            if audio_size > MAX_UPLOAD:
                raise MultipartError("Файл больше %s" % fmt_size(MAX_UPLOAD))

        tail = reader.read_exact(2)
        if tail == b"--":
            break
        if tail != b"\r\n":
            raise MultipartError("Некорректный разделитель multipart")

    if not audio_name:
        raise MultipartError("Файл не найден в запросе")

    lang = fields.get("lang", "ru")
    if lang not in LANGS:
        lang = "ru"
    speakers = fields.get("speakers", "").strip()
    speakers = int(speakers) if speakers.isdigit() and 1 <= int(speakers) <= 20 else None
    align = fields.get("align", "1") != "0"

    opts = {"lang": lang, "speakers": speakers, "align": align}
    job = Job(job_id, audio_name, opts)
    job.update(progress=3)
    with JOBS_LOCK:
        JOBS[job_id] = job
    job.save_meta()
    WORK_QUEUE.put(job)
    log_line("job %s: queued %s (%s, lang=%s)" %
             (job_id, audio_name, fmt_size(audio_size), lang))
    return job_id


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "CallAnalyzer/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        # log only remote clients: localhost polling would flood the log
        client = self.client_address[0] if self.client_address else "?"
        if client.startswith("127.") or client in ("::1", "localhost"):
            return
        log_line("http %s %s from %s -> %s" % (self.command, self.path, client, code))

    # -- optional token auth (--token) -------------------------------------- #
    def _query_token(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return query.get("token", [])

    def _token_ok(self):
        token = getattr(self.server, "token", None)
        if not token:
            return True
        if token in self._query_token():
            return True
        if self.headers.get("X-Token") == token:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        morsel = cookie.get("ca_token")
        return bool(morsel) and morsel.value == token

    def _auth_headers(self):
        """Remember a valid ?token=... in a cookie so polling keeps working."""
        token = getattr(self.server, "token", None)
        if token and token in self._query_token():
            return {"Set-Cookie": "ca_token=%s; Path=/; HttpOnly; SameSite=Lax"
                    % urllib.parse.quote(token)}
        return {}

    # -- response helpers -------------------------------------------------- #
    def send_bytes(self, data, ctype="text/html; charset=utf-8", status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def send_error_json(self, msg, status=400):
        self.close_connection = True
        self.send_json({"error": msg}, status)

    def redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- routing ----------------------------------------------------------- #
    def do_GET(self):
        if not self._token_ok():
            return self.send_bytes(TOKEN_PAGE.encode("utf-8"), status=401)
        path = urllib.parse.urlparse(self.path).path
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/") if p]

        if path in ("/", "/index.html"):
            return self.send_bytes(PAGE.encode("utf-8"), headers=self._auth_headers())

        if path == "/api/health":
            ok, ver = ollama_status()
            return self.send_json({"ok": True, "ollama": ok, "ollama_version": ver,
                                   "models_dir": OLLAMA_MODELS})

        if path == "/api/jobs":
            return self.send_json({"jobs": recent_jobs()})

        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "jobs":
            job_id = parts[2]
            if len(parts) == 3:
                snap = self.get_snapshot(job_id)
                if not snap:
                    return self.send_json({"error": "Задача не найдена"}, 404)
                return self.send_json(snap)

            if len(parts) == 5 and parts[3] == "file":
                return self.serve_job_file(job_id, parts[4])

        return self.send_json({"error": "Не найдено"}, 404)

    def get_snapshot(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is not None:
            return job.snapshot()
        return read_meta(job_id)

    def serve_job_file(self, job_id, name):
        job_dir = (JOBS_DIR / job_id).resolve()
        allowed = {f["name"] for f in list_files(job_dir)}
        if name not in allowed:
            return self.send_json({"error": "Файл не найден"}, 404)
        target = (job_dir / name).resolve()
        if target.parent != job_dir or not target.exists():
            return self.send_json({"error": "Файл не найден"}, 404)
        data = target.read_bytes()
        suffix = ".report.md" if name.endswith(".report.md") else target.suffix.lower()
        ctypes = {".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
                  ".json": "application/json; charset=utf-8",
                  ".srt": "application/x-subrip; charset=utf-8"}
        ctype = ctypes.get(suffix, "application/octet-stream")
        ascii_fallback = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        disposition = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
            ascii_fallback, urllib.parse.quote(name))
        return self.send_bytes(data, ctype, 200, {"Content-Disposition": disposition})

    def do_POST(self):
        if not self._token_ok():
            return self.send_error_json("Требуется токен", 401)
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/upload":
            return self.send_error_json("Не найдено", 404)
        try:
            job_id = receive_upload(self)
        except MultipartError as exc:
            return self.send_error_json(str(exc), 400)
        except Exception:
            traceback.print_exc()
            return self.send_error_json("Внутренняя ошибка при загрузке файла", 500)
        self.send_json({"id": job_id}, 200)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    token = None            # set from --token; None disables auth

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
TOKEN_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Нужен токен</title>
<style>body{margin:0;background:#0b1220;color:#e6edf7;font:16px/1.6 "Segoe UI",sans-serif;
display:grid;place-items:center;min-height:100vh}div{max-width:520px;padding:28px;
border:1px solid #22304a;border-radius:14px;background:#131d31}code{background:#0e1728;
padding:2px 6px;border-radius:5px}</style></head>
<body><div><h1>Нужен токен</h1>
<p>Сервер запущен с проверкой доступа. Откройте ссылку с токеном, который напечатан
в окне сервера:</p><p><code>http://адрес:порт/?token=ВАШ_ТОКЕН</code></p>
<p style="color:#8fa3bf">После первого открытия токен запоминается в cookie браузера.</p>
</div></body></html>
"""

PAGE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Анализ звонков — локально</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #0b1220; color: #e6edf7;
         font: 15px/1.55 "Segoe UI", system-ui, sans-serif; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 28px 20px 80px; }
  h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: .2px; }
  .sub { color: #8fa3bf; margin-bottom: 24px; }
  .card { background: #131d31; border: 1px solid #22304a;
          border-radius: 14px; padding: 20px; margin-bottom: 18px; }
  .drop { border: 2px dashed #2f4467; border-radius: 12px; padding: 34px 18px;
          text-align: center; cursor: pointer; transition: .15s; }
  .drop:hover, .drop.over { border-color: #38bdf8; background: #10243a; }
  .drop b { color: #38bdf8; }
  .muted { color: #8fa3bf; font-size: 13px; }
  .file { margin-top: 12px; font-size: 14px; color: #cfe3ff; word-break: break-all; }
  .opts { display: flex; flex-wrap: wrap; gap: 14px 22px; margin-top: 18px;
          align-items: flex-end; }
  .opt { display: flex; flex-direction: column; gap: 6px; }
  .opt label { font-size: 12px; text-transform: uppercase; letter-spacing: .6px;
               color: #8fa3bf; }
  select, button { font: inherit; }
  select { background: #0e1728; color: #e6edf7; border: 1px solid #2a3b58;
           border-radius: 9px; padding: 9px 12px; min-width: 150px; }
  .chk { display: flex; align-items: center; gap: 8px; padding-bottom: 9px; }
  input[type=checkbox] { width: 17px; height: 17px; accent-color: #38bdf8; }
  .btn { background: #38bdf8; color: #06121f; border: 0; border-radius: 10px;
         padding: 11px 22px; font-weight: 700; cursor: pointer; }
  .btn:hover { background: #67cdfb; }
  .btn[disabled] { opacity: .5; cursor: default; }
  .btn.ghost { background: #1b2940; color: #cfe3ff; border: 1px solid #2a3b58;
               font-weight: 500; }
  .progress { margin-top: 6px; }
  .bar { height: 10px; background: #0e1728; border-radius: 99px; overflow: hidden;
         border: 1px solid #22304a; }
  .bar > i { display: block; height: 100%; width: 0; background: linear-gradient(90deg,#2563eb,#38bdf8);
             transition: width .4s; }
  .row { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 10px;
         align-items: baseline; }
  .stages { display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }
  .chip { border: 1px solid #2a3b58; border-radius: 999px; padding: 5px 12px;
          font-size: 13px; color: #8fa3bf; }
  .chip.done { border-color: #1f6f4a; color: #7ee2b0; background: #0f2a20; }
  .chip.active { border-color: #38bdf8; color: #bae6fd; background: #10243a; }
  .log { background: #0a1120; border: 1px solid #1b2940; border-radius: 10px;
         padding: 10px 12px; font: 12px/1.5 Consolas, monospace; color: #9fb3cf;
         max-height: 190px; overflow: auto; white-space: pre-wrap; margin: 0; }
  .metrics { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 12px 0 0;
             font-size: 13px; color: #a9bdd8; }
  .metrics b { color: #e6edf7; font-weight: 600; }
  .tabs { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab { background: #0e1728; border: 1px solid #2a3b58; color: #a9bdd8;
         border-radius: 9px; padding: 8px 14px; cursor: pointer; }
  .tab.active { border-color: #38bdf8; color: #e6f6ff; background: #10243a; }
  .report h2 { font-size: 17px; margin: 22px 0 8px; color: #bae6fd;
               border-bottom: 1px solid #22304a; padding-bottom: 6px; }
  .report h1 { font-size: 21px; }
  .report p { margin: 8px 0; }
  .report ul { margin: 8px 0; padding-left: 22px; }
  .report li { margin: 4px 0; }
  .report code { background: #0e1728; border: 1px solid #22304a; border-radius: 5px;
                 padding: 1px 6px; font-size: 13px; }
  pre.dialog { background: #0a1120; border: 1px solid #1b2940; border-radius: 10px;
               padding: 14px; white-space: pre-wrap; overflow: auto; max-height: 520px;
               font: 13px/1.6 Consolas, monospace; color: #d7e4f5; margin: 0; }
  .files { display: grid; gap: 10px; }
  .files a { display: flex; justify-content: space-between; gap: 12px; text-decoration: none;
             color: #cfe3ff; background: #0e1728; border: 1px solid #2a3b58;
             border-radius: 10px; padding: 12px 14px; }
  .files a:hover { border-color: #38bdf8; }
  .files span { color: #8fa3bf; font-size: 13px; }
  .alert { border-radius: 10px; padding: 12px 14px; margin-top: 12px; font-size: 14px; }
  .alert.err { background: #2a1218; border: 1px solid #7f1d1d; color: #fecaca; }
  .alert.warn { background: #2a2412; border: 1px solid #7c5e12; color: #fde68a; }
  .history { display: grid; gap: 8px; }
  .history button { text-align: left; background: #0e1728; border: 1px solid #22304a;
                    color: #cfe3ff; border-radius: 10px; padding: 10px 12px; cursor: pointer; }
  .history button:hover { border-color: #38bdf8; }
  .history small { color: #8fa3bf; }
  .hide { display: none !important; }
  .spinner { display: inline-block; width: 13px; height: 13px; border: 2px solid #38bdf8;
             border-right-color: transparent; border-radius: 50%; animation: sp .8s linear infinite;
             vertical-align: -2px; margin-right: 7px; }
  @keyframes sp { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Анализ звонков</h1>
  <div class="sub">Whisper large-v3 · диаризация pyannote · локальная модель qwen2.5:7b — всё на этом компьютере, без интернета</div>

  <div id="warn" class="alert warn hide"></div>

  <div class="card">
    <div id="drop" class="drop" role="button" tabindex="0" aria-label="Выбрать аудиофайл">
      <div style="font-size:17px;margin-bottom:6px">Перетащите аудио или видео сюда</div>
      <div class="muted">или <b>нажмите, чтобы выбрать файл</b> — wav, mp3, m4a, ogg, flac, mp4, mkv и др., до 2 ГБ</div>
      <input id="file" type="file" class="hide" accept=".wav,.mp3,.m4a,.aac,.ogg,.oga,.opus,.flac,.wma,.amr,.mp4,.mkv,.mov,.webm,.avi,.m4v">
    </div>
    <div id="fileinfo" class="file hide"></div>

    <div class="opts">
      <div class="opt">
        <label for="lang">Язык</label>
        <select id="lang">
          <option value="ru" selected>Русский</option>
          <option value="en">English</option>
          <option value="uk">Українська</option>
          <option value="de">Deutsch</option>
          <option value="fr">Français</option>
          <option value="es">Español</option>
          <option value="it">Italiano</option>
          <option value="pl">Polski</option>
          <option value="tr">Türkçe</option>
          <option value="kk">Қазақша</option>
        </select>
      </div>
      <div class="opt">
        <label for="speakers">Говорящих</label>
        <select id="speakers">
          <option value="">Авто</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
          <option value="5">5</option>
        </select>
      </div>
      <div class="chk">
        <input id="align" type="checkbox" checked>
        <label for="align" style="margin:0">Пословное выравнивание</label>
      </div>
      <button id="go" class="btn">Анализировать</button>
    </div>
    <div class="muted" style="margin-top:10px">Выравнивание доступно для русского и английского; для остальных языков оно будет пропущено.</div>
  </div>

  <div id="progcard" class="card hide">
    <div class="row">
      <div><span id="spinner" class="spinner hide"></span><b id="stage">В очереди</b>
        <span id="pct" class="muted" style="margin-left:8px"></span></div>
      <div class="muted" id="elapsed"></div>
    </div>
    <div class="bar"><i id="bar"></i></div>
    <div class="stages" id="chips"></div>
    <div class="metrics" id="metrics"></div>
    <details style="margin-top:12px">
      <summary class="muted" style="cursor:pointer">Лог обработки</summary>
      <pre class="log" id="log"></pre>
    </details>
    <div id="err" class="alert err hide"></div>
  </div>

  <div id="result" class="card hide">
    <div class="row">
      <div><b id="rname"></b> <span class="muted" id="rmeta"></span></div>
    </div>
    <div class="tabs">
      <button class="tab active" data-tab="report">Отчёт</button>
      <button class="tab" data-tab="dialog">Диалог</button>
      <button class="tab" data-tab="files">Файлы</button>
    </div>
    <div id="tab-report" class="report"></div>
    <pre id="tab-dialog" class="dialog hide"></pre>
    <div id="tab-files" class="files hide"></div>
  </div>

  <div class="card">
    <div style="margin-bottom:12px"><b>История</b> <span class="muted">— последние обработки</span></div>
    <div id="history" class="history"><div class="muted">Пока пусто</div></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const stageOrder = ['transcribe', 'align', 'diarize', 'analyze'];
let current = null, timer = null, busy = false;

fetch('/api/health').then(r => r.json()).then(h => {
  if (!h.ollama) {
    $('#warn').classList.remove('hide');
    $('#warn').textContent = 'Ollama не отвечает — анализ будет недоступен. Запустите Ollama (ollama app.exe) и обновите страницу.';
  }
}).catch(() => {});

const drop = $('#drop'), fileInput = $('#file');
drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('keydown', ev => {
  if (ev.key === 'Enter' || ev.key === ' ') {
    ev.preventDefault();
    fileInput.click();
  }
});
['dragover', 'dragenter'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.add('over');
}));
['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', ev => {
  if (ev.dataTransfer.files.length) {
    fileInput.files = ev.dataTransfer.files;
    showFile();
  }
});
fileInput.addEventListener('change', showFile);

function showFile() {
  const f = fileInput.files[0];
  if (!f) return;
  $('#fileinfo').classList.remove('hide');
  $('#fileinfo').textContent = f.name + '  ·  ' + (f.size / 1048576).toFixed(1) + ' МБ';
}

function setBusy(v) {
  busy = v;
  $('#go').disabled = v;
  $('#spinner').classList.toggle('hide', !v);
}

$('#go').addEventListener('click', () => {
  const f = fileInput.files[0];
  if (!f) { alert('Сначала выберите аудиофайл'); return; }
  if (f.size > 2147483648) { alert('Максимальный размер — 2 ГБ'); return; }

  const fd = new FormData();
  fd.append('file', f);
  fd.append('lang', $('#lang').value);
  fd.append('speakers', $('#speakers').value);
  fd.append('align', $('#align').checked ? '1' : '0');

  setBusy(true);
  $('#result').classList.add('hide');
  $('#progcard').classList.remove('hide');
  $('#err').classList.add('hide');
  $('#log').textContent = '';
  $('#chips').innerHTML = '';
  $('#metrics').innerHTML = '';
  $('#stage').textContent = 'Загрузка файла…';
  $('#pct').textContent = '';
  $('#bar').style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const p = Math.round(e.loaded / e.total * 100);
      $('#bar').style.width = p + '%';
      $('#pct').textContent = p + '%';
    }
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status !== 200) {
      showError(data.error || ('Ошибка загрузки (HTTP ' + xhr.status + ')'));
      setBusy(false);
      return;
    }
    current = data.id;
    $('#stage').textContent = 'В очереди';
    poll(current);
  };
  xhr.onerror = () => { showError('Не удалось отправить файл на сервер'); setBusy(false); };
  xhr.send(fd);
});

function poll(id) {
  clearTimeout(timer);
  fetch('/api/jobs/' + id).then(r => r.ok ? r.json() : Promise.reject(r)).then(st => {
    if (st.id !== current) return;
    render(st);
    if (st.state === 'done' || st.state === 'error') {
      setBusy(false);
      loadHistory();
    } else {
      timer = setTimeout(() => poll(id), 1000);
    }
  }).catch(() => { timer = setTimeout(() => poll(id), 2000); });
}

function render(st) {
  $('#progcard').classList.remove('hide');
  $('#stage').textContent = st.state === 'error' ? 'Ошибка' : (st.stage_label || st.stage);
  $('#pct').textContent = st.state === 'running' ? st.progress + '%' : '';
  $('#bar').style.width = st.progress + '%';
  $('#elapsed').textContent = st.elapsed ? Math.round(st.elapsed) + ' с' : '';

  const idx = stageOrder.indexOf(st.stage);
  $('#chips').innerHTML = stageOrder.map((k, i) => {
    const label = ['Распознавание', 'Выравнивание', 'Диаризация', 'Анализ ИИ'][i];
    let cls = 'chip';
    if (st.state === 'done' || (idx >= 0 && i < idx)) cls += ' done';
    if (idx === i && st.state === 'running') cls += ' active';
    return '<span class="' + cls + '">' + label + '</span>';
  }).join('');

  const m = st.metrics || {};
  const bits = [];
  if (m.duration) bits.push('длительность <b>' + Math.round(m.duration) + ' с</b>');
  if (m.speakers) bits.push('говорящих <b>' + m.speakers + '</b>');
  if (m.segments) bits.push('сегментов <b>' + m.segments + '</b>');
  if (m.asr_sec) bits.push('ASR <b>' + m.asr_sec.toFixed(1) + ' с</b>');
  if (m.align_sec) bits.push('выравнивание <b>' + m.align_sec.toFixed(1) + ' с</b>');
  if (m.align_skipped) bits.push('выравнивание <b>пропущено</b>');
  if (m.diar_sec) bits.push('диаризация <b>' + m.diar_sec.toFixed(1) + ' с</b>');
  if (m.analyze_sec) bits.push('анализ <b>' + m.analyze_sec.toFixed(1) + ' с</b>');
  $('#metrics').innerHTML = bits.join('');

  if (st.log && st.log.length) {
    $('#log').textContent = st.log.slice(-60).join('\n');
    $('#log').scrollTop = $('#log').scrollHeight;
  }

  if (st.state === 'error') {
    $('#err').classList.remove('hide');
    $('#err').textContent = st.error || 'Обработка не удалась';
  }

  if (st.state === 'done') {
    $('#result').classList.remove('hide');
    $('#rname').textContent = st.name;
    $('#rmeta').textContent = ' · ' + (st.metrics && st.metrics.duration ? Math.round(st.metrics.duration) + ' с' : '');
    $('#tab-report').innerHTML = st.report_html || '<p>Отчёт пуст</p>';
    $('#tab-dialog').textContent = st.dialogue || 'Нет данных';
    $('#tab-files').innerHTML = (st.files || []).map(f =>
      '<a href="/api/jobs/' + encodeURIComponent(st.id) + '/file/' + encodeURIComponent(f.name) + '">' +
      '<span style="color:#e6edf7">' + f.label + '</span><span>' + f.name + ' · ' + f.size + '</span></a>'
    ).join('') || '<div class="muted">Файлы не найдены</div>';
    $('#result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function showError(msg) {
  $('#progcard').classList.remove('hide');
  $('#stage').textContent = 'Ошибка';
  $('#err').classList.remove('hide');
  $('#err').textContent = msg;
}

document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  ['report', 'dialog', 'files'].forEach(t => {
    $('#tab-' + t).classList.toggle('hide', t !== btn.dataset.tab);
  });
}));

function loadHistory() {
  fetch('/api/jobs').then(r => r.json()).then(d => {
    const box = $('#history');
    if (!d.jobs.length) { box.innerHTML = '<div class="muted">Пока пусто</div>'; return; }
    box.innerHTML = d.jobs.map(j => {
      const when = new Date(j.created * 1000).toLocaleString('ru-RU');
      const state = j.state === 'done' ? 'готово' : j.state === 'error' ? 'ошибка' : 'в работе';
      return '<button data-id="' + j.id + '"><b>' + escapeHtml(j.name) + '</b> ' +
             '<small>· ' + when + ' · ' + state + '</small></button>';
    }).join('');
    box.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
      current = b.dataset.id;
      fetch('/api/jobs/' + current).then(r => r.json()).then(st => {
        render(st);
        if (st.state !== 'done' && st.state !== 'error') poll(current);
      });
    }));
  }).catch(() => {});
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
loadHistory();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Local web UI for the offline call analyzer")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--open", action="store_true", help="open the browser on start")
    ap.add_argument("--token", default=os.environ.get("CALL_ANALYZER_TOKEN") or None,
                    help="require ?token=... for every request (LAN safety)")
    args = ap.parse_args()

    cc_paths.ensure_dirs()
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prune_jobs()

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    httpd = None
    port = args.port
    Server.token = args.token
    for candidate in range(port, port + 10):
        try:
            httpd = Server((args.host, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print("Не удалось занять порт %d-%d" % (args.port, args.port + 9))
        return 1

    url = "http://%s:%d/" % ("127.0.0.1" if args.host in ("0.0.0.0", "") else args.host, port)
    ok, ver = ollama_status()
    print("=" * 60)
    print("  Анализ звонков — локальный веб-интерфейс")
    print("  адрес:      %s" % url)
    if args.host in ("0.0.0.0", ""):
        for ip in lan_ips():
            print("  в сети:     http://%s:%d/" % (ip, port))
        if args.token:
            print("  доступ:     по токену  ?token=%s" % args.token)
        else:
            print("  внимание:   доступ без пароля — только для доверенной сети")
    print("  данные:     %s" % ROOT)
    print("  ollama:     %s" % ("OK " + ver if ok else "НЕ ОТВЕЧАЕТ — запустите Ollama"))
    print("  модели:     %s" % OLLAMA_MODELS)
    print("  остановка:  Ctrl+C")
    print("=" * 60, flush=True)
    log_line("webapp started at %s (ollama %s)" % (url, "ok" if ok else "down"))

    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
