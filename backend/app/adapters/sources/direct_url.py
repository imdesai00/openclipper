"""Direct media URL download: SSRF-safe, size- and time-limited, content-type checked."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.security.ssrf import SafeTarget, UnsafeUrlError, check_url

MAX_REDIRECTS = 5
ALLOWED_TYPES = (
    "video/",
    "audio/",
    "application/octet-stream",
    "binary/octet-stream",
    "application/mp4",
    "application/x-matroska",
)
ALLOWED_EXT = {
    ".mp4",
    ".m4v",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".ts",
    ".mts",
    ".m2ts",
    ".flv",
    ".wmv",
    ".mpg",
    ".mpeg",
    # audio-only sources (podcasts, voice recordings), see DECISIONS D-029
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".aac",
}
CHUNK = 1024 * 1024


class DownloadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Downloaded:
    path: Path
    filename: str
    size: int
    ext: str


def safe_filename(name: str) -> str:
    name = unquote(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ]+", "_", name, flags=re.UNICODE).strip(" .")
    return name[:200] or "video"


def _filename(resp: httpx.Response, final_url: str) -> str:
    cd = resp.headers.get("content-disposition")
    if cd:
        msg = Message()
        msg["content-disposition"] = cd
        fn = msg.get_param("filename", header="content-disposition")
        if isinstance(fn, str) and fn:
            return safe_filename(fn)
    return safe_filename(urlsplit(final_url).path)


def _open(target: SafeTarget, client: httpx.Client) -> httpx.Response:
    """Connect to the validated IP; keep Host and TLS server name as the real hostname."""
    ip = f"[{target.ip}]" if ":" in target.ip else target.ip
    netloc = f"{ip}:{target.port}"
    req = client.build_request(
        "GET",
        f"{target.scheme}://{netloc}{target.path_query}",
        headers={
            "Host": target.host if target.port in (80, 443) else f"{target.host}:{target.port}",
            "Accept": "video/*,application/octet-stream;q=0.9",
            "Accept-Encoding": "identity",
            "User-Agent": "OpenClipper/0.1",
        },
        extensions={"sni_hostname": target.host},
    )
    return client.send(req, stream=True)


def download(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int,
    timeout_s: int,
    allow_private: bool,
    progress: Callable[[float, str | None], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Downloaded:
    deadline = time.monotonic() + timeout_s
    current = url
    with httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, read=60.0),
        verify=True,
        limits=httpx.Limits(max_connections=1),
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                target = check_url(current, allow_private=allow_private)  # re-checked on EVERY hop
            except UnsafeUrlError as exc:
                raise DownloadError(exc.code, exc.message) from exc
            try:
                resp = _open(target, client)
            except httpx.HTTPError as exc:
                raise DownloadError("fetch_failed", f"Could not download the file ({type(exc).__name__})") from exc
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                resp.close()
                if not loc:
                    raise DownloadError("bad_redirect", "The server sent a redirect without a location")
                current = urljoin(current, loc)
                continue
            break
        else:
            raise DownloadError("too_many_redirects", "Too many redirects")

        with closing(resp):
            if resp.status_code != 200:
                raise DownloadError("http_error", f"The server answered with status {resp.status_code}")
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if not ctype.startswith(ALLOWED_TYPES):
                raise DownloadError(
                    "bad_content_type",
                    f"That link is not a video file (content type: {ctype or 'unknown'})",
                )
            declared = resp.headers.get("content-length")
            total = int(declared) if declared and declared.isdigit() else None
            if total is not None and total > max_bytes:
                raise DownloadError("too_large", "The file is larger than the allowed download size")
            filename = _filename(resp, current)
            ext = Path(filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                ext = ".mp4" if ctype.startswith("video/") else ".mp3" if ctype.startswith("audio/") else ext
            if ext not in ALLOWED_EXT:
                raise DownloadError("bad_extension", "Unsupported file type")
            out = dest_dir / f"download{ext}"
            written = 0
            with out.open("wb") as f:
                for chunk in resp.iter_bytes(CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise DownloadError("too_large", "The file is larger than the allowed download size")
                    if time.monotonic() > deadline:
                        raise DownloadError("timeout", "The download took too long")
                    if cancelled and cancelled():
                        raise DownloadError("cancelled", "Cancelled")
                    f.write(chunk)
                    if progress and total:
                        progress(written / total, "Downloading")
    if written == 0:
        raise DownloadError("empty", "The server returned an empty file")
    return Downloaded(path=out, filename=filename, size=written, ext=ext.lstrip("."))
