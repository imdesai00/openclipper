"""Pure URL classification for YouTube video / live links. No network, no yt-dlp import."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class NotSupportedLinkError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_youtube_host(url: str) -> bool:
    try:
        return (urlsplit(url.strip()).hostname or "").lower() in HOSTS
    except ValueError:
        return False


def normalize_video_url(url: str) -> str:
    """Return https://www.youtube.com/watch?v=ID or raise. Channels, playlists and handles are refused."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in HOSTS:
        raise NotSupportedLinkError("not_youtube", "That is not a YouTube link")
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")
    qs = parse_qs(parts.query)
    vid: str | None = None
    if host.endswith("youtu.be"):
        vid = path.lstrip("/").split("/")[0]
    elif path == "/watch":
        vid = (qs.get("v") or [""])[0]
    else:
        m = re.match(r"^/(?:live|shorts|embed|v)/([^/]+)$", path)
        if m:
            vid = m.group(1)
    if not vid or not _ID.match(vid):
        raise NotSupportedLinkError(
            "unsupported_link",
            "Only single video or live links are supported. Channel, playlist and profile links are not.",
        )
    return f"https://www.youtube.com/watch?v={vid}"
