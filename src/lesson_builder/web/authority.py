"""Entry points: `require_ipv4_bind` and `require_loopback_bind` guard server binds; `find_request_authority_error` guards each request.

Both servers validate the Host and Origin headers of every request against the
bound address so DNS rebinding cannot reach authored source through a browser.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import cast
from urllib.parse import urlsplit

from lesson_builder.web.settings import K_WEB_LOOPBACK_HOSTS
from lesson_builder.web.settings import K_WEB_MAX_PORT
from lesson_builder.web.settings import K_WEB_WILDCARD_HOST


def require_ipv4_bind(host: str, port: int) -> None:
    """Raise ``ValueError`` unless ``host`` is a concrete IPv4 bind and ``port`` is valid."""
    if not host or host == K_WEB_WILDCARD_HOST:
        raise ValueError("server host must be a concrete IPv4 bind address")
    if ":" in host:
        raise ValueError("server host must be an IPv4 address")
    if not 0 <= port <= K_WEB_MAX_PORT:
        raise ValueError("server port must be between 0 and 65535")


def require_loopback_bind(host: str, port: int) -> None:
    """Raise ``ValueError`` unless the bind is a loopback host and a valid port."""
    require_ipv4_bind(host, port)
    if host.lower() not in K_WEB_LOOPBACK_HOSTS:
        raise ValueError("server host must be loopback (127.0.0.1 or localhost)")


def find_request_authority_error(handler: BaseHTTPRequestHandler) -> tuple[int, str] | None:
    """Return an HTTP error for a Host or Origin other than the bound address."""
    address = cast(tuple[str, int], handler.server.server_address)
    bound_host = str(address[0])
    bound_port = address[1]
    host = handler.headers.get("Host")
    if host is None or not _is_allowed_authority(host, bound_host, bound_port):
        return 403, "untrusted Host authority"
    origin = handler.headers.get("Origin")
    if origin is None:
        return None
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return 400, "malformed Origin"
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.path not in {"", "/"}:
        return 400, "malformed Origin"
    if hostname is None or port is None:
        return 400, "malformed Origin"
    if not _is_allowed_host(hostname, bound_host) or port != bound_port:
        return 403, "untrusted Origin authority"
    return None


def _is_allowed_authority(authority: str, bound_host: str, bound_port: int) -> bool:
    """Validate a Host header against the bound host and port."""
    try:
        parsed = urlsplit(f"//{authority}")
        return (
            not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and parsed.hostname is not None
            and parsed.port == bound_port
            and _is_allowed_host(parsed.hostname, bound_host)
        )
    except ValueError:
        return False


def _is_allowed_host(host: str, bound_host: str) -> bool:
    """Allow the bound address without accepting unrelated DNS aliases."""
    normalized = host.lower()
    bound_normalized = bound_host.lower()
    if bound_normalized in K_WEB_LOOPBACK_HOSTS:
        return normalized in K_WEB_LOOPBACK_HOSTS
    return normalized == bound_normalized


__all__ = [
    # Bind guards.
    "require_ipv4_bind",
    "require_loopback_bind",
    # Request guard.
    "find_request_authority_error",
]
