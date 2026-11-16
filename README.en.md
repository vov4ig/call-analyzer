# call-analyzer — offline call-center analysis

[![CI](https://github.com/vov4ig/call-analyzer/actions/workflows/ci.yml/badge.svg)](../../actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Language / Язык:** **English** · [Русский](README.md)

> **RU:** Полностью локальный анализ звонков: Whisper large-v3 с диаризацией
> pyannote и отчётом по качеству от локального qwen2.5 через Ollama. Интернет
> после установки не нужен, аудио не покидает компьютер. Есть установщик для
> Windows без прав администратора, веб-интерфейс с доступом по LAN и токеном,
> а также вариант с Docker. Полная русская документация: [README.md](README.md).

Drop an audio recording into a browser and get a speaker-labeled transcript plus
a quality-control report for the agent. Everything runs on your computer: after
installation no internet is needed and no audio leaves the machine.

```
mp3/wav/m4a/ogg/video
        │
        ▼
  Whisper large-v3  ──►  wav2vec2 word alignment
        │
        ▼
  pyannote 3.3 diarization  ──►  "who said what" dialogue
        │
        ▼
  qwen2.5:7b via Ollama  ──►  9-section report + recommendations
```

## Stack

| Stage | Model | Where |
|---|---|---|
| ASR | faster-whisper large-v3 (fp16) | GPU, CTranslate2 |
| Word alignment | wav2vec2 XLSR-53 (ru), torchaudio 960H (en) | GPU |
| Diarization | pyannote.audio 3.3.2 (segmentation-3.0 + WeSpeaker) | GPU |
| Report | qwen2.5:7b-instruct | Ollama |

The report covers: summary, issue topic, who is the agent and who is the caller,
customer problem, resolution, emotions and tone, script compliance, agent
mistakes and recommendations. Four downloads are produced: `.json` (segments,
words, speakers), `.txt`, `.srt`, `.md`.

The web UI uses only the Python standard library (`http.server`) — no FastAPI,
no Node, no CDN. Jobs run in a single sequential worker: 8 GB of VRAM cannot
host two pipelines in parallel.

## Requirements

- Windows 10/11 x64, PowerShell 5.1+
- NVIDIA GPU; verified on an **RTX 5060 Laptop, 8 GB, driver 591.44**
- 6 GB VRAM minimum, ~15 GB disk, no administrator rights (everything is
  installed under `C:\tools` and `C:\cc`)

| GPU | Architecture | Flag |
|---|---|---|
| RTX 50xx | Blackwell (sm_120) | `-Torch cu128` (default) |
| RTX 40xx / 30xx | Ada / Ampere | `-Torch cu128` |
| RTX 20xx / GTX 16xx | Turing | `-Torch cu128` |
| GTX 10xx | Pascal | `-Torch cu118` |

## Install

```powershell
git clone https://github.com/vov4ig/call-analyzer.git
cd call-analyzer
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1
```

The installer: downloads Git/Miniconda/FFmpeg into `C:\tools`; creates the `wx`
conda env with pinned CUDA PyTorch and the whisperX fork; installs a
`torch.load` compatibility shim for pyannote; downloads 8.9 GB of models;
installs Ollama with qwen2.5:7b; runs a synthetic 2-speaker smoke test; and
calls `verify.ps1`. Log: `C:\cc\logs\install.log`.

Custom paths / older GPU:

```powershell
powershell ... -File install\install.ps1 -Home D:\calls -Tools D:\tools -Torch cu121
```

### Hugging Face token

The two pyannote diarization models are gated. Accept the terms on
[segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) and
[wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM),
create a read token at <https://huggingface.co/settings/tokens>, then:

```powershell
$env:HF_TOKEN = "hf_xxx"
powershell ... -File install\install.ps1
```

The token is used once during download. At run time all models are read from
disk with `HF_HUB_OFFLINE=1`. `-Mirror` pulls byte-identical files from public
re-uploads if the gated endpoint is unreachable — this does not mean the
upstream terms were accepted; see [THIRD_PARTY.md](THIRD_PARTY.md).

## Run

```bat
web.bat
```

Open <http://127.0.0.1:8080>, drop a file, choose the language (ru/en/auto) and
optionally the number of speakers. From CLI:

```bat
transcribe.bat "D:\calls\call_001.mp3" --lang ru --num-speakers 2
analyze.bat "D:\calls\call_001.json"
```

### LAN access

```bat
set CALL_ANALYZER_TOKEN=my-secret
web-lan.bat
```

The server listens on `0.0.0.0:8080` and prints its LAN addresses. Open
`http://<ip>:8080/?token=my-secret` once; the token is then kept in a cookie.
The `X-Token` header works too. Allow the port in Windows Firewall if asked.

## Docker (Linux / NVIDIA)

The native installer is the primary path; Docker is an optional extra and is
not verified on Windows. Requires the NVIDIA Container Toolkit.

```bash
git clone https://github.com/vov4ig/call-analyzer.git
cd call-analyzer
docker compose up -d --build
docker compose exec app python install/stage2_models.py --home /data
```

Two services: `app` (web UI on 8080) and `ollama` (official image). Models live
in named volumes and never enter the image. On an 8 GB GPU you may need to
limit how many LLM layers Ollama keeps in VRAM — see [docker/README.md](docker/README.md).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CALL_ANALYZER_HOME` | `C:\cc` | data root: models, jobs, logs, temp |
| `CALL_ANALYZER_PY` | autodetect | python.exe of the `wx` env (for .bat files) |
| `CALL_ANALYZER_MODEL` | `qwen2.5:7b` | Ollama model for reports |
| `CALL_ANALYZER_TOKEN` | none | enables web UI token auth |
| `FFMPEG` | autodetect | ffmpeg binary or its directory |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODELS` | `<home>\models\ollama` | Ollama model store |

## Performance

RTX 5060 Laptop 8 GB, 26-second call: ~17 s for ASR + alignment + diarization
(including ~8 s model loading), ~9 s for the LLM report. Roughly 2–4× faster
than real time; a one-hour call takes 15–30 minutes.

## Diagnostics

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File verify.ps1
```

| Symptom | Fix |
|---|---|
| `sm_120 is not supported`, no CUDA | reinstall torch: `stage1.ps1 -Torch cu128` (`cu118` for Pascal) |
| pyannote `UnpicklingError` / `weights_only` | install `shim/sitecustomize.py` into site-packages |
| `ffmpeg not found` | set `FFMPEG=C:\tools\ffmpeg\bin\ffmpeg.exe` |
| `Ollama is not reachable` | start `ollama.exe serve` with `OLLAMA_MODELS` set |
| `CUDA out of memory` | lower `--batch-size`, use shorter files, use a smaller LLM |
| Cyrillic paths fail | install under `C:\cc` / `C:\tools` |
| Speakers are mixed up | pass `--num-speakers`, use clean mono 16 kHz audio |

## Limitations

This is an assistant, not a replacement for listening: an LLM can invent
details, so always cross-check the report against the transcript. One language
per file; overlapping speech and similar voices confuse diarization. Very long
files (2 h+) should be split into 20–30 minute chunks. Audio, transcripts and
reports contain personal data and stay on your machine — storing and processing
them is your responsibility.

## License

Code: [MIT](LICENSE). Models and third-party components have their own terms:
[THIRD_PARTY.md](THIRD_PARTY.md). Русская версия: [README.md](README.md).
