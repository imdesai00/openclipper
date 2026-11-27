from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.clip_helpers import DURATION_S, run
from tests.fake_llm import FakeLLM

OLLAMA = os.environ.get("TEST_OLLAMA_URL")
OLLAMA_MODEL = os.environ.get("TEST_OLLAMA_MODEL", "qwen2.5:0.5b")

needs_ollama = pytest.mark.skipif(
    not OLLAMA, reason="SKIPPED: no Ollama server (set TEST_OLLAMA_URL and pull TEST_OLLAMA_MODEL)"
)


@pytest.fixture
def fake() -> Iterator[FakeLLM]:
    f = FakeLLM()
    yield f
    f.close()


@pytest.fixture
def fake2() -> Iterator[FakeLLM]:
    f = FakeLLM()
    yield f
    f.close()


# ---------------------------------------------------------------- media for the clip tests
@pytest.fixture(scope="session")
def red_blue(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """1280x720: the LEFT half is solid red, the RIGHT half solid blue, with a quiet tone (-30 dB) for 20 s."""
    out = tmp_path_factory.mktemp("clips") / "redblue.mp4"
    f = ["-f", "lavfi", "-i"]
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *f, f"color=c=red:s=640x720:r=25:d={DURATION_S}",
         *f, f"color=c=blue:s=640x720:r=25:d={DURATION_S}", *f, f"sine=frequency=440:sample_rate=48000:duration={DURATION_S}",
         "-filter_complex", "[0][1]hstack=inputs=2[v];[2]volume=-30dB[a]", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])  # fmt: skip
    return out


@pytest.fixture(scope="session")
def black_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("clipsb") / "black.mp4"
    f = ["-f", "lavfi", "-i"]
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *f, f"color=c=black:s=640x360:r=25:d={DURATION_S}",
         *f, f"sine=frequency=300:duration={DURATION_S}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(out)])  # fmt: skip
    return out


@pytest.fixture(scope="session")
def podcast(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("clipsa") / "podcast.m4a"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=330:sample_rate=48000:duration={DURATION_S}", "-af", "volume=-20dB", "-c:a", "aac", str(out)])  # fmt: skip
    return out
