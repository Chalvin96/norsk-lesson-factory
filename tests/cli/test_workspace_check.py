"""Behavior tests for the lesson-data check command contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.cli import main
from tests.workspace.builders import assemble_valid_workspace
from tests.workspace.builders import workspace_snapshot


def test_workspace_check_command_given_complete_workspace_expect_single_json_document_and_zero_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assemble_valid_workspace(tmp_path)
    snapshot_before = workspace_snapshot(tmp_path)

    exit_code = main(["check", "--workspace-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["status"] == "valid"
    assert payload["read_only"] is True
    assert payload["workspace_root"] == str(tmp_path.resolve())
    assert payload["counts"] == {"passed": 5, "failed": 0, "skipped": 0, "error": 0}
    assert [check["check_id"] for check in payload["checks"]] == [
        "content_layout",
        "character_registry",
        "terminology_registry",
        "curriculum_plan",
        "distribution",
    ]
    assert workspace_snapshot(tmp_path) == snapshot_before


def test_workspace_check_command_given_missing_content_expect_exit_one_and_clean_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["check", "--workspace-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["status"] == "invalid"
    assert [check["check_id"] for check in payload["checks"] if check["status"] == "failed"] == ["content_layout"]
    assert [check["check_id"] for check in payload["checks"] if check["status"] == "skipped"] == [
        "character_registry",
        "terminology_registry",
        "curriculum_plan",
        "distribution",
    ]


def test_workspace_check_command_given_unexpected_runtime_error_expect_exit_three_and_stderr_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assemble_valid_workspace(tmp_path)

    def _raise_runtime_error(repo_root: Path) -> object:
        raise RuntimeError("unexpected harness failure")

    monkeypatch.setattr(
        "lesson_builder.application.operations.check_lesson_data.load_character_registry",
        _raise_runtime_error,
    )
    exit_code = main(["check", "--workspace-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 3
    payload = json.loads(captured.out)
    assert payload["status"] == "incomplete"
    assert payload["counts"]["error"] == 1
    assert "RuntimeError" in captured.err


def test_workspace_check_command_given_text_format_expect_human_readable_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assemble_valid_workspace(tmp_path)

    exit_code = main(["check", "--workspace-root", str(tmp_path), "--format", "text"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("workspace: ")
    assert "content_layout: passed" in captured.out
    assert "distribution: passed" in captured.out
    with pytest.raises(json.JSONDecodeError):
        json.loads(captured.out)
