"""Behavior tests for the canonical strict and collecting lesson compiler."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.compile_lesson_package import collect_lesson_package
from lesson_builder.application.operations.compile_lesson_package import compile_lesson_package
from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_compile_lesson_package_given_clean_source_expect_valid_compilation(tmp_path: Path):
    source = _copy_fixture(tmp_path)

    compiled = compile_lesson_package(source, terminology_bans=TerminologyBans())

    assert compiled.is_valid is True
    assert compiled.lesson is not None
    assert compiled.audit.material_findings == []
    assert compiled.errors == ()


def test_compile_lesson_package_given_clean_source_expect_one_exercise_yaml_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_fixture(tmp_path)
    module = importlib.import_module("lesson_builder.application.operations.load_exercises")
    original_parse = module.load_unique_yaml
    parse_calls: list[str] = []

    def count_parse(text: str):
        parse_calls.append(text)
        return original_parse(text)

    monkeypatch.setattr(module, "load_unique_yaml", count_parse)

    compiled = compile_lesson_package(source, terminology_bans=TerminologyBans())

    assert compiled.is_valid is True
    assert len(parse_calls) == 1


def test_compile_lesson_package_given_source_audit_finding_expect_strict_rejection(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "- no: Jeg jobber hjemme i dag.", "- no: Jeg jobber hjemme but i dag."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="norwegian-example-english-conjunction"):
        compile_lesson_package(source, terminology_bans=TerminologyBans())


def test_collect_lesson_package_given_source_audit_finding_expect_readable_invalid_result(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "- no: Jeg jobber hjemme i dag.", "- no: Jeg jobber hjemme but i dag."
        ),
        encoding="utf-8",
    )

    compiled = collect_lesson_package(source, terminology_bans=TerminologyBans())

    assert compiled.is_valid is False
    assert compiled.lesson is not None
    assert any(finding.code == "norwegian-example-english-conjunction" for finding in compiled.audit.findings)


def test_compile_lesson_package_given_blocking_gate_result_expect_strict_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = _copy_fixture(tmp_path)
    blocking_result = CheckResult(
        check_id="test_gate",
        severity="blocker",
        unit_id="question_word_order",
        message="test gate blocked the package",
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.compile_lesson_package.gate_lesson_results",
        lambda *args, **kwargs: [blocking_result],
    )

    with pytest.raises(ValueError, match="deterministic gate blocked lesson-package compilation"):
        compile_lesson_package(source, terminology_bans=TerminologyBans())


def test_compile_lesson_package_given_non_norwegian_recall_target_expect_strict_rejection(tmp_path: Path) -> None:
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            'audio_target: "Snakker du norsk?"',
            'audio_target: "Please snakker du norsk?"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="recall_audio_language"):
        compile_lesson_package(source, terminology_bans=TerminologyBans())


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the clean catalog package into a disposable directory."""
    source = tmp_path / "source"
    source.mkdir()
    for filename in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / filename, source / filename)
    return source
