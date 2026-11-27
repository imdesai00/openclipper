import base64

import pytest

from app.adapters.sources.youtube_url import NotSupportedLinkError, is_youtube_host, normalize_video_url
from app.adapters.sources.ytdlp import classify_error
from app.errors import AppError
from app.services.uploads import parse_metadata

pytestmark = pytest.mark.unit
VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=30s&list=PLabc",
        f"https://m.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/live/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"  https://www.youtube.com/watch?v={VID}  ",
    ],
)
def test_video_links_are_normalized(url: str) -> None:
    assert normalize_video_url(url) == f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PLabc",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/channel/UC1234567890123456789012",
        "https://www.youtube.com/c/Name",
        "https://www.youtube.com/user/name",
        "https://www.youtube.com/results?search_query=x",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/",
        "https://evil.example/watch?v=" + VID,
        "https://youtube.com.evil.example/watch?v=" + VID,
        "ftp://www.youtube.com/watch?v=" + VID,
    ],
)
def test_channels_playlists_and_lookalikes_are_refused(url: str) -> None:
    with pytest.raises(NotSupportedLinkError):
        normalize_video_url(url)


def test_host_detection_is_exact() -> None:
    assert is_youtube_host(f"https://youtu.be/{VID}")
    assert not is_youtube_host("https://notyoutube.com/x")
    assert not is_youtube_host("https://youtube.com.evil.example/x")


@pytest.mark.parametrize(
    ("output", "code"),
    [
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "youtube_bot_check"),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "youtube_forbidden"),
        ("ERROR: HTTP Error 429: Too Many Requests", "youtube_rate_limited"),
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "video_private"),
        ("ERROR: [youtube] x: Video unavailable", "video_unavailable"),
        ("ERROR: This video is DRM protected", "drm_protected"),
        ("ERROR: Requested format is not available", "format_unavailable"),
        ("ERROR: Unsupported URL: https://example.com", "unsupported_url"),
        ("something totally new went wrong", "ytdlp_failed"),
    ],
)
def test_yt_dlp_errors_map_to_actionable_codes(output: str, code: str) -> None:
    err = classify_error(output)
    assert err.code == code and err.message  # never an empty or raw-traceback message


def _meta(**kv: str) -> str:
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in kv.items())


def test_tus_metadata_parsing() -> None:
    m = parse_metadata(_meta(filename="my video.mp4", project_id="abc") + ",flag")
    assert m == {"filename": "my video.mp4", "project_id": "abc", "flag": ""}
    assert parse_metadata(None) == {}


@pytest.mark.parametrize("bad", ["bad key!! Zm9v", "key !!notbase64!!"])
def test_tus_metadata_rejects_garbage(bad: str) -> None:
    with pytest.raises(AppError):
        parse_metadata(bad)


@pytest.mark.parametrize(
    ("stderr", "code", "must_not_contain"),
    [
        ("ERROR: [youtube] abc: Unable to download API page: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
         "tls_certificate_error", "null"),
        ("ERROR: [youtube] abc: Failed to resolve 'www.youtube.com' ([Errno -2] Name or service not known)",
         "network_unreachable", "null"),
    ],
)  # fmt: skip
def test_network_errors_are_explained(stderr: str, code: str, must_not_contain: str) -> None:
    err = classify_error(stderr + "\nnull\n")
    assert err.code == code and must_not_contain not in err.message.split()


def test_unknown_errors_never_say_null_or_show_the_noisy_prefix() -> None:
    err = classify_error("null\nERROR: [youtube] aqz-KE-bpKQ: Something unexpected happened here\nnull")
    assert err.code == "ytdlp_failed"
    assert err.message == "The download failed: Something unexpected happened here"
    assert "null" not in classify_error("null").message.split()
    assert classify_error("").message  # even with no output at all the user gets a sentence
