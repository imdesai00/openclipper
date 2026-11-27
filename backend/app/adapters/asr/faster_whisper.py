"""faster-whisper (CTranslate2) engine. The library is imported lazily: only the worker-asr image has it."""

from __future__ import annotations

import logging
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.config import Settings
from app.ports.asr import ASRError, SegmentData, TranscriptData, WordData

log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_LOADED: dict[str, Any] = {}  # one model kept in memory between jobs; loading takes seconds and a few hundred MB


def _need_library() -> Any:
    try:
        import faster_whisper
    except ImportError as exc:  # the API and media images deliberately do not ship it
        raise ASRError("asr_not_installed", "Speech recognition is not installed in this container.") from exc
    return faster_whisper


def read_wav_16k_mono(path: Path) -> np.ndarray:
    """Float32 samples in [-1, 1]. We control the format (extract_audio writes it), so we do not need PyAV to decode."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise ASRError("bad_audio", "The prepared audio is not 16 kHz mono 16-bit.")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


class FasterWhisperEngine:
    name = "faster-whisper"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.cache = settings.whisper_cache_dir

    # ------------------------------------------------------------------ device
    def device_and_compute(self) -> tuple[str, str]:
        device = self.s.whisper_device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                device = "cpu"
        compute = self.s.whisper_compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        return device, compute

    # ------------------------------------------------------------------ model files
    def _repo(self, model: str) -> str:
        from faster_whisper.utils import _MODELS

        return str(_MODELS.get(model, model))

    def _snapshot(self, model: str, local_only: bool) -> str:
        from huggingface_hub import snapshot_download

        return str(
            snapshot_download(
                self._repo(model),
                cache_dir=self.cache,
                local_files_only=local_only,
                allow_patterns=[
                    "config.json",
                    "preprocessor_config.json",
                    "model.bin",
                    "tokenizer.json",
                    "vocabulary.*",
                ],
            )
        )

    def is_model_available(self, model: str) -> bool:
        _need_library()
        try:
            self._snapshot(model, True)
            return True
        except Exception:
            return False

    def ensure_model(self, model: str, progress: Callable[[float, str | None], None]) -> None:
        _need_library()
        if self.is_model_available(model):
            return
        from huggingface_hub import HfApi, snapshot_download
        from tqdm.auto import tqdm

        repo = self._repo(model)
        patterns = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]
        try:
            info = HfApi().model_info(repo, files_metadata=True)
            wanted = {
                s.rfilename: (s.size or 0)
                for s in (info.siblings or [])
                if any(_match(s.rfilename, p) for p in patterns)
            }
            total = max(1, sum(wanted.values()))
        except Exception as exc:
            raise ASRError(
                "model_unavailable", f"Could not look up the speech model '{model}'. Is the internet reachable?"
            ) from exc
        done = [0]
        lock = threading.Lock()

        class Bar(tqdm):  # type: ignore[misc]
            def __init__(self, *a: Any, **kw: Any) -> None:
                kw["disable"] = True  # never print to the console
                super().__init__(*a, **kw)
                self._bytes = kw.get("unit") == "B"

            def update(self, n: float | None = 1) -> bool | None:
                if self._bytes and n:
                    with lock:
                        done[0] += int(n)
                        progress(min(0.99, done[0] / total), f"Downloading speech model {model}")
                return None

        progress(0.0, f"Downloading speech model {model}")
        try:
            snapshot_download(repo, cache_dir=self.cache, allow_patterns=patterns, tqdm_class=Bar)
        except Exception as exc:
            log.exception("speech model download failed: %s", model)
            raise ASRError(
                "model_download_failed", f"Could not download the speech model '{model}': {type(exc).__name__}."
            ) from exc
        progress(1.0, f"Speech model {model} ready")

    def downloaded_models(self) -> list[str]:
        """Names of the models that are fully on disk (cheap: looks at the cache folders, no network)."""
        from app.pipeline.asr_options import MODELS

        out = []
        for name in MODELS:
            try:
                if self.is_model_available(name):
                    out.append(name)
            except ASRError:
                return []
        return out

    # ------------------------------------------------------------------ recognition
    def _load(self, model: str) -> Any:
        fw = _need_library()
        device, compute = self.device_and_compute()
        key = f"{model}|{device}|{compute}"
        with _LOCK:
            if key not in _LOADED:
                _LOADED.clear()  # keep memory flat: only the most recent model stays loaded
                path = self._snapshot(model, True)
                _LOADED[key] = fw.WhisperModel(
                    path, device=device, compute_type=compute, cpu_threads=self.s.whisper_cpu_threads
                )
            return _LOADED[key]

    def transcribe(
        self,
        audio: Path,
        *,
        model: str,
        language: str | None,
        vad: bool,
        progress: Callable[[float, str | None], None],
        cancelled: Callable[[], bool],
    ) -> TranscriptData:
        try:
            samples = read_wav_16k_mono(audio)
        except (wave.Error, EOFError) as exc:
            raise ASRError("bad_audio", "The prepared audio could not be read.") from exc
        if samples.size < 1600:
            raise ASRError("audio_too_short", "There is not enough audio to transcribe.")
        duration = samples.size / 16000
        wm = self._load(model)
        progress(0.0, "Recognising speech")
        segments_iter, info = wm.transcribe(
            samples,
            language=language,
            word_timestamps=True,
            vad_filter=vad,
            beam_size=5,
            condition_on_previous_text=False,  # stops one bad sentence from poisoning the rest (repetition loops)
        )
        out: list[SegmentData] = []
        for seg in segments_iter:  # lazy generator: recognition happens as we iterate
            if cancelled():
                raise ASRError("cancelled", "Cancelled")
            words = [
                WordData(
                    start=float(w.start), end=float(w.end), text=str(w.word).strip(), probability=float(w.probability)
                )
                for w in (seg.words or [])
                if str(w.word).strip()
            ]
            text = str(seg.text).strip()
            if text and words:
                out.append(SegmentData(start=float(seg.start), end=float(seg.end), text=text, words=words))
            progress(min(0.99, float(seg.end) / duration), "Recognising speech")
        return TranscriptData(
            language=str(info.language),
            language_probability=float(info.language_probability),
            duration_s=duration,
            engine=self.name,
            model=model,
            segments=out,
        )


def _match(name: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(name, pattern)
