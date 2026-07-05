"""Command-line helpers for lesson metadata validation and graph runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from lesson_builder.pipeline.cli import add_parsers as add_graph_parsers
from lesson_builder.schema import ExportedLesson, Lesson


def _validate_lesson_file(path: Path) -> None:
    lesson_payload: dict[str, Any] = json.loads(path.read_text())
    if "schema_version" in lesson_payload:
        ExportedLesson.model_validate(lesson_payload)
    else:
        Lesson.model_validate(lesson_payload)


def _lesson_validate(args: argparse.Namespace) -> int:
    failed = False
    for file_name in args.require_files:
        path = Path(file_name)
        try:
            _validate_lesson_file(path)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            failed = True
            print(f"{path}: invalid: {exc}", file=sys.stderr)
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lesson-data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lesson_parser = subparsers.add_parser("lesson")
    lesson_subparsers = lesson_parser.add_subparsers(dest="lesson_command", required=True)

    validate_parser = lesson_subparsers.add_parser("validate")
    validate_parser.add_argument("--require-files", nargs="+", required=True)
    validate_parser.set_defaults(func=_lesson_validate)

    add_graph_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
