"""Run ffmpeg safely: argument lists only (never a shell), protocol whitelist, timeout, cancel, progress."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path


class FfmpegError(Exception):
    def __init__(self, code: str, message: str, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stderr_tail = stderr_tail


class Cancelled(Exception):
    pass


def _on_timeout(flag: threading.Event, proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    flag.set()
    proc.kill()


def _base(threads: int) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    if threads:
        cmd += ["-threads", str(threads)]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "5", *cmd]
    return cmd


def run_ffmpeg(
    *,
    input_path: Path,
    output_args: list[str],
    output_path: Path | None,
    duration_s: float | None,
    timeout_s: int,
    threads: int = 0,
    input_args: list[str] | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Transcode `input_path`. `input_args` go before -i (e.g. a fast input-side -ss)."""
    cmd = _base(threads) + [
        "-protocol_whitelist",
        "file",
        *(input_args or []),
        "-i",
        str(input_path),
        *output_args,
    ]
    if output_path is not None:
        cmd.append(str(output_path))
    _run(cmd, duration_s, timeout_s, on_progress, cancelled)


def _run(
    cmd: list[str],
    duration_s: float | None,
    timeout_s: int,
    on_progress: Callable[[float], None] | None,
    cancelled: Callable[[], bool] | None,
) -> None:
    proc = subprocess.Popen(  # noqa: S603
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True
    )
    tail: deque[str] = deque(maxlen=40)
    timed_out = threading.Event()

    def _drain() -> None:
        assert proc.stderr
        for line in proc.stderr:
            tail.append(line.rstrip())

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    timer = threading.Timer(timeout_s, _on_timeout, args=(timed_out, proc))
    timer.start()
    was_cancelled = False
    try:
        assert proc.stdout
        for line in proc.stdout:
            if line.startswith("out_time_us=") and duration_s and on_progress:
                try:
                    on_progress(min(0.999, max(0.0, int(line.split("=", 1)[1]) / 1e6 / duration_s)))
                except ValueError:
                    pass
            if cancelled and cancelled():
                was_cancelled = True
                proc.kill()
                break
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=5)
    if was_cancelled:
        raise Cancelled()
    if timed_out.is_set():
        raise FfmpegError("ffmpeg_timeout", "Processing took too long and was stopped")
    if proc.returncode != 0:
        raise FfmpegError("ffmpeg_failed", "Video processing failed", "\n".join(tail))
    if on_progress:
        on_progress(1.0)


def run_ffmpeg_to_pipe(
    *,
    input_path: Path,
    output_args: list[str],
    chunk_bytes: int,
    timeout_s: int,
    threads: int = 0,
    on_chunk: Callable[[bytes], None],
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Decode to stdout and hand raw chunks to `on_chunk` (used for waveform extraction)."""
    cmd = [c for c in _base(threads) if c not in ("-progress", "pipe:1", "-nostats")]
    cmd += ["-protocol_whitelist", "file", "-i", str(input_path), *output_args, "pipe:1"]
    proc = subprocess.Popen(  # noqa: S603
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
    )
    tail: deque[str] = deque(maxlen=40)
    timed_out = threading.Event()

    def _drain() -> None:
        assert proc.stderr
        for line in proc.stderr:
            tail.append(line.decode(errors="replace").rstrip())

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    timer = threading.Timer(timeout_s, _on_timeout, args=(timed_out, proc))
    timer.start()
    was_cancelled = False
    try:
        assert proc.stdout
        while chunk := proc.stdout.read(chunk_bytes):
            on_chunk(chunk)
            if cancelled and cancelled():
                was_cancelled = True
                proc.kill()
                break
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=5)
    if was_cancelled:
        raise Cancelled()
    if timed_out.is_set():
        raise FfmpegError("ffmpeg_timeout", "Processing took too long and was stopped")
    if proc.returncode != 0:
        raise FfmpegError("ffmpeg_failed", "Audio extraction failed", "\n".join(tail))


def capture_ffmpeg(args: list[str], timeout_s: int) -> str:
    """Run an analysis pass (loudness, black or silence detection) and return what ffmpeg printed at info level.

    Argument list only, protocol whitelist on, hard timeout. Used for measuring, never for producing a file."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-protocol_whitelist", "file", *args]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "5", *cmd]
    try:
        proc = subprocess.run(  # noqa: S603  (argument list, no shell)
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError("ffmpeg_timeout", "Measuring the clip took too long and was stopped") from exc
    if proc.returncode != 0:
        raise FfmpegError("ffmpeg_failed", "Measuring the clip failed", "\n".join(proc.stderr.splitlines()[-20:]))
    return proc.stderr
