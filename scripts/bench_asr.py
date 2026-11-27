#!/usr/bin/env python3
"""Measure speech-recognition speed and accuracy on THIS machine, through OpenClipper's own engine.

    cd backend && uv run --extra asr python ../scripts/bench_asr.py --models tiny base small

Audio: ~65 s of speech made with espeak-ng from known sentences (so word error rate is exact). Real human speech is
harder than synthetic speech: treat the WER column as a smoke test of the pipeline, not as an accuracy claim, and use
--audio with your own 16 kHz mono WAV (no WER is computed for it) to measure speed on your content.
Numbers are only worth quoting together with the CPU line this script prints.
"""

from __future__ import annotations

import argparse
import base64
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.adapters.asr.faster_whisper import FasterWhisperEngine
from app.config import Settings

SENTENCES = [
    "The quick brown fox jumps over the lazy dog near the river bank.",
    "Artificial intelligence is changing how people edit video and make short clips.",
    "Please remember to subscribe, and thank you for watching this episode.",
    "Today we are going to talk about how small teams can ship software faster without burning out.",
    "The first step is to write down what you are building and why anyone would care.",
    "Then you cut the plan in half, and you ship the smaller half before the end of the week.",
    "Most of the best ideas we found came from reading support emails instead of dashboards.",
    "If a feature takes longer than two weeks to build, it is probably several features pretending to be one.",
]


def norm(t: str) -> list[str]:
    return [w for w in re.sub(r"[^\w' ]+", " ", t.lower()).split() if w]


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def make_audio(work: Path) -> tuple[Path, str]:
    if not shutil.which("espeak-ng"):
        sys.exit("espeak-ng is needed to generate the benchmark audio (or pass --audio)")
    parts = []
    for i, s in enumerate(SENTENCES * 2):
        p = work / f"s{i}.wav"
        subprocess.run(["espeak-ng", "-s", "150", "-w", str(p), s], check=True)
        parts.append(p)
    (work / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    out = work / "speech.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)], check=True)  # fmt: skip
    return out, " ".join(SENTENCES * 2)


def cpu_name() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["tiny", "base", "small"])
    ap.add_argument("--audio", type=Path, help="16 kHz mono 16-bit WAV; skips WER")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--cache", default=str(Path.home() / ".cache/openclipper-bench-models"))
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    # The engine only reads the WHISPER_* settings; the rest just has to be well-formed for Settings to load.
    for k, v in {"DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x", "REDIS_URL": "redis://localhost:6379/0",
                 "APP_ENCRYPTION_KEY": base64.b64encode(b"k" * 32).decode(), "APP_JWT_SECRET": "j" * 48,
                 "APP_ENV": "test"}.items():  # fmt: skip
        os.environ.setdefault(k, v)
    s = Settings(WHISPER_CACHE_DIR=a.cache, WHISPER_DEVICE=a.device)  # type: ignore[call-arg]
    work = Path(tempfile.mkdtemp(prefix="oc-bench-"))
    audio, ref = (a.audio, "") if a.audio else make_audio(work)
    engine = FasterWhisperEngine(s)
    dev, comp = engine.device_and_compute()
    print(f"CPU: {cpu_name()} | threads available: {os.cpu_count()} | device={dev} compute={comp}")
    print("| model | load s (cold) | audio s | transcribe s (median of runs) | real-time factor | WER | words |")
    print("|---|---|---|---|---|---|---|")
    for m in a.models:
        engine.ensure_model(m, lambda *_: None)
        t0 = time.monotonic()
        engine._load(m)  # noqa: SLF001  measured separately from transcription
        load = time.monotonic() - t0
        times, data = [], None
        for _ in range(a.runs):
            t0 = time.monotonic()
            data = engine.transcribe(
                audio, model=m, language=None, vad=True, progress=lambda *_: None, cancelled=lambda: False
            )
            times.append(time.monotonic() - t0)
        assert data is not None
        text = " ".join(seg.text for seg in data.segments)
        med = statistics.median(times)
        e = f"{wer(ref, text):.3f}" if ref else "n/a"
        words = sum(len(seg.words) for seg in data.segments)
        print(f"| {m} | {load:.1f} | {data.duration_s:.1f} | {med:.1f} | {med / data.duration_s:.3f} | {e} | {words} |")


if __name__ == "__main__":
    main()
