"""Stage 2: download every model into a plain on-disk layout.

Models are stored in the same simplified HF-cache layout that whisperx's
``local_files_only=True`` expects, so no ``huggingface_hub`` metadata is needed
at run time. Downloads go through plain HTTPS with resume support; no xet /
hf_transfer, which stalls on slow links.

    python stage2_models.py                 # official sources, token from HF_TOKEN
    python stage2_models.py --token hf_...  # official sources, explicit token
    python stage2_models.py --mirror        # public re-uploads of the gated models

Only the two pyannote models are gated. Everything else is public.
See THIRD_PARTY.md before using --mirror.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

log_file = None


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dst, size=None, token=None, retries=5):
    """Download with resume, size check and a token for gated endpoints."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists() and size is not None and dst.stat().st_size == size:
        log(f"  SKIP  {dst.name} ({size / 1e6:.1f} MB already)")
        return dst

    last_error = None
    for attempt in range(1, retries + 1):
        have = dst.stat().st_size if dst.exists() else 0
        if size is not None and have > size:
            dst.unlink()
            have = 0
        headers = {"User-Agent": "call-analyzer-installer"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if have:
            headers["Range"] = f"bytes={have}-"
            log(f"  RESUME {dst.name} from {have / 1e6:.1f} MB")
        else:
            log(f"  GET   {url}")

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                total = resp.headers.get("Content-Length")
                total = have + int(total) if total else None
                done = have
                mark = time.time()
                with open(dst, mode) as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if time.time() - mark > 10:
                            mark = time.time()
                            pct = f"{100 * done / total:.0f}%" if total else "?"
                            log(f"        {dst.name}: {done / 1e6:.0f} MB ({pct})")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 403):
                raise SystemExit(
                    f"HTTP {exc.code} for {url}\n"
                    "This model is gated. Accept its terms on huggingface.co, then\n"
                    "create a read token (Settings -> Access Tokens) and pass it with\n"
                    "  python stage2_models.py --token hf_xxx\n"
                    "or set HF_TOKEN. See THIRD_PARTY.md."
                ) from exc
            log(f"  WARN  HTTP {exc.code}, retry {attempt}/{retries}")
            time.sleep(3 * attempt)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            log(f"  WARN  {type(exc).__name__}: {exc}, retry {attempt}/{retries}")
            time.sleep(3 * attempt)
            continue

        got = dst.stat().st_size
        if size is not None and got != size:
            log(f"  WARN  size {got} != {size}, retry {attempt}/{retries}")
            time.sleep(2)
            continue
        log(f"  OK    {dst.name} ({got / 1e6:.1f} MB)")
        return dst

    raise SystemExit(f"download failed after {retries} attempts: {url} ({last_error})")


