"""Entry point: `prepare_source_package` application behavior."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.prepare_source_package import prepare_source_package
from lesson_builder.domain.lesson.models.terminology import TerminologyBans

K_PREPARE_SOURCE_PACKAGE_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "catalog_package" / "fixture"


def test_prepare_source_package_given_valid_source_expect_values_without_workflow_sidecars(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    shutil.copytree(K_PREPARE_SOURCE_PACKAGE_FIXTURE, source_dir)

    prepared = prepare_source_package(source_dir, terminology_bans=TerminologyBans())

    assert prepared.export_doc["artifact_id"] == "lesson:question_word_order"
    assert prepared.export_hash.startswith("sha256:")
    assert prepared.exercise_diagnostics["total_exercises"] > 0
    assert prepared.transcript_document["blocks"]
    assert not (source_dir / "exercise_diagnostics.json").exists()
    assert not (source_dir / "transcript.yaml").exists()


def test_prepare_source_package_given_file_changes_after_read_expect_captured_snapshot_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    shutil.copytree(K_PREPARE_SOURCE_PACKAGE_FIXTURE, source_dir)
    module = importlib.import_module("lesson_builder.application.operations.prepare_source_package")
    original_load = module.load_lesson_source

    def load_captured_source(lesson_text: str, exercises_text: str, *, audit):
        lesson_path = source_dir / "lesson.md"
        lesson_path.write_text(
            lesson_path.read_text(encoding="utf-8").replace(
                "Du snakker norsk.",
                "Du snakker norsk i dag.",
            ),
            encoding="utf-8",
        )
        return original_load(lesson_text, exercises_text, audit=audit)

    monkeypatch.setattr(module, "load_lesson_source", load_captured_source)

    prepared = prepare_source_package(source_dir, terminology_bans=TerminologyBans())

    transcript_texts = [block["text"] for block in prepared.transcript_document["blocks"]]
    assert "Du snakker norsk." in transcript_texts
    assert "Du snakker norsk i dag." not in transcript_texts


def test_prepare_source_package_given_plan_changes_after_read_expect_captured_plan_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_dir = tmp_path / "source"
    shutil.copytree(K_PREPARE_SOURCE_PACKAGE_FIXTURE, source_dir)
    module = importlib.import_module("lesson_builder.application.operations.prepare_source_package")
    original_audit = module.audit_source_text

    def audit_captured_source(exercises_text: str, *, lesson_text: str):
        plan_path = source_dir / "plan.md"
        plan_path.write_text(
            plan_path.read_text(encoding="utf-8").replace("kind: grammar", "kind: vocabulary"),
            encoding="utf-8",
        )
        return original_audit(exercises_text, lesson_text=lesson_text)

    monkeypatch.setattr(module, "audit_source_text", audit_captured_source)

    prepared = prepare_source_package(source_dir, terminology_bans=TerminologyBans())

    assert prepared.export_doc["kind"] == "grammar"
