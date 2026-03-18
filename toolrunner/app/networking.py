from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    pass


def ensure_safe_public_url(raw_url: str) -> None:
    parsed = urlparse(str(raw_url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only http and https URLs are allowed")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise UnsafeUrlError("URL hostname is required")
    if hostname in {"localhost", "0.0.0.0"} or hostname.endswith('.local'):
        raise UnsafeUrlError("Local or private hostnames are not allowed")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        _validate_hostname_resolution(hostname)
        return
    _validate_ip(ip)


def _validate_hostname_resolution(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Could not resolve hostname '{hostname}': {exc}") from exc
    if not infos:
        raise UnsafeUrlError(f"Could not resolve hostname '{hostname}'")
    for info in infos:
        candidate = info[4][0]
        _validate_ip(ipaddress.ip_address(candidate))


def _validate_ip(ip: ipaddress._BaseAddress) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeUrlError(f"Address '{ip}' is not publicly routable")
