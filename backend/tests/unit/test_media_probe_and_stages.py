from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.adapters.media.ffprobe import MediaValidationError, probe
from app.api.v1.media import parse_range
from app.errors import AppError
from app.pipeline.stages.waveform import peaks_from_samples

pytestmark = pytest.mark.unit


def test_probe_valid_video(small_mp4: Path) -> None:
    r = probe(small_mp4)
    assert (r.width, r.height, r.video_codec, r.audio_codec, r.has_audio) == (320, 240, "h264", "aac", True)
    assert 2.5 < r.duration_s < 3.5 and r.fps == 25.0 and "mp4" in r.container


def test_probe_silent_video(media_dir: Path) -> None:
    r = probe(media_dir / "hd_silent.mp4")
    assert r.has_audio is False and r.audio_codec is None and (r.width, r.height) == (1920, 1080)


@pytest.mark.parametrize(
    ("name", "code"),
    [("not_video.mp4", "not_media"), ("audio_only.mp4", "no_video_stream")],
)
def test_probe_rejects_non_video(media_dir: Path, name: str, code: str) -> None:
    with pytest.raises(MediaValidationError) as e:
        probe(media_dir / name)
    assert e.value.code == code


def test_probe_rejects_empty_and_truncated(tmp_path: Path, small_mp4: Path) -> None:
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    with pytest.raises(MediaValidationError):
        probe(empty)
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(small_mp4.read_bytes()[:200])
    with pytest.raises(MediaValidationError):
        probe(cut)


def test_probe_rejects_images_that_ffmpeg_can_read(tmp_path: Path) -> None:
    png = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:duration=1",
                    "-frames:v", "1", "-f", "image2", str(png)], check=True)  # fmt: skip
    with pytest.raises(MediaValidationError) as e:
        probe(png)
    assert e.value.code in {"unsupported_container", "not_media", "too_short", "no_duration"}


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=90-", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=-500", 100, (0, 99)),
        ("bytes=50-5000", 100, (50, 99)),
        ("bytes=0-0", 100, (0, 0)),
        ("bytes=0-1,5-9", 100, None),  # multi-range: whole file is a valid response
        ("garbage", 100, None),
        ("bytes=-", 100, None),
    ],
)
def test_range_parsing(header: str | None, size: int, expected: tuple[int, int] | None) -> None:
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=500-600", "bytes=9-3", "bytes=-0"])
def test_unsatisfiable_ranges_give_416(header: str) -> None:
    with pytest.raises(AppError) as e:
        parse_range(header, 100)
    assert e.value.status_code == 416 and e.value.headers == {"Content-Range": "bytes */100"}


def test_waveform_peaks_shape_and_range() -> None:
    t = np.arange(8000 * 2)
    samples = (np.sin(t / 20) * 30000).astype(np.int16)
    peaks = peaks_from_samples(samples, 100)
    assert len(peaks) == 100 and all(-127 <= lo <= hi <= 127 for lo, hi in peaks)
    assert max(hi for _, hi in peaks) >= 115  # 30000/32768 of full scale is about 116
    assert peaks_from_samples(np.empty(0, dtype=np.int16), 10) == []
    json.dumps(peaks)  # serializable


def test_rebucket_merges_without_losing_extremes() -> None:
    from app.pipeline.stages.waveform import rebucket

    mins = list(range(0, -2013, -1))
    maxs = list(range(2013))
    lo, hi = rebucket(mins, maxs, 2000)
    assert len(lo) == len(hi) == 2000 and min(lo) == min(mins) and max(hi) == max(maxs)
    assert rebucket([1, 2], [3, 4], 2000) == ([1, 2], [3, 4])
