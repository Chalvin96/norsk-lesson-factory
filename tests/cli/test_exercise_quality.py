"""Behavior tests for the public exercise lint command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lesson_builder.cli import main
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the catalog fixture to a disposable source directory."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    for name in ("lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source_dir / name)
    return source_dir


def _run_lint(source_dir: Path, *, fix: bool = False) -> int:
    """Invoke the public CLI route for one lint invocation."""
    return main(["exercise", "lint", str(source_dir), *(["--fix"] if fix else [])])


def test_exercise_lint_given_clean_source_expect_zero_and_clean_audit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A clean source package passes without invoking a model or writing files."""
    source_dir = _copy_fixture(tmp_path)

    exit_code = _run_lint(source_dir)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "check"
    assert output["repairs"] == []
    assert output["audit"]["status"] == "clean"


def test_exercise_lint_given_punctuation_spacing_expect_blocking_without_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Read-only lint reports malformed keyed recall punctuation and preserves source."""
    source_dir = _copy_fixture(tmp_path)
    exercises_path = source_dir / "exercises.yaml"
    original = exercises_path.read_text(encoding="utf-8")
    malformed = original.replace('text_md: " du norsk?"', 'text_md: " du norsk ?"', 1)
    exercises_path.write_text(malformed, encoding="utf-8")

    exit_code = _run_lint(source_dir)

    assert exit_code == 1
    assert exercises_path.read_text(encoding="utf-8") == malformed
    output = json.loads(capsys.readouterr().out)
    assert output["audit"]["status"] == "blocked"
    assert any(finding["code"] == "recall-rendered-punctuation" for finding in output["audit"]["findings"])


def test_exercise_lint_given_punctuation_spacing_with_fix_expect_clean_repaired_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Explicit fix mode applies only the safe punctuation-spacing repair."""
    source_dir = _copy_fixture(tmp_path)
    exercises_path = source_dir / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace('text_md: " du norsk?"', 'text_md: " du norsk ?"', 1),
        encoding="utf-8",
    )

    exit_code = _run_lint(source_dir, fix=True)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "fix"
    assert any(repair["code"] == "recall-punctuation-spacing" for repair in output["repairs"])
    assert " du norsk ?" not in exercises_path.read_text(encoding="utf-8")
    assert output["audit"]["status"] == "clean"
