"""Entry points: `send_uncached_html` and `send_uncached_json` write responses; `render_rebuild_error_document` renders the 503 page."""

from __future__ import annotations

import json
from html import escape
from http.server import BaseHTTPRequestHandler


def send_uncached_html(handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
    """Write an HTML response that browsers must not cache."""
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_uncached_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    """Write a JSON response that browsers must not cache."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def send_uncached_audio(handler: BaseHTTPRequestHandler, body: bytes) -> None:
    """Write an audio response that browsers must not cache between draft edits."""
    handler.send_response(200)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def render_rebuild_error_document(surface: str) -> bytes:
    """Return an actionable page for a failed live source rebuild."""
    name = escape(surface)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} unavailable - norsk lesson factory</title></head>
<body><main><h1>{name} unavailable</h1>
<p>The source could not be rebuilt for this request. Fix the authored source and reload this page.</p>
<p>The previous {name.lower()} was not reused, so this state cannot be mistaken for current content.</p>
</main></body></html>""".encode()


__all__ = ["render_rebuild_error_document", "send_uncached_audio", "send_uncached_html", "send_uncached_json"]
