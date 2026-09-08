"""Behavior tests for the public lesson-file validation command."""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.cli import main


def test_lesson_validate_given_v3_export_payload_expect_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    payload_path = tmp_path / "v3.json"
    payload_path.write_text('{"schema_version": "3.0"}\n', encoding="utf-8")

    result = main(["lesson", "validate", "--require-files", str(payload_path)])

    assert result == 1
    assert "invalid" in capsys.readouterr().err


def test_lesson_validate_given_unversioned_public_payload_expect_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    payload_path = tmp_path / "unversioned.json"
    payload_path.write_text(
        '{"id": "demo", "sections": [], "exercises": [], "practice_groups": [], "media": {"audio": []}}\n',
        encoding="utf-8",
    )

    result = main(["lesson", "validate", "--require-files", str(payload_path)])

    assert result == 1
    assert "invalid" in capsys.readouterr().err


def test_lesson_validate_given_non_object_json_expect_rejected_without_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    payload_path = tmp_path / "list.json"
    payload_path.write_text("[]\n", encoding="utf-8")

    result = main(["lesson", "validate", "--require-files", str(payload_path)])

    assert result == 1
    assert "lesson payload must be a JSON object" in capsys.readouterr().err
