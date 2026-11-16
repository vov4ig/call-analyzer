"""Call-center transcription: ASR + alignment + diarization, fully offline.

Usage:
    python run_call.py call.mp3 --lang ru
    python run_call.py call.wav --lang ru --num-speakers 2

Writes <audio>.json / <audio>.txt / <audio>.srt next to the audio file
(or into --out dir). Models are read from CALL_ANALYZER_HOME (C:\\cc by
default); see cc_paths.py for all overrides.
"""
import argparse
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cc_paths

# offline by default: everything must already be on disk
cc_paths.configure_offline_env()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_wav(src: Path, tmp: Path) -> Path:
    """Convert any input to 16 kHz mono wav (whisperX requirement)."""
    if src.suffix.lower() == ".wav":
        return src
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / (src.stem + "_16k.wav")
    log(f"converting {src.name} -> 16 kHz mono wav")
    subprocess.run([cc_paths.find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src), "-ac", "1", "-ar", "16000", str(out)], check=True)
    return out


def fmt_ts(sec: float, sep=",") -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--lang", default="ru", help="ASR language (ru by default)")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute", default="float16")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--min-speakers", type=int, default=None)
    ap.add_argument("--max-speakers", type=int, default=None)
    ap.add_argument("--num-speakers", type=int, default=None)
    ap.add_argument("--no-align", action="store_true", help="skip word alignment (faster, no word timings)")
    ap.add_argument("--out", default=None, help="output directory")
    args = ap.parse_args()

    src = Path(args.audio).expanduser().resolve()
    if not src.exists():
        raise SystemExit(f"no such file: {src}")
    out_dir = Path(args.out).resolve() if args.out else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / src.stem

    import torch
    import whisperx
    from whisperx.offline_diarize import OfflineDiarizationPipeline

    # whisperx calls ffmpeg by name -> make sure it is on PATH for this process
    os.environ["PATH"] = str(Path(cc_paths.find_ffmpeg()).parent) + os.pathsep + os.environ.get("PATH", "")

    log(f"torch {torch.__version__} | cuda {torch.cuda.is_available()}")
    wav = ensure_wav(src, cc_paths.TMP)

    t0 = time.time()
    model_dir = str(cc_paths.whisper_snapshot())
    log(f"ASR large-v3 ({args.lang}) on {args.device} ...")
    asr = whisperx.load_model(model_dir, args.device, compute_type=args.compute,
                              language=args.lang)
    audio = whisperx.load_audio(str(wav))
    result = asr.transcribe(audio, batch_size=args.batch_size)
    log(f"ASR done in {time.time()-t0:.1f}s: {len(result['segments'])} segments")
    del asr
    gc.collect()
    torch.cuda.empty_cache()

    if not args.no_align:
        try:
            t0 = time.time()
            align_dir = cc_paths.ALIGN_EN_DIR if args.lang == "en" else cc_paths.ALIGN_DIR
            model_a, metadata = whisperx.load_align_model(
                language_code=args.lang, device=args.device,
                model_dir=str(align_dir), local_files_only=True)
            result = whisperx.align(result["segments"], model_a, metadata, audio,
                                    args.device, return_char_alignments=False)
            log(f"alignment done in {time.time()-t0:.1f}s")
            del model_a
            gc.collect()
            torch.cuda.empty_cache()
        except Exception as exc:  # alignment is optional, never fatal
            log(f"WARNING: alignment skipped ({exc})")

    t0 = time.time()
    log("diarization ...")
    diar = OfflineDiarizationPipeline(
        config_path=str(cc_paths.PYANNOTE_CONFIG), device=args.device)
    turns = diar(str(wav), num_speakers=args.num_speakers,
                 min_speakers=args.min_speakers, max_speakers=args.max_speakers)
    speakers = sorted(turns["speaker"].unique().tolist())
    log(f"diarization done in {time.time()-t0:.1f}s: {len(turns)} turns, {len(speakers)} speakers")

    result = whisperx.assign_word_speakers(turns, result)

    segments = []
    for s in result["segments"]:
        seg = {
            "start": round(float(s["start"]), 3),
            "end": round(float(s["end"]), 3),
            "speaker": s.get("speaker") or "SPEAKER_?",
            "text": s["text"].strip(),
        }
        if s.get("words"):
            seg["words"] = [{"w": w["word"], "start": round(float(w["start"]), 3),
                             "end": round(float(w["end"]), 3)} for w in s["words"]
                            if "start" in w and "end" in w]
        segments.append(seg)

    payload = {
        "source": str(src),
        "language": args.lang,
        "duration": round(len(audio) / whisperx.audio.SAMPLE_RATE, 3),
        "speakers": speakers,
        "segments": segments,
    }
    (base.with_suffix(".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # merged dialogue, one line per speaker turn
    lines = []
    prev = None
    for seg in segments:
        if seg["speaker"] == prev:
            lines[-1][2].append(seg["text"])
        else:
            lines.append([seg["start"], seg["speaker"], [seg["text"]]])
            prev = seg["speaker"]
    txt = "\n".join(f"[{fmt_ts(a, '.')[:8]}] {spk}: {' '.join(txts)}"
                    for a, spk, txts in lines)
    base.with_suffix(".txt").write_text(txt + "\n", encoding="utf-8")

    srt = []
    for i, seg in enumerate(segments, 1):
        srt.append(f"{i}\n{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}\n"
                   f"{seg['speaker']}: {seg['text']}\n")
    base.with_suffix(".srt").write_text("\n".join(srt), encoding="utf-8")

    log(f"written: {base.with_suffix('.json').name}, {base.with_suffix('.txt').name}, "
        f"{base.with_suffix('.srt').name}")
    print("\n" + txt + "\n")
    log("RUN_CALL_DONE")


if __name__ == "__main__":
    sys.exit(main())
