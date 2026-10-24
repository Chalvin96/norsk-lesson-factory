"""Not a check itself — run a local test server and issue raw HTTP requests against it."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPConnection
from http.client import HTTPResponse
from http.server import HTTPServer
from socket import create_connection


@dataclass(frozen=True)
class HttpReply:
    """Status, selected headers, and decoded body of one response."""

    status: int
    headers: dict[str, str]
    body: str


@contextmanager
def serving(server: HTTPServer) -> Iterator[HTTPServer]:
    """Serve on a daemon thread and always release the socket afterwards."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def send_request(
    server: HTTPServer,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    host: str | None = None,
    omit_host: bool = False,
    body: bytes | None = None,
) -> HttpReply:
    """Send one request using the bound authority unless ``omit_host`` is true."""
    address = str(server.server_address[0])
    connection = HTTPConnection(address, server.server_port)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if not omit_host:
            authority = host if host is not None else f"{address}:{server.server_port}"
            connection.putheader("Host", authority)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return HttpReply(response.status, {key.lower(): value for key, value in response.getheaders()}, body)
    finally:
        connection.close()


def send_raw_request(server: HTTPServer, request: bytes) -> HttpReply:
    """Send exact request bytes and return the parsed HTTP response."""
    address = str(server.server_address[0])
    with create_connection((address, server.server_port)) as connection:
        connection.sendall(request)
        response = HTTPResponse(connection)
        response.begin()
        body = response.read().decode("utf-8", errors="replace")
        return HttpReply(response.status, {key.lower(): value for key, value in response.getheaders()}, body)


__all__ = ["HttpReply", "send_raw_request", "send_request", "serving"]
