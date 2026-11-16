"""Single place that resolves every path and endpoint used by the pipeline.

The same code runs on Windows, Linux and inside Docker. Point it at a data
directory with ``CALL_ANALYZER_HOME`` (default ``C:\\cc`` on Windows,
``~/call-analyzer`` elsewhere); everything else is derived from it.

Environment variables
---------------------
CALL_ANALYZER_HOME    data root: models, web jobs, logs, tmp
CALL_ANALYZER_MODEL   Ollama model name (default ``qwen2.5:7b``)
FFMPEG                path to ``ffmpeg`` binary or its directory
OLLAMA_HOST           Ollama endpoint (default ``http://127.0.0.1:11434``)
OLLAMA_MODELS         Ollama model store (default ``<home>/models/ollama``)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

IS_WINDOWS = os.name == "nt"
DEFAULT_HOME = Path(r"C:\cc") if IS_WINDOWS else Path.home() / "call-analyzer"

HOME = Path(os.environ.get("CALL_ANALYZER_HOME") or DEFAULT_HOME).expanduser()

MODELS = HOME / "models"
WHISPER_DIR = MODELS / "whisper"
ALIGN_DIR = MODELS / "align"          # Russian wav2vec2 (default alignment)
ALIGN_EN_DIR = MODELS / "align_en"    # English torchaudio bundle
PYANNOTE_DIR = MODELS / "pyannote"
PYANNOTE_CONFIG = PYANNOTE_DIR / "config.yaml"
TMP = HOME / "tmp"
AUDIO = HOME / "audio"
LOGS = HOME / "logs"
WEB = HOME / "web"
JOBS = WEB / "jobs"
META = WEB / "meta"

DEFAULT_MODEL = os.environ.get("CALL_ANALYZER_MODEL") or "qwen2.5:7b"


def find_ffmpeg() -> str:
    """Locate ffmpeg: explicit env var, PATH, then common install dirs."""
    explicit = os.environ.get("FFMPEG") or os.environ.get("FFMPEG_BIN")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            p = p / ("ffmpeg.exe" if IS_WINDOWS else "ffmpeg")
        if p.exists():
            return str(p)
        raise SystemExit(f"FFMPEG points to a missing file: {p}")

    exe = shutil.which("ffmpeg")
    if exe:
        return exe

    candidates = [
        Path(r"C:\tools\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        HOME / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    raise SystemExit(
        "ffmpeg not found. Install it and either add it to PATH or set "
        "FFMPEG=C:\\path\\to\\ffmpeg.exe"
    )


def ollama_url() -> str:
    """Ollama endpoint without a trailing slash."""
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not host:
        return "http://127.0.0.1:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.rstrip("/")


def ollama_models() -> Path:
    explicit = os.environ.get("OLLAMA_MODELS")
    return Path(explicit).expanduser() if explicit else MODELS / "ollama"


def configure_offline_env() -> None:
    """Make every model library stay on disk (no network at run time)."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HOME", str(MODELS / "hf"))
    os.environ.setdefault("TORCH_HOME", str(MODELS / "torch"))
    os.environ.setdefault("NLTK_DATA", str(MODELS / "nltk"))


def ensure_dirs() -> None:
    for d in (HOME, MODELS, TMP, AUDIO, LOGS, JOBS, META):
        d.mkdir(parents=True, exist_ok=True)


def whisper_snapshot() -> Path:
    """Return the faster-whisper snapshot directory that contains model.bin."""
    base = WHISPER_DIR / "models--Systran--faster-whisper-large-v3" / "snapshots"
    for snap in sorted(p for p in base.glob("*") if (p / "model.bin").exists()):
        return snap
    raise SystemExit(f"whisper model not found in {base} - run install/stage2_models.py")
