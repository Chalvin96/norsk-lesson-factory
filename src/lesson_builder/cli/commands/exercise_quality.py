"""Entry point: `add_exercise_commands` registers deterministic exercise linting.

The command audits authored Markdown/YAML packages and applies only safe,
mechanical source repairs. Model-backed lesson generation and review belong to
the catalog-generation workflow, not this command family.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lesson_builder.cli.commands.registration import SubparserRegistrar


def add_exercise_commands(parent: SubparserRegistrar) -> None:
    """Register deterministic exercise-source linting and safe transport repair."""
    parser = parent.add_parser(
        "exercise",
        help="Lint authored exercise YAML without invoking a model",
    )
    subparsers = parser.add_subparsers(dest="exercise_command", required=True)
    lint_parser = subparsers.add_parser(
        "lint",
        help="Audit one lesson.md/exercises.yaml source directory",
    )
    lint_parser.add_argument("source_dir", help="directory containing lesson.md and exercises.yaml")
    lint_parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "apply only deterministic transport repairs (quoting, aliases, IDs, and "
            "recall punctuation spacing) before auditing"
        ),
    )
    lint_parser.set_defaults(func=run_exercise_lint)


def run_exercise_lint(args: argparse.Namespace) -> int:
    """Run the deterministic exercise audit and optional safe source repair."""
    from lesson_builder.application.operations.audit_source import audit_source_directory
    from lesson_builder.application.operations.repair_source import repair_exercise_source

    source_dir = Path(args.source_dir)
    repairs: list[dict[str, Any]] = []
    if args.fix:
        lesson_path = source_dir / "lesson.md"
        exercises_path = source_dir / "exercises.yaml"
        try:
            repair = repair_exercise_source(
                exercises_path.read_text(encoding="utf-8"),
                lesson_text=lesson_path.read_text(encoding="utf-8"),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"error: exercise source repair failed: {exc}")
            return 1
        if repair.changed:
            try:
                exercises_path.write_text(repair.exercises_yaml, encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                print(f"error: could not write repaired exercises.yaml: {exc}")
                return 1
        repairs = [item.model_dump(mode="json") for item in repair.repairs]

    try:
        audit = audit_source_directory(source_dir)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: exercise source audit failed: {exc}")
        return 1
    payload = {
        "source_dir": str(source_dir),
        "mode": "fix" if args.fix else "check",
        "repairs": repairs,
        "audit": audit.model_dump(mode="json"),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if audit.material_findings else 0


__all__ = ["add_exercise_commands", "run_exercise_lint"]
