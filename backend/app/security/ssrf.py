"""SSRF protection for user-supplied URLs (SPEC §3.1).

Rules: http/https only, no credentials in the URL, every hop re-resolved and checked, only globally
routable addresses (blocks loopback, private, link-local incl. cloud metadata 169.254.169.254, CGNAT,
multicast, reserved, IPv6 ULA, IPv4-mapped IPv6). The caller connects to the *validated IP* so DNS
rebinding between check and connect is impossible.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_SCHEMES = {"http", "https"}


class UnsafeUrlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SafeTarget:
    url: str
    scheme: str
    host: str
    port: int
    path_query: str
    ip: str  # the validated address to connect to


def _norm(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def is_public(ip_text: str) -> bool:
    try:
        ip = _norm(ipaddress.ip_address(ip_text))
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError("dns_failed", f"Could not resolve {host}") from exc
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


def check_url(url: str, *, allow_private: bool = False) -> SafeTarget:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("invalid_url", "That is not a valid URL") from exc
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("invalid_scheme", "Only http and https links are allowed")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials_in_url", "Links with embedded credentials are not allowed")
    host = (parts.hostname or "").strip().rstrip(".")
    if not host:
        raise UnsafeUrlError("invalid_url", "That is not a valid URL")
    port = port or (443 if parts.scheme.lower() == "https" else 80)
    addrs = resolve(host, port)
    if not addrs:
        raise UnsafeUrlError("dns_failed", f"Could not resolve {host}")
    if not allow_private:
        bad = [a for a in addrs if not is_public(a)]
        if bad:  # reject if ANY record is internal: a rebinding host often mixes public and private
            raise UnsafeUrlError("blocked_address", "That address is not allowed (private or internal network)")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return SafeTarget(url=url, scheme=parts.scheme.lower(), host=host, port=port, path_query=path, ip=addrs[0])
