from __future__ import annotations

import http.server
import threading
from pathlib import Path
from typing import Any

import pytest

from app.adapters.sources import direct_url
from app.adapters.sources.direct_url import DownloadError, safe_filename
from app.security import ssrf
from app.security.ssrf import UnsafeUrlError, check_url, is_public

pytestmark = pytest.mark.unit

BLOCKED_IPS = [
    "127.0.0.1", "127.1.2.3", "10.0.0.5", "192.168.1.1", "172.16.0.1", "172.31.255.255",
    "169.254.169.254", "169.254.0.1", "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254",
]  # fmt: skip


@pytest.mark.parametrize("ip", BLOCKED_IPS)
def test_non_public_addresses_are_not_public(ip: str) -> None:
    assert not is_public(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses_are_public(ip: str) -> None:
    assert is_public(ip)


@pytest.mark.parametrize("ip", [i for i in BLOCKED_IPS if ":" not in i])
def test_literal_private_ipv4_urls_are_blocked(ip: str) -> None:
    with pytest.raises(UnsafeUrlError) as e:
        check_url(f"http://{ip}/video.mp4")
    assert e.value.code == "blocked_address"


@pytest.mark.parametrize("host", ["[::1]", "[fe80::1]", "[::ffff:127.0.0.1]", "[fd12:3456::1]"])
def test_literal_private_ipv6_urls_are_blocked(host: str) -> None:
    with pytest.raises(UnsafeUrlError):
        check_url(f"http://{host}/video.mp4")


def test_localhost_by_name_is_blocked() -> None:
    with pytest.raises(UnsafeUrlError):
        check_url("http://localhost:8000/x.mp4")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("file:///etc/passwd", "invalid_scheme"),
        ("ftp://8.8.8.8/x.mp4", "invalid_scheme"),
        ("gopher://8.8.8.8/", "invalid_scheme"),
        ("javascript:alert(1)", "invalid_scheme"),
        ("http://user:pw@8.8.8.8/x.mp4", "credentials_in_url"),
        ("http:///nohost", "invalid_url"),
        ("http://8.8.8.8:99999/x", "invalid_url"),
    ],
)
def test_bad_schemes_and_shapes(url: str, code: str) -> None:
    with pytest.raises(UnsafeUrlError) as e:
        check_url(url)
    assert e.value.code == code


def test_public_literal_ip_is_accepted_and_pinned() -> None:
    t = check_url("https://8.8.8.8/a/b.mp4?x=1")
    assert (t.scheme, t.port, t.ip, t.path_query) == ("https", 443, "8.8.8.8", "/a/b.mp4?x=1")


def test_private_allowed_only_when_explicitly_enabled() -> None:
    assert check_url("http://127.0.0.1:9/x.mp4", allow_private=True).ip == "127.0.0.1"


def test_dns_rebinding_mix_of_public_and_private_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssrf, "resolve", lambda host, port: ["8.8.8.8", "10.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        check_url("http://rebind.example/x.mp4")


class _Resp:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status_code, self.headers = status, headers

    def close(self) -> None:
        pass


def test_redirect_to_internal_address_is_blocked_on_every_hop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hops: list[str] = []

    def fake_open(target: ssrf.SafeTarget, _client: Any) -> _Resp:
        hops.append(target.ip)
        return _Resp(302, {"location": "http://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr(direct_url, "_open", fake_open)
    with pytest.raises(DownloadError) as e:
        direct_url.download("http://8.8.8.8/video.mp4", tmp_path, max_bytes=10**6, timeout_s=5, allow_private=False)
    assert e.value.code == "blocked_address"
    assert hops == ["8.8.8.8"]  # the metadata address was never contacted


def test_redirect_loop_is_cut_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(direct_url, "_open", lambda *_: _Resp(302, {"location": "http://8.8.8.8/again"}))
    with pytest.raises(DownloadError) as e:
        direct_url.download("http://8.8.8.8/a", tmp_path, max_bytes=10**6, timeout_s=5, allow_private=False)
    assert e.value.code == "too_many_redirects"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("../../etc/passwd", "passwd"), ("a b.mp4", "a b.mp4"), ("..\\..\\x.mp4", "x.mp4"), ("", "video"), ("%2e%2e/%2e%2e/x.mp4", "x.mp4"),
     ("n\x00ame.mp4", "n_ame.mp4"), ("x" * 500 + ".mp4", ("x" * 500 + ".mp4")[:200])],
)  # fmt: skip
def test_filenames_are_sanitized(raw: str, expected: str) -> None:
    out = safe_filename(raw)
    assert out == expected and "/" not in out and "\\" not in out and ".." not in out.split("/")


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:
        _Handler.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a: object) -> None:
        pass


def test_hls_playlist_disguised_as_mp4_never_makes_ffmpeg_fetch_urls(tmp_path: Path) -> None:
    """The classic attack: a text playlist saved as .mp4 that points ffmpeg at internal URLs."""
    from app.adapters.media.ffprobe import MediaValidationError, probe

    _Handler.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        evil = tmp_path / "evil.mp4"
        evil.write_text(
            "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\n"
            f"http://127.0.0.1:{port}/internal-secret\n#EXT-X-ENDLIST\n"
        )
        with pytest.raises(MediaValidationError):
            probe(evil)
        concat = tmp_path / "concat.mp4"
        concat.write_text(f"ffconcat version 1.0\nfile 'http://127.0.0.1:{port}/concat-secret'\nfile '/etc/passwd'\n")
        with pytest.raises(MediaValidationError):
            probe(concat)
    finally:
        srv.shutdown()
    assert _Handler.hits == []  # not a single request reached the "internal" server
