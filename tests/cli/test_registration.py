"""Behavior tests for retained lesson-data commands and parser laziness."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from lesson_builder.cli import build_parser
from lesson_builder.cli import main

K_PARSER_LAZY_IMPORT_PROBE = (
    "import json, sys; "
    "from lesson_builder.cli import build_parser; "
    "build_parser(); "
    "print(json.dumps(sorted(name for name in sys.modules if name.startswith('lesson_builder.'))))"
)
K_CLI_SUPPORTED_COMMANDS = ("check", "regenerate-dist", "release", "release-verify", "workbench")
K_CLI_REMOVED_COMMANDS = (
    "lesson",
    "terminology",
    "catalog",
    "curriculum",
    "exercise",
    "export",
    "validate-distribution",
    "package",
    "publish",
    "doctor",
    "preview",
)


@pytest.mark.parametrize(
    ("argv", "expected_namespace"),
    [
        (
            ["workbench", "--no-browser"],
            {"command": "workbench", "host": "127.0.0.1", "port": 0, "no_browser": True},
        ),
        (["regenerate-dist", "--repo-root", "."], {"repo_root": "."}),
        (
            ["release", "--repo-root", "."],
            {"approve": False, "tag": None, "max_cost": None, "format": "text"},
        ),
        (["release", "--approve", "--tag", "v2026.09.14"], {"approve": True, "tag": "v2026.09.14"}),
        (
            ["release-verify", "--tag", "v2026.09.14", "--github-repository", "owner/repo"],
            {"tag": "v2026.09.14", "github_repository": "owner/repo", "format": "text"},
        ),
        (["check", "--workspace-root", "."], {"workspace_root": ".", "format": "json"}),
    ],
)
def test_cli_parser_given_supported_command_expect_bound_handler_and_namespace(
    argv: list[str], expected_namespace: dict[str, object]
) -> None:
    """Every retained route parses to a bound handler with expected defaults."""
    args = build_parser().parse_args(argv)

    assert callable(args.func)
    assert {key: getattr(args, key) for key in expected_namespace} == expected_namespace


def test_cli_parser_given_parser_construction_expect_lazy_domain_modules_not_imported() -> None:
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


def test_cli_help_given_top_level_invocation_expect_exact_supported_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Top-level help advertises only the supported command set."""
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    help_text = capsys.readouterr().out
    usage = help_text.split("positional arguments:", 1)[0]
    command_list = re.search(r"\{([^}]*)\}", usage, re.DOTALL)
    assert command_list is not None
    listed = tuple(command.strip() for command in command_list.group(1).split(","))
    assert listed == K_CLI_SUPPORTED_COMMANDS


@pytest.mark.parametrize("command", K_CLI_REMOVED_COMMANDS)
def test_cli_parser_given_removed_top_level_command_expect_argparse_error(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removed command names fail through argparse's normal choice error."""
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args([command])

    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_workbench_given_occupied_port_expect_one_line_error_and_exit_one(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from http.server import HTTPServer

    monkeypatch.setattr("lesson_builder.application.operations.workbench_build.ensure_workbench_app", lambda root: None)
    with HTTPServer(("127.0.0.1", 0), type("Handler", (), {})) as occupied:
        port = occupied.server_port
        result = main(["workbench", "--workspace-root", str(tmp_path), "--port", str(port), "--no-browser"])
    assert result == 1
    assert capsys.readouterr().err == (
        f"lesson-data workbench: port {port} is already in use; choose another with --port or use --port 0\n"
    )
