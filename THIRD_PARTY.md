# Third-party components

This project is MIT-licensed, but it orchestrates software and model weights that
belong to other people. Nothing in this repository re-distributes model weights:
every model is downloaded by the installer from its original source.

**You are responsible for accepting the upstream terms before using the models
commercially.** This file lists what is used and under which terms, as of 2026.

## Code

| Component | Used for | License |
|---|---|---|
| [whisperX](https://github.com/m-bain/whisperX) (`JayDHulster/whisperX-offline@f5ae868`) | ASR + word alignment + diarization glue | BSD-2-Clause |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Whisper inference backend | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | Fast transformer inference | MIT |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) 3.3.2 | Speaker diarization | MIT |
| [PyTorch](https://pytorch.org) / [torchaudio](https://pytorch.org/audio) | Tensors, GPU, audio | BSD-3-Clause |
| [Ollama](https://github.com/ollama/ollama) | Local LLM server | MIT |
| [FFmpeg](https://ffmpeg.org) (BtbN builds, `-gpl` variant) | Audio decoding / conversion | GPL-3.0 (the build we install) |
| [nltk](https://www.nltk.org) | Sentence splitting | Apache-2.0 |

## Models

| Model | Source | License | Notes |
|---|---|---|---|
| `Systran/faster-whisper-large-v3` | Hugging Face | MIT | CTranslate2 conversion of OpenAI Whisper large-v3 (MIT). Public, no token. |
| `jonatasgrosman/wav2vec2-large-xlsr-53-russian` | Hugging Face | Apache-2.0 | Russian word alignment. Public, no token. |
| `WAV2VEC2_ASR_BASE_960H` | download.pytorch.org | BSD-3-Clause (torchaudio) | English word alignment. Public, no token. |
| `pyannote/segmentation-3.0` | Hugging Face | MIT | **Gated.** Requires a HF account, accepting the model card terms, and an access token. |
| `pyannote/wespeaker-voxceleb-resnet34-LM` | Hugging Face | MIT | **Gated**, same conditions as above. |
| `qwen2.5:7b` (Qwen2.5-7B-Instruct) | Ollama registry | Apache-2.0 | Report generation. Public. |

### About the gated pyannote models

`pyannote/segmentation-3.0` and the WeSpeaker embedding model are published on
Hugging Face behind an access gate: you must log in, open the model page, accept
the conditions and create a read token. The installer's default path does exactly
that and passes your token to the download request; the token is used once and is
never written to disk by this project.

The installer also has a `--mirror` mode that pulls byte-identical copies from
public re-uploads. It exists because the original author's network could not reach
the gated endpoint. **Using the mirror mode means you have not accepted the
upstream terms**, and the SHA-256 checks in the installer only prove the files are
identical, not that you are allowed to use them. Do not use `--mirror` in a
commercial or public deployment without checking the license yourself.

## Fonts, icons, screenshots

The web UI uses no external assets: no CDN, no web fonts, no icon packs. Everything
is inline SVG/CSS, so the page works on an air-gapped network.