def hf_snapshot_dir(repo_id, revision, cache_root):
    """Create <cache>/models--org--name/snapshots/<rev> + refs/main and return it."""
    org, name = repo_id.split("/")
    base = Path(cache_root) / f"models--{org}--{name}"
    snap = base / "snapshots" / revision
    snap.mkdir(parents=True, exist_ok=True)
    (base / "refs").mkdir(parents=True, exist_ok=True)
    (base / "refs" / "main").write_text(revision, encoding="utf-8")
    (Path(cache_root) / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n", encoding="utf-8")
    return snap


def hf_files(repo_id, revision, files, cache_root, token=None):
    snap = hf_snapshot_dir(repo_id, revision, cache_root)
    for name, size in files:
        download(f"https://huggingface.co/{repo_id}/resolve/{revision}/{name}",
                 snap / name, size, token=token)
    return snap


WHISPER = "Systran/faster-whisper-large-v3"
WHISPER_REV = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
WHISPER_FILES = [
    ("config.json", 2394),
    ("model.bin", 3087284237),
    ("preprocessor_config.json", 340),
    ("tokenizer.json", 2480617),
    ("vocabulary.json", 1068114),
]

RU_ALIGN = "jonatasgrosman/wav2vec2-large-xlsr-53-russian"
RU_REV = "2329100508896c6d9b157019803ab5601e6f3406"
RU_FILES = [
    ("config.json", 1782),
    ("preprocessor_config.json", 262),
    ("pytorch_model.bin", 1262093719),
    ("special_tokens_map.json", 85),
    ("vocab.json", 387),
]

EN_ALIGN_URL = ("https://download.pytorch.org/torchaudio/models/"
                "wav2vec2_fairseq_base_ls960_asr_ls960.pth")
EN_ALIGN_SIZE = 377664473

# Official (gated) sources for the pyannote pipeline parts.
SEG_OFFICIAL = ("pyannote/segmentation-3.0", "pytorch_model.bin", 5905440)
EMB_OFFICIAL = ("pyannote/wespeaker-voxceleb-resnet34-LM", "pytorch_model.bin", 26645418)
# Public re-uploads of the same bytes (see THIRD_PARTY.md).
SEG_MIRROR = ("tensorlake/segmentation-3.0", "pytorch_model.bin", 5905440)
EMB_MIRROR = ("tensorlake/wespeaker-voxceleb-resnet34-LM", "pytorch_model.bin", 26645418)

SEG_SHA = "da85c29829d4002daedd676e012936488234d9255e65e86dfab9bec6b1729298"
EMB_SHA = "366edf44f4c80889a3eb7a9d7bdf02c4aede3127f7dd15e274dcdb826b143c56"

PYANNOTE_CONFIG = """version: 3.1.0

pipeline:
  name: pyannote.audio.pipelines.SpeakerDiarization
  params:
    clustering: AgglomerativeClustering
    embedding: {emb}
    embedding_batch_size: 32
    embedding_exclude_overlap: true
    segmentation: {seg}
    segmentation_batch_size: 32

params:
  clustering:
    method: centroid
    min_cluster_size: 12
    threshold: 0.7045654963945799
  segmentation:
    min_duration_off: 0.0
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None,
                    help="data root (default: CALL_ANALYZER_HOME or C:\\cc)")
    ap.add_argument("--token", default=os.environ.get("HF_TOKEN"),
                    help="Hugging Face read token for the gated pyannote models")
    ap.add_argument("--mirror", action="store_true",
                    help="take pyannote weights from public re-uploads (no token)")
    ap.add_argument("--only", default="all",
                    choices=["all", "whisper", "align", "pyannote", "nltk"])
    ap.add_argument("--skip-nltk", action="store_true")
    args = ap.parse_args()

    home = Path(args.home or os.environ.get("CALL_ANALYZER_HOME")
                or (r"C:\cc" if os.name == "nt" else Path.home() / "call-analyzer"))
    models = home / "models"
    models.mkdir(parents=True, exist_ok=True)

    global log_file
    (home / "logs").mkdir(parents=True, exist_ok=True)
    log_file = home / "logs" / "stage2.log"
    log("=== STAGE 2: models ===")
    log(f"home: {home}")

    token = None if args.mirror else args.token

    # Fail early: nobody wants to download 4 GB and then hit the gate.
    if args.only in ("all", "pyannote") and not args.mirror and not token:
        raise SystemExit(
            "The pyannote models are gated on Hugging Face.\n"
            "1. Log in and accept the terms on\n"
            "   https://huggingface.co/pyannote/segmentation-3.0\n"
            "   https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM\n"
            "2. Create a read token: https://huggingface.co/settings/tokens\n"
            "3. Re-run: python stage2_models.py --token hf_xxx\n"
            "Fallback (not recommended, see THIRD_PARTY.md): --mirror"
        )

    if args.only in ("all", "whisper"):
        log("[whisper] faster-whisper large-v3 (~3.0 GB)")
        snap = hf_files(WHISPER, WHISPER_REV, WHISPER_FILES, models / "whisper")
        log(f"  snapshot -> {snap}")

    if args.only in ("all", "align"):
        log("[align] ru: wav2vec2-large-xlsr-53-russian (~1.26 GB)")
        snap = hf_files(RU_ALIGN, RU_REV, RU_FILES, models / "align")
        log(f"  snapshot -> {snap}")
        log("[align] en: torchaudio WAV2VEC2_ASR_BASE_960H")
        download(EN_ALIGN_URL, models / "align_en" /
                 "wav2vec2_fairseq_base_ls960_asr_ls960.pth", EN_ALIGN_SIZE)

    if args.only in ("all", "pyannote"):
        dst = models / "pyannote"
        dst.mkdir(parents=True, exist_ok=True)
        seg_repo, seg_name, seg_size = SEG_MIRROR if args.mirror else SEG_OFFICIAL
        emb_repo, emb_name, emb_size = EMB_MIRROR if args.mirror else EMB_OFFICIAL
        if args.mirror:
            log("[pyannote] MIRROR mode: public re-uploads, upstream terms NOT accepted")
        # --only pyannote --token ... => we land here before whisper; fine.
        download(f"https://huggingface.co/{seg_repo}/resolve/main/{seg_name}",
                 dst / "seg.bin", seg_size, token=token)
        download(f"https://huggingface.co/{emb_repo}/resolve/main/{emb_name}",
                 dst / "emb.bin", emb_size, token=token)

        for name, want in (("seg.bin", SEG_SHA), ("emb.bin", EMB_SHA)):
            got = sha256(dst / name)
            if got != want:
                raise SystemExit(f"sha256 mismatch {name}: {got} != {want}")
            log(f"  SHA256 OK {name}")

        (dst / "config.yaml").write_text(
            PYANNOTE_CONFIG.format(seg=(dst / "seg.bin").as_posix(),
                                   emb=(dst / "emb.bin").as_posix()),
            encoding="utf-8")
        log(f"  config -> {dst / 'config.yaml'}")

    if args.only in ("all", "nltk") and not args.skip_nltk:
        log("[nltk] punkt + punkt_tab")
        import nltk
        for pkg in ("punkt", "punkt_tab"):
            ok = nltk.download(pkg, download_dir=str(models / "nltk"), quiet=True)
            log(f"  {pkg}: {ok}")

    log("STAGE2_MODELS_DONE")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
