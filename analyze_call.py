"""Analyze a transcribed call with a local LLM (Ollama / qwen2.5:7b).

Usage:
    python analyze_call.py call.json
    python analyze_call.py call.json --model qwen2.5:7b --out report.md

Ollama endpoint: OLLAMA_HOST or --ollama (default http://127.0.0.1:11434).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import urllib.request
import urllib.error

import cc_paths

OLLAMA = cc_paths.ollama_url()

SYSTEM = (
    "Ты аналитик качества работы контакт-центра. Тебе дают расшифровку телефонного "
    "разговора с разделением по говорящим (SPEAKER_00 — обычно оператор, SPEAKER_01 — клиент; "
    "если непонятно, определи по смыслу). Отвечай по-русски, строго по структуре, без воды."
)

PROMPT = """Проанализируй разговор и выдай отчёт в таком формате:

## Краткое содержание
(2-4 предложения)

## Тема обращения
(одна строка)

## Кто говорил
(кто оператор, кто клиент — по репликам)

## Проблема клиента
(пункты)

## Как решили
(пункты, или «не решено»)

## Эмоции и тон
(настроение клиента и оператора, были ли конфликтные моменты)

## Соблюдение скрипта
(приветствие, выяснение проблемы, уточняющие вопросы, решение, завершение — что было, чего не было)

## Оценка качества
(число от 1 до 10 и одно предложение обоснования)

## Рекомендации оператору
(2-3 пункта)

Расшифровка:
---
{transcript}
---
"""


def build_transcript(data, limit=12000):
    lines, prev, buf, start = [], None, [], 0.0
    for seg in data["segments"]:
        if seg["speaker"] != prev:
            if prev is not None:
                lines.append((start, prev, " ".join(buf)))
            prev, buf, start = seg["speaker"], [seg["text"]], seg["start"]
        else:
            buf.append(seg["text"])
    if prev is not None:
        lines.append((start, prev, " ".join(buf)))

    out = []
    for start, spk, text in lines:
        m, s = divmod(int(start), 60)
        line = f"[{m:02d}:{s:02d}] {spk}: {text}"
        out.append(line)
    text = "\n".join(out)
    return text[:limit]


def ask(prompt, model, timeout=900, endpoint=None):
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "system": SYSTEM,
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(f"{endpoint or OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="*.json produced by run_call.py")
    ap.add_argument("--model", default=cc_paths.DEFAULT_MODEL)
    ap.add_argument("--ollama", default=OLLAMA, help="Ollama endpoint")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=12000)
    args = ap.parse_args()

    src = Path(args.transcript).expanduser().resolve()
    data = json.loads(src.read_text(encoding="utf-8"))
    transcript = build_transcript(data, args.limit)
    if not transcript.strip():
        raise SystemExit("empty transcript")

    print(f"transcript: {len(transcript)} chars, {len(data['segments'])} segments", flush=True)
    t0 = time.time()
    try:
        ans = ask(PROMPT.format(transcript=transcript), args.model, endpoint=args.ollama)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Ollama is not reachable at {args.ollama}: {exc}")

    report = ans.get("response", "").strip()
    out = Path(args.out) if args.out else src.with_suffix(".report.md")
    header = (f"# Анализ звонка: {src.stem}\n\n"
              f"- источник: `{data.get('source', '?')}`\n"
              f"- язык: {data.get('language', '?')}\n"
              f"- длительность: {data.get('duration', 0):.0f} с\n"
              f"- говорящих: {len(data.get('speakers', []))}\n"
              f"- модель: {args.model}\n\n---\n\n")
    out.write_text(header + report + "\n", encoding="utf-8")

    print(report)
    print(f"\n[{time.time()-t0:.1f}s] saved -> {out}", flush=True)
    print("ANALYZE_CALL_DONE", flush=True)


if __name__ == "__main__":
    sys.exit(main())
