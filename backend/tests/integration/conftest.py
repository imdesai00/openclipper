from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

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
