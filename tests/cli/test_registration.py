"""Behavior tests for lesson-data parser registration and lazy command routing."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from lesson_builder.cli import build_parser

K_PARSER_LAZY_IMPORT_PROBE = (
    "import json, sys; "
    "from lesson_builder.cli import build_parser; "
    "build_parser(); "
    "print(json.dumps(sorted(name for name in sys.modules if name.startswith('lesson_builder.'))))"
)

K_PARSER_ROUTES: list[tuple[list[str], dict[str, object]]] = [
    (
        ["lesson", "validate", "--require-files", "a.json", "b.json"],
        {"command": "lesson", "lesson_command": "validate", "require_files": ["a.json", "b.json"]},
    ),
    (["terminology", "audit", "dist/lessons/a.json", "--summary"], {"terminology_command": "audit", "summary": True}),
    (["terminology", "list"], {"terminology_command": "list"}),
    (["terminology", "ban", "offending phrase"], {"terminology_command": "ban", "phrase": "offending phrase"}),
    (["terminology", "unban", "offending phrase"], {"terminology_command": "unban", "phrase": "offending phrase"}),
    (
        ["exercise", "lint", "content/lessons/alpha", "--fix"],
        {"exercise_command": "lint", "source_dir": "content/lessons/alpha", "fix": True},
    ),
    (["catalog", "travel"], {"category": "travel", "guidance": "", "cefr": [], "max_iterations": 3}),
    (
        ["generate-lessons", "--plan", "content/curriculum/plan.yaml", "--batch-id", "batch-1"],
        {"job": "author", "workers": 6, "catalog_id": None},
    ),
    (["promote-lessons", "--source-batch", "batch.yaml"], {"auto_approve": False, "replace": False, "approval": None}),
    (
        ["recompile-lessons", "--plan", "plan.yaml", "--source-batch", "old.yaml", "--batch-id", "batch-2"],
        {"source_batch": "old.yaml", "batch_id": "batch-2"},
    ),
    (["resume-lessons", "--source-batch", "old.yaml", "--batch-id", "batch-3"], {"job": None, "workers": None}),
    (["curriculum", "catalog", "--auto-approve"], {"text": "catalog", "auto_approve": True, "check": False}),
    (["curriculum", "dependencies"], {"text": "dependencies", "from_review": None}),
    (
        ["curriculum", "promote-dependencies", "--from-review", "review.yaml", "--approval", "approval.yaml"],
        {"text": "promote-dependencies", "approval": "approval.yaml"},
    ),
    (["regenerate-dist", "--repo-root", "."], {"repo_root": "."}),
    (["export", "--repo-root", "."], {"workers": 1}),
    (["validate-distribution", "--repo-root", "."], {"distribution_root": None}),
    (["check", "--workspace-root", "."], {"workspace_root": ".", "format": "json"}),
    (
        ["package", "--distribution-root", "distribution", "--output", "lessons.tar.gz"],
        {"distribution_root": "distribution", "output": "lessons.tar.gz"},
    ),
    (
        ["publish", "lessons.tar.gz", "--distribution-root", "distribution", "--s3"],
        {"archive": "lessons.tar.gz", "s3": True, "github_tag": None},
    ),
]

K_CLI_COMMAND_ORDER = (
    "lesson",
    "terminology",
    "catalog",
    "generate-lessons",
    "promote-lessons",
    "recompile-lessons",
    "resume-lessons",
    "curriculum",
    "exercise",
    "regenerate-dist",
    "export",
    "validate-distribution",
    "check",
    "package",
    "publish",
    "doctor",
    "preview",
)


@pytest.mark.parametrize(("argv", "expected_namespace"), K_PARSER_ROUTES)
def test_parser_registration_given_command_route_argv_expect_bound_handler_and_namespace(
    argv: list[str], expected_namespace: dict[str, object]
) -> None:
    """Every registered route parses to a bound handler with unchanged defaults."""
    args = build_parser().parse_args(argv)

    assert callable(args.func)
    assert {key: getattr(args, key) for key in expected_namespace} == expected_namespace


def test_parser_registration_given_parser_construction_expect_lazy_domain_modules_not_imported() -> None:
    """Building the parser loads no workflow, provider, or publication runtime."""
    result = subprocess.run(
        [sys.executable, "-c", K_PARSER_LAZY_IMPORT_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    forbidden = [
        name
        for name in loaded
        if name.startswith(("lesson_builder.clients", "lesson_builder.workflow", "lesson_builder.domain.distribution"))
    ]
    assert forbidden == []


def test_parser_registration_given_build_parser_expect_stable_command_order() -> None:
    """Keep the existing help order while command families are split by function."""
    parser = build_parser()
    action = next(action for action in parser._actions if action.dest == "command")

    assert tuple(action.choices) == K_CLI_COMMAND_ORDER
