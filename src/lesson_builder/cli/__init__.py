"""Entry points: `main` (the ``lesson-data`` console script) and `build_parser`.

`main` parses one command invocation and returns its exit code. `build_parser`
assembles the full parser; command families register themselves in
:mod:`lesson_builder.cli.commands` and keep domain imports lazy inside
handlers, so parser construction initializes no providers, graphs, or
publication adapters.
"""

from __future__ import annotations

import argparse

from lesson_builder.cli.commands import add_commands

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Assemble the complete lesson-data parser with every command family."""
    parser = argparse.ArgumentParser(prog="lesson-data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
