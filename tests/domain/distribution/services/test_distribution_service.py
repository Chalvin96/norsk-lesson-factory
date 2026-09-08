"""Behavior tests for distribution projection and complete export assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lesson_builder.application.operations.export_distribution import create_lesson_packet_from_source
from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.cli import main
from lesson_builder.domain.lesson.models.export import ExportedLesson
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_create_lesson_packet_from_source_given_source_package_expect_lean_packet(tmp_path: Path):
    package = _copy_fixture(tmp_path)

    packet, metadata = create_lesson_packet_from_source(package)

    ExportedLesson.model_validate(packet)
    assert metadata["kind"] == "grammar"
    assert packet["schema_version"] == "4.0"
    assert packet["sections"]
    assert packet["exercises"]
    assert packet["content"]
    assert packet["media"] == {"audio": []}
    assert all("bloom" not in exercise for exercise in packet["exercises"])
    assert all("derived_from" not in exercise for exercise in packet["exercises"])


def test_create_lesson_packet_from_source_given_missing_kind_expect_explicit_error(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    plan_path = package / "plan.md"
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("kind: grammar\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must contain a non-empty kind"):
        create_lesson_packet_from_source(package)


def test_export_distribution_given_source_package_expect_packet_catalog_and_schema(tmp_path: Path):
    _copy_fixture(tmp_path)

    first = export_distribution(tmp_path)
    second = export_distribution(tmp_path)

    catalog_path = tmp_path / "dist" / "catalog.json"
    schema_path = tmp_path / "dist" / "schema" / "lesson.schema.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert first["created"] == 1
    assert second["unchanged"] == 1
    assert first["schema_path"] == str(schema_path)
    assert first["catalog_updated"] == 1
    assert second["catalog_unchanged"] == 1
    assert catalog == {"lessons": [{"lesson_id": "question_word_order", "position": 0}]}
    assert not (tmp_path / "dist" / "manifest.json").exists()


def test_export_distribution_given_family_metadata_expect_catalog_only(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    package_plan = package / "plan.md"
    package_plan.write_text(
        package_plan.read_text(encoding="utf-8").replace(
            "kind: grammar\n", "kind: grammar\nfamily_id: grammar-family\n"
        ),
        encoding="utf-8",
    )

    export_distribution(tmp_path)

    packet = json.loads((tmp_path / "dist" / "lessons" / "question_word_order.json").read_text(encoding="utf-8"))
    catalog = json.loads((tmp_path / "dist" / "catalog.json").read_text(encoding="utf-8"))
    assert "family_id" not in packet
    assert catalog == {"lessons": [{"lesson_id": "question_word_order", "position": 0, "family_id": "grammar-family"}]}
    assert set(catalog["lessons"][0]) == {"lesson_id", "position", "family_id"}


def test_export_distribution_given_same_source_in_two_roots_expect_deterministic_packets(tmp_path: Path):
    _copy_fixture(tmp_path)
    first_root = tmp_path / "distribution-one"
    second_root = tmp_path / "distribution-two"

    export_distribution(tmp_path, output_root=first_root)
    export_distribution(tmp_path, output_root=second_root)

    first_packet = first_root / "dist" / "lessons" / "question_word_order.json"
    second_packet = second_root / "dist" / "lessons" / "question_word_order.json"
    assert first_packet.stem == json.loads(first_packet.read_text(encoding="utf-8"))["id"]
    assert first_packet.read_bytes() == second_packet.read_bytes()
    assert (first_root / "dist" / "catalog.json").read_bytes() == (second_root / "dist" / "catalog.json").read_bytes()


def test_exported_lesson_given_catalog_metadata_field_expect_invalid_packet_rejected(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    packet, _metadata = create_lesson_packet_from_source(package)
    packet["family_id"] = "grammar-family"

    with pytest.raises(ValueError, match="extra"):
        ExportedLesson.model_validate(packet)


def test_exported_lesson_given_unknown_content_reference_expect_invalid_packet_rejected(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    packet, _metadata = create_lesson_packet_from_source(package)
    packet["content"][0]["id"] = "missing-content-id"

    with pytest.raises(ValueError, match="content references must resolve"):
        ExportedLesson.model_validate(packet)


def test_exported_lesson_given_duplicate_choose_option_id_expect_invalid_packet_rejected(tmp_path: Path):
    package = _copy_fixture(tmp_path)
    packet, _metadata = create_lesson_packet_from_source(package)
    choose = next(exercise for exercise in packet["exercises"] if exercise["operation"] == "choose")
    choose["payload"]["options"][1]["option_id"] = choose["payload"]["options"][0]["option_id"]

    with pytest.raises(ValueError, match="duplicate option id"):
        ExportedLesson.model_validate(packet)


def test_export_distribution_given_later_package_failure_expect_distribution_unchanged(tmp_path: Path, monkeypatch):
    _copy_fixture(tmp_path, "first")
    _copy_fixture(tmp_path, "second")
    export_distribution(tmp_path)
    packet_path = tmp_path / "dist" / "lessons" / "first.json"
    catalog_path = tmp_path / "dist" / "catalog.json"
    packet_before = packet_path.read_bytes()
    catalog_before = catalog_path.read_bytes()

    original = create_lesson_packet_from_source

    def fail_on_second(source_dir: Path, **kwargs):
        if source_dir.name == "second":
            raise ValueError("broken source")
        return original(source_dir, **kwargs)

    monkeypatch.setattr(
        "lesson_builder.application.operations.export_distribution.create_lesson_packet_from_source",
        fail_on_second,
    )
    with pytest.raises(ValueError, match="broken source"):
        export_distribution(tmp_path)

    assert packet_path.read_bytes() == packet_before
    assert catalog_path.read_bytes() == catalog_before


def test_export_distribution_given_existing_audio_distribution_expect_explicit_error(tmp_path: Path):
    _copy_fixture(tmp_path)
    packet_path = tmp_path / "dist" / "lessons" / "question_word_order.json"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text(
        json.dumps({"media": {"audio": [{"id": "audio-1"}]}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot overwrite a distribution containing audio"):
        export_distribution(tmp_path)


def test_validate_distribution_command_given_current_complete_distribution_expect_success(tmp_path: Path, capsys):
    _copy_fixture(tmp_path)
    export_distribution(tmp_path)

    assert main(["validate-distribution", "--repo-root", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["lesson_count"] == 1


def _copy_fixture(repo: Path, lesson_id: str = "question_word_order") -> Path:
    package = repo / "content" / "lessons" / lesson_id
    package.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        source = (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        if lesson_id != "question_word_order":
            source = source.replace("question_word_order", lesson_id)
        (package / name).write_text(source, encoding="utf-8")
    _write_curriculum_plan(repo)
    return package


def _write_curriculum_plan(repo: Path) -> None:
    lesson_ids = sorted(path.name for path in (repo / "content" / "lessons").iterdir() if path.is_dir())
    slots = "".join(
        f"- sequence_index: {index}\n"
        f"  catalog_id: {lesson_id}\n"
        f"  catalog_kind: grammar\n"
        f"  title: {lesson_id}\n"
        f"  cefr_level: A1\n"
        f"  human_gate: approved\n"
        for index, lesson_id in enumerate(lesson_ids)
    )
    plan_path = repo / "content" / "curriculum" / "plan.yaml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(f"schema_version: 0.5-catalog-plan\nslots:\n{slots}", encoding="utf-8")
