"""Entry point: `add_lesson_validation_commands` registers lesson-file validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.domain.lesson.models import ExportedLesson
from lesson_builder.domain.lesson.models import Lesson

K_CLI_PUBLIC_LESSON_FIELDS = frozenset({"schema_version", "id", "sections", "exercises", "practice_groups", "media"})


def add_lesson_validation_commands(parent: SubparserRegistrar) -> None:
    """Register the nested lesson validate command for authored or exported files."""
    lesson_parser = parent.add_parser("lesson")
    lesson_subparsers = lesson_parser.add_subparsers(dest="lesson_command", required=True)

    validate_parser = lesson_subparsers.add_parser("validate")
    validate_parser.add_argument("--require-files", nargs="+", required=True)
    validate_parser.set_defaults(func=_lesson_validate)


def _validate_lesson_file(path: Path) -> None:
    lesson_payload = json.loads(path.read_text())
    if not isinstance(lesson_payload, dict):
        raise TypeError("lesson payload must be a JSON object")
    if K_CLI_PUBLIC_LESSON_FIELDS.intersection(lesson_payload):
        ExportedLesson.model_validate(lesson_payload)
    else:
        Lesson.model_validate(lesson_payload)


def _lesson_validate(args: argparse.Namespace) -> int:
    failed = False
    for file_name in args.require_files:
        path = Path(file_name)
        try:
            _validate_lesson_file(path)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            failed = True
            print(f"{path}: invalid: {exc}", file=sys.stderr)
        else:
            print(f"{path}: ok")
    return 1 if failed else 0


__all__ = ["add_lesson_validation_commands"]
