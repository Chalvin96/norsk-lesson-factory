"""Entry point: `add_workbench_commands` registers the local authoring workbench."""

from __future__ import annotations

import argparse
import errno
import sys
import webbrowser
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.web.settings import K_WEB_DEFAULT_HOST
from lesson_builder.web.settings import K_WEB_DEFAULT_PORT
from lesson_builder.workspace.paths import get_workspace_root


def add_workbench_commands(parent: SubparserRegistrar) -> None:
    """Register the loopback-only read-only authoring workbench command."""
    parser = parent.add_parser("workbench", help="Serve the local authoring workbench (loopback only)")
    parser.add_argument("--workspace-root", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--host",
        default=K_WEB_DEFAULT_HOST,
        help=f"loopback bind address: 127.0.0.1 or localhost (default: {K_WEB_DEFAULT_HOST})",
    )
    parser.add_argument("--port", type=int, default=K_WEB_DEFAULT_PORT, help="port, or 0 to choose a free local port")
    parser.add_argument("--no-browser", action="store_true", help="print the URL without opening a browser")
    parser.set_defaults(func=_workbench)


def _workbench(args: argparse.Namespace) -> int:
    """Serve the workbench until interrupted."""
    from lesson_builder.application.operations.workbench_build import ensure_workbench_app
    from lesson_builder.web.server import build_workbench_url
    from lesson_builder.web.server import create_workbench_server

    root = Path(args.workspace_root) if args.workspace_root else get_workspace_root()
    try:
        ensure_workbench_app(root)
        server = create_workbench_server(root, args.host, args.port)
    except (OSError, RuntimeError, ValueError) as exc:
        message = (
            f"port {args.port} is already in use; choose another with --port or use --port 0"
            if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE
            else str(exc)
        )
        print(f"lesson-data workbench: {message}", file=sys.stderr)
        return 1
    url = build_workbench_url(server)
    print(f"Authoring workbench available at {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAuthoring workbench stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


__all__ = ["add_workbench_commands"]
