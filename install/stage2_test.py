"""Stage 2 smoke test: load every local model and run a real call end to end.

    python install/stage2_test.py            # audio from <home>/audio/test_mix.wav
    python install/stage2_test.py call.wav

Needs install/make_test_audio.ps1 (Windows) or any 2-speaker audio file.
Prints a transcript with speaker labels; no files are written.
"""
from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch
import whisperx
from whisperx.offline_diarize import OfflineDiarizationPipeline

import cc_paths

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
AUDIO = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else cc_paths.AUDIO / "test_mix.wav"


def head(msg):
    print("::", msg, flush=True)


head(f"torch {torch.__version__} cuda={torch.cuda.is_available()} "
     f"gpu={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}")
head(f"audio {AUDIO}")
if not AUDIO.exists():
    raise SystemExit(f"audio not found: {AUDIO} (run install/make_test_audio.ps1)")

# ---------- 1. ASR (large-v3, local only) ----------
t0 = time.time()
model_dir = str(cc_paths.whisper_snapshot())
asr = whisperx.load_model(model_dir, DEVICE, compute_type="float16" if DEVICE == "cuda" else "int8")
audio = whisperx.load_audio(str(AUDIO))
print("audio seconds:", len(audio) / 16000.0, flush=True)
res = asr.transcribe(audio, batch_size=8)
head(f"ASR ok: {len(res['segments'])} segments in {time.time() - t0:.1f}s")

# ---------- 2. alignment en (torchaudio bundle) ----------
model_a, meta = whisperx.load_align_model(language_code="en", device=DEVICE,
                                          model_dir=str(cc_paths.ALIGN_EN_DIR))
t0 = time.time()
res = whisperx.align(res["segments"], model_a, meta, audio, DEVICE, return_char_alignments=False)
head(f"ALIGN en ok in {time.time() - t0:.1f}s: {len(res['segments'])} segments")
del model_a, meta
gc.collect()
torch.cuda.empty_cache()

# ---------- 3. alignment ru model load from local cache (production path) ----------
ru_a, ru_meta = whisperx.load_align_model(language_code="ru", device=DEVICE,
                                          model_dir=str(cc_paths.ALIGN_DIR),
                                          local_files_only=True)
head("ALIGN ru model loads offline OK")
del ru_a, ru_meta
gc.collect()
torch.cuda.empty_cache()

# ---------- 4. diarization ----------
t0 = time.time()
diar = OfflineDiarizationPipeline(config_path=str(cc_paths.PYANNOTE_CONFIG), device=DEVICE)
dial = diar(str(AUDIO))
head(f"DIAR ok: {len(dial)} turns, {time.time() - t0:.1f}s, "
     f"speakers={sorted(dial['speaker'].unique().tolist())}")

# ---------- 5. speaker assignment ----------
res = whisperx.assign_word_speakers(dial, res)
print("--- SEGMENTS ---", flush=True)
for s in res["segments"]:
    print(f"{s['start']:7.2f}-{s['end']:7.2f} [{s.get('speaker', '?')}] {s['text']}", flush=True)

print("STAGE2_TEST_DONE", flush=True)
