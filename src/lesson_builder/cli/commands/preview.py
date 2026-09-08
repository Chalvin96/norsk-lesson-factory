"""Entry point: `add_preview_commands` registers the local author preview."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_preview_commands(parent: SubparserRegistrar) -> None:
    """Register the provider-free read-only lesson preview command."""
    parser = parent.add_parser("preview", help="Serve a read-only local authoring preview")
    parser.add_argument("lesson", help="lesson id or path under content/lessons")
    parser.add_argument("--workspace-root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--host", default="127.0.0.1", help="loopback host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="port, or 0 to choose a free local port")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    parser.set_defaults(func=_preview)


def _preview(args: argparse.Namespace) -> int:
    """Build and serve one preview until interrupted."""
    from lesson_builder.application.operations.author_preview import build_author_preview
    from lesson_builder.formats.html.author_preview import render_author_preview
    from lesson_builder.workspace.preview_server import build_preview_url
    from lesson_builder.workspace.preview_server import create_preview_server

    root = Path(args.workspace_root) if args.workspace_root else get_workspace_root()

    def render_current_preview() -> str:
        """Re-read source files so each browser request reflects current edits."""
        return render_author_preview(build_author_preview(root, args.lesson))

    try:
        document = render_current_preview()
        server = create_preview_server(args.host, args.port, document, render_current_preview)
    except (OSError, TypeError, ValueError) as exc:
        print(f"lesson-data preview: {exc}", file=sys.stderr)
        return 1
    url = build_preview_url(server)
    print(f"Authoring preview available at {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAuthoring preview stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


__all__ = ["add_preview_commands"]
