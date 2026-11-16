# Docker / Linux variant

This is an **optional** path. The native Windows installer (`install/install.ps1`)
is the primary, fully tested way to run the project. Docker was authored
statically: Docker is not installed on the author's machine, so treat this as a
starting point and report issues.

## Requirements

- Linux with an NVIDIA driver
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Docker Engine + Compose plugin
- ~15 GB free disk for the image and model volumes

On Windows, use WSL2 + Docker Desktop with the `nvidia-container-toolkit` inside
the WSL distro. Installing WSL requires administrator rights.

## Start

From the repository root (the build context must be the root, not `docker/`):

```bash
export CALL_ANALYZER_TOKEN=change-me        # optional but recommended
docker compose up -d --build
```

The image contains code and Python dependencies only — **no model weights**.
Download them once into the named volume:

```bash
# gated pyannote models: accept the terms and use a token
export HF_TOKEN=hf_xxx
docker compose exec -e HF_TOKEN="$HF_TOKEN" app \
  python install/stage2_models.py --home /data

# or, if the gated endpoint is unreachable (see THIRD_PARTY.md):
docker compose exec app python install/stage2_models.py --home /data --mirror
```

Then pull the LLM into the Ollama volume:

```bash
docker compose exec ollama ollama pull qwen2.5:7b
docker compose restart app
```

Open <http://localhost:8080>. If you set `CALL_ANALYZER_TOKEN`, open
`http://localhost:8080/?token=change-me` once so the browser stores the cookie.

## Volumes

| Volume | Mounted at | Contents |
|---|---|---|
| `call-analyzer_data` | `/data` | whisper, alignment, pyannote, nltk, jobs, logs |
| `ollama_models` | `/ollama-models` | qwen2.5:7b and other Ollama models |

`docker compose down` keeps both volumes. `docker compose down -v` deletes all
models and jobs.

## GPU memory

The app and the LLM are separate containers and both may request the same GPU.
With 8 GB of VRAM, Ollama sometimes keeps too many layers resident and the ASR
stage runs out of memory. Remedies:

- use a smaller model: `docker compose exec ollama ollama pull qwen2.5:3b` and
  set `CALL_ANALYZER_MODEL=qwen2.5:3b` in the `app` service;
- restrict Ollama with `OLLAMA_GPU_OVERHEAD=1073741824` or run it with
  `CUDA_VISIBLE_DEVICES=""` (CPU inference, slower but frees VRAM);
- pin both containers to different GPUs with `device_ids` if you have more than one.

## Useful commands

```bash
docker compose logs -f app
docker compose exec app python -c "import torch, whisperx; print('cuda:', torch.cuda.is_available())"
docker compose exec app python install/stage2_test.py
docker compose stats
```
