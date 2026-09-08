"""Entry point: `create_preview_server` serves one in-memory HTML preview."""

from __future__ import annotations

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from socket import AF_INET6
from typing import ClassVar
from typing import cast
from urllib.parse import urlsplit

K_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
K_LOOPBACK_IPV6 = "::1"
K_MAX_PORT = 65535


class PreviewServer(ThreadingHTTPServer):
    """Threaded loopback server whose only resource is the rendered preview."""

    allow_reuse_address = True
    daemon_threads = True


class PreviewServerIPv6(PreviewServer):
    """Threaded IPv6 loopback server for the bracketed ``[::1]`` authority."""

    address_family = AF_INET6


def create_preview_server(
    host: str,
    port: int,
    document: str,
    document_factory: Callable[[], str] | None = None,
) -> PreviewServer:
    """Create a loopback-only server, optionally rebuilding HTML for every GET."""
    if host not in K_LOOPBACK_HOSTS and host != K_LOOPBACK_IPV6:
        raise ValueError("preview server host must be a loopback address")
    if not 0 <= port <= K_MAX_PORT:
        raise ValueError("preview server port must be between 0 and 65535")
    handler = _build_preview_handler(document, document_factory)
    server_type = PreviewServerIPv6 if host == K_LOOPBACK_IPV6 else PreviewServer
    return server_type((host, port), handler)


def build_preview_url(server: PreviewServer) -> str:
    """Return the safe local URL for a running preview server."""
    host = str(server.server_address[0])
    authority = f"[{host}]" if host == K_LOOPBACK_IPV6 else host
    return f"http://{authority}:{server.server_port}/"


def _build_preview_handler(document: str, document_factory: Callable[[], str] | None) -> type[BaseHTTPRequestHandler]:
    """Build a handler closed over HTML and an optional fresh-document factory."""
    document_bytes = document.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "NorskLessonFactoryPreview/1.0"
        _document: ClassVar[bytes] = document_bytes
        _document_factory: ClassVar[Callable[[], str] | None] = document_factory

        def do_GET(self) -> None:  # noqa: N802 (stdlib handler API)
            """Serve only the root preview document."""
            authority_status = _validate_request_authority(self)
            if authority_status is not None:
                self.send_error(*authority_status)
                return
            if urlsplit(self.path).path != "/":
                self.send_error(404, "preview resource not found")
                return
            try:
                factory = type(self)._document_factory
                response_document = factory() if factory is not None else None
                response_bytes = response_document.encode("utf-8") if response_document is not None else self._document
            except Exception:
                response_bytes = _rebuild_error_document()
                self.send_response(503)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(response_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(response_bytes)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format: str, *args: object) -> None:
            """Keep authored content and request details out of terminal logs."""

    return Handler


def _rebuild_error_document() -> bytes:
    """Return an actionable response when a live source reload fails."""
    return b"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Preview unavailable - norsk lesson factory</title></head>
<body><main><h1>Preview unavailable</h1>
<p>The source could not be rebuilt for this request. Fix the authored source and reload this page.</p>
<p>The previous preview was not reused, so this state cannot be mistaken for current content.</p>
</main></body></html>"""


def _validate_request_authority(handler: BaseHTTPRequestHandler) -> tuple[int, str] | None:
    """Return an HTTP error for an untrusted Host or Origin authority."""
    address = cast(tuple[str, int], handler.server.server_address)
    bound_port = address[1]
    host = handler.headers.get("Host")
    if host is None or not _is_allowed_authority(host, bound_port):
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
    if not _is_allowed_host(hostname) or port != bound_port:
        return 403, "untrusted Origin authority"
    return None


def _is_allowed_authority(authority: str, bound_port: int) -> bool:
    """Validate a Host header's exact loopback name/IP and bound port."""
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
            and _is_allowed_host(parsed.hostname)
        )
    except ValueError:
        return False


def _is_allowed_host(host: str) -> bool:
    """Allow exact local authorities without accepting DNS aliases."""
    normalized = host.lower()
    return normalized in {*K_LOOPBACK_HOSTS, K_LOOPBACK_IPV6}


__all__ = ["K_LOOPBACK_HOSTS", "PreviewServer", "create_preview_server", "build_preview_url"]
