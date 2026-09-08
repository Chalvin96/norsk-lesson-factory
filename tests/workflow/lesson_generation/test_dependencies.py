"""Tests for the lesson-package default collaborators (copier, compiler, ledger)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lesson_builder.workflow.lesson_generation.dependencies import default_export_compiler
from lesson_builder.workflow.lesson_generation.dependencies import default_ledger_writer
from lesson_builder.workflow.lesson_generation.dependencies import default_source_copier
from lesson_builder.workflow.lesson_generation.dependencies import validate_approved_source
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_HASH_PREFIX
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REQUIRED_FILES
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_SOURCE_SUBDIR
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_TRANSCRIPT_FILE
from tests.paths import K_CARDINAL_NUMBERS_FIXTURE_ROOT
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def _append_speak_exercise(source_dir: Path) -> None:
    """Append one exact-production speak exercise to a copied source package."""
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "{{exercise: build-fronted-time}}",
            "{{exercise: build-fronted-time}}\n\n{{exercise: speak-conditional-target}}",
            1,
        ),
        encoding="utf-8",
    )
    exercises_path = source_dir / "exercises.yaml"
    exercises = yaml.safe_load(exercises_path.read_text(encoding="utf-8"))
    exercises.append(
        {
            "handle": "speak-conditional-target",
            "op": "speak",
            "objective": "obj-question-order",
            "bloom": "apply",
            "prompt_md": "Say exactly: Hvis timen ikke passer, kan jeg få en time på fredag.",
            "target": "Hvis timen ikke passer, kan jeg få en time på fredag.",
            "derived_from": [{"section_id": "sec-contrast"}],
        }
    )
    exercises_path.write_text(
        yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_default_source_copier_given_valid_fixture_expect_all_files_copied_and_hashed(tmp_path: Path):
    source_dir, files, source_hash = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )

    assert source_dir == tmp_path / K_LESSON_GENERATION_SOURCE_SUBDIR
    assert set(files) == set(K_LESSON_GENERATION_REQUIRED_FILES)
    assert source_hash.startswith(K_LESSON_GENERATION_HASH_PREFIX)
    for name in K_LESSON_GENERATION_REQUIRED_FILES:
        assert (source_dir / name).exists()


def test_default_source_copier_given_missing_fixture_file_expect_filenotfounderror(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="missing required file"):
        default_source_copier(fixture_source=tmp_path, output_root=tmp_path, run_id="r1")


def test_default_source_copier_given_repeated_copy_expect_same_hash(tmp_path: Path):
    _, _, hash_a = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path / "a", run_id="r1"
    )
    _, _, hash_b = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path / "b", run_id="r2"
    )

    assert hash_a == hash_b


def test_default_export_compiler_given_valid_fixture_expect_diagnostics_sidecar_not_export_field(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert "exercise_progression" not in export_doc
    diagnostics_path = source_dir / "exercise_diagnostics.json"
    assert diagnostics_path.is_file()
    assert json.loads(diagnostics_path.read_text(encoding="utf-8"))["total_exercises"] > 0


def test_default_export_compiler_given_valid_fixture_expect_no_audio_files_written(tmp_path: Path):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert not (tmp_path / "audio").exists()
    assert not (source_dir / "audio").exists()
    assert "audio_declarations" in export_doc


def test_default_export_compiler_given_three_file_fixture_expect_derived_transcript_and_audio(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )

    assert not (source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE).exists()
    export_doc, export_hash = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert export_doc["artifact_id"] == "lesson:question_word_order"
    assert "lesson" in export_doc
    assert "semantic_hash" in export_doc
    assert len(export_doc["transcript_ids"]) > 0
    assert all("Translation:" not in block["text"] for block in export_doc["transcript_blocks"])
    assert all({"id", "text", "origin", "source", "audio"} <= block.keys() for block in export_doc["transcript_blocks"])
    assert all(block["audio"] == {"role": "model", "language": "nb-NO"} for block in export_doc["transcript_blocks"])
    assert all("find-fix-modal-target" not in block["id"] for block in export_doc["transcript_blocks"])
    assert all("judge-fronted-time-target" not in block["id"] for block in export_doc["transcript_blocks"])
    assert all("Kan jeg får litt vann?" not in block["text"] for block in export_doc["transcript_blocks"])
    assert all("I dag jeg jobber hjemme." not in block["text"] for block in export_doc["transcript_blocks"])
    assert len(export_doc["audio_declarations"]) == len(export_doc["transcript_blocks"])
    assert all(
        declaration["audio_id"] == f"audio-{declaration['transcript_id']}"
        for declaration in export_doc["audio_declarations"]
    )
    assert export_doc["kind"] == "grammar"
    assert (source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE).exists()
    assert export_hash.startswith(K_LESSON_GENERATION_HASH_PREFIX)


def test_default_export_compiler_given_committed_cardinal_recall_target_expect_no_context_labels(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CARDINAL_NUMBERS_FIXTURE_ROOT,
        output_root=tmp_path,
        run_id="r1",
    )
    registry_path = tmp_path / "content" / "authoring" / "characters.yaml"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        "schema_version: 1\ncharacters:\n  jonas:\n    name: Jonas\n  mina:\n    name: Mina\n",
        encoding="utf-8",
    )

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    target_blocks = [
        block
        for block in export_doc["transcript_blocks"]
        if block["source"].get("exercise_id") == "number-sequence-recall"
    ]
    assert [block["text"] for block in target_blocks] == ["åtte bøker, tre epler, ti bord, seks egg og en kopp."]
    assert all("books" not in block["text"] and "apples" not in block["text"] for block in target_blocks)


def test_default_export_compiler_given_real_fixture_expect_exercises_parsed(tmp_path: Path):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    exercise_ids = {ex.get("id") for ex in export_doc["exercises"]}
    assert "identify-question" in exercise_ids
    assert "build-fronted-time" in exercise_ids


def test_default_export_compiler_given_intended_meaning_example_expect_skip_audio(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "- en: You speak Norwegian.",
            "- en: '*Intended meaning: You speak Norwegian.*'",
            1,
        ),
        encoding="utf-8",
    )

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert all(block["text"] != "Du snakker norsk." for block in export_doc["transcript_blocks"])


def test_default_export_compiler_given_pronunciation_speak_exercise_expect_norwegian_transcript_and_audio(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    plan_path = source_dir / "plan.md"
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("kind: grammar", "kind: pronunciation", 1),
        encoding="utf-8",
    )
    _append_speak_exercise(source_dir)

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    speak_exercise = next(ex for ex in export_doc["exercises"] if ex["id"] == "speak-conditional-target")
    assert speak_exercise["operation"] == "speak"
    assert speak_exercise["payload"]["target"] == "Hvis timen ikke passer, kan jeg få en time på fredag."
    target_blocks = [
        block
        for block in export_doc["transcript_blocks"]
        if block["source"].get("exercise_id") == "speak-conditional-target"
    ]
    assert len(target_blocks) == 1
    assert target_blocks[0]["text"] == "Hvis timen ikke passer, kan jeg få en time på fredag."
    assert target_blocks[0]["origin"] == "exercise_target"
    assert target_blocks[0]["audio"] == {"role": "model", "language": "nb-NO"}
    target_declarations = [
        declaration
        for declaration in export_doc["audio_declarations"]
        if declaration["transcript_id"] == target_blocks[0]["id"]
    ]
    assert len(target_declarations) == 1
    assert target_declarations[0]["audio_id"] == f"audio-{target_blocks[0]['id']}"
    assert target_declarations[0]["purpose"] == "exercise target"


def test_default_export_compiler_given_grammar_speak_with_oracle_expect_export_and_audio(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    _append_speak_exercise(source_dir)

    export_doc, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    speak_exercise = next(ex for ex in export_doc["exercises"] if ex["id"] == "speak-conditional-target")
    assert speak_exercise["operation"] == "speak"


def test_default_export_compiler_given_unrelated_inserted_paragraph_expect_candidates_and_audio_stable(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    first_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "::: examples\n- no: Du snakker norsk.",
            "This note does not change an example.\n\n::: examples\n- no: Du snakker norsk.",
            1,
        ),
        encoding="utf-8",
    )

    second_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert second_export["transcript_ids"] == first_export["transcript_ids"]
    assert second_export["dependency_hash"] == first_export["dependency_hash"]


def test_default_export_compiler_given_changed_exercise_target_expect_audio_identity_changes(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    _append_speak_exercise(source_dir)
    first_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")
    first_target = next(
        block
        for block in first_export["transcript_blocks"]
        if block["source"].get("exercise_id") == "speak-conditional-target"
    )
    first_declaration = next(
        declaration
        for declaration in first_export["audio_declarations"]
        if declaration["transcript_id"] == first_target["id"]
    )

    exercises_path = source_dir / "exercises.yaml"
    exercises = yaml.safe_load(exercises_path.read_text(encoding="utf-8"))
    speak_exercise = next(exercise for exercise in exercises if exercise["handle"] == "speak-conditional-target")
    speak_exercise["prompt_md"] = "Say exactly: Hvis timen ikke passer, kan jeg få en time på mandag."
    speak_exercise["target"] = "Hvis timen ikke passer, kan jeg få en time på mandag."
    exercises_path.write_text(yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False), encoding="utf-8")

    second_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")
    second_target = next(
        block
        for block in second_export["transcript_blocks"]
        if block["source"].get("exercise_id") == "speak-conditional-target"
    )
    second_declaration = next(
        declaration
        for declaration in second_export["audio_declarations"]
        if declaration["transcript_id"] == second_target["id"]
    )

    assert second_target["text"] != first_target["text"]
    assert second_target["id"] != first_target["id"]
    assert second_declaration["audio_id"] != first_declaration["audio_id"]


def test_default_export_compiler_given_package_without_exercises_expect_value_error(tmp_path: Path):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    exercises_path = source_dir / "exercises.yaml"
    exercises_path.write_text("[]\n", encoding="utf-8")
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        "\n".join(
            line for line in lesson_path.read_text(encoding="utf-8").splitlines() if not line.startswith("{{exercise:")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least one exercise"):
        default_export_compiler(source_dir=source_dir, run_id="r1")


def test_default_export_compiler_given_duplicate_yaml_key_expect_mechanical_preflight_block(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    exercises_path = source_dir / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "- handle: identify-question\n",
            "- handle: identify-question\n  handle: overwritten-question\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mechanical exercise preflight"):
        default_export_compiler(source_dir=source_dir, run_id="r1")


def test_default_export_compiler_given_major_build_target_finding_expect_preflight_block(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    exercises_path = source_dir / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("text: hjemme.", "text: hjemme", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="build-answer-punctuation-missing"):
        default_export_compiler(source_dir=source_dir, run_id="r1")


def test_default_export_compiler_given_stale_transcript_yaml_expect_rederived_blocks(tmp_path: Path):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    first_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")
    transcript_path = source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE
    transcript_path.write_text(
        yaml.safe_dump(
            {"blocks": [{"id": "stale", "text": "Dette skal erstattes."}]},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    second_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert second_export["transcript_ids"] == first_export["transcript_ids"]
    assert second_export["transcript_ids"] != ["stale"]


def test_default_export_compiler_given_derived_output_expect_no_translation_fields(tmp_path: Path):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    default_export_compiler(source_dir=source_dir, run_id="r1")
    transcript_path = source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE
    transcript = yaml.safe_load(transcript_path.read_text(encoding="utf-8"))

    assert all("translation" not in block and "en" not in block for block in transcript["blocks"])


def test_default_export_compiler_given_changed_lesson_example_expect_dependency_hash_changes(
    tmp_path: Path,
):
    source_dir, _, _ = default_source_copier(
        fixture_source=K_CATALOG_PACKAGE_FIXTURE_ROOT, output_root=tmp_path, run_id="r1"
    )
    first_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")
    lesson_path = source_dir / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace("Du snakker norsk.", "Du snakker norsk i dag."),
        encoding="utf-8",
    )

    second_export, _ = default_export_compiler(source_dir=source_dir, run_id="r1")

    assert first_export["dependency_hash"] != second_export["dependency_hash"]


def test_default_ledger_writer_given_valid_export_expect_appended_jsonl(tmp_path: Path):
    ledger_path = tmp_path / "ledger.jsonl"
    export_doc = {"course_id": "test", "export_hash": "sha256:abc"}

    entry = default_ledger_writer(ledger_path=ledger_path, run_id="r1", export_doc=export_doc, source_hash="sha256:src")

    assert entry["run_id"] == "r1"
    assert entry["status"] == "accepted"
    lines = ledger_path.read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "r1"


def test_default_ledger_writer_given_two_writes_expect_two_lines(tmp_path: Path):
    ledger_path = tmp_path / "ledger.jsonl"

    default_ledger_writer(ledger_path=ledger_path, run_id="r1", export_doc={"export_hash": "h1"}, source_hash="s1")
    default_ledger_writer(ledger_path=ledger_path, run_id="r2", export_doc={"export_hash": "h2"}, source_hash="s2")

    lines = [line for line in ledger_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2


def test_default_ledger_writer_given_changed_export_hash_expect_new_record(tmp_path: Path):
    ledger_path = tmp_path / "ledger.jsonl"
    default_ledger_writer(
        ledger_path=ledger_path,
        run_id="r1",
        export_doc={"artifact_id": "lesson:x", "export_hash": "h1"},
        source_hash="s1",
    )
    default_ledger_writer(
        ledger_path=ledger_path,
        run_id="r1",
        export_doc={"artifact_id": "lesson:x", "export_hash": "h2"},
        source_hash="s1",
    )

    lines = [line for line in ledger_path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2


def test_validate_approved_source_given_missing_authoring_file_expect_actionable_error(tmp_path: Path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    for name in ("plan.md", "lesson.md"):
        (fixture / name).write_text("---\n---\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="exercises.yaml"):
        validate_approved_source(fixture)


def test_validate_approved_source_given_unapproved_plan_expect_value_error(tmp_path: Path):
    fixture = tmp_path / "fixture"
    import shutil

    shutil.copytree(K_CATALOG_PACKAGE_FIXTURE_ROOT, fixture)
    plan_path = fixture / "plan.md"
    plan_path.write_text(plan_path.read_text().replace("approved: true", "approved: false"), encoding="utf-8")

    with pytest.raises(ValueError, match="approved: true"):
        validate_approved_source(fixture)


def test_validate_approved_source_given_stale_approval_hash_expect_value_error(tmp_path: Path):
    fixture = tmp_path / "fixture"
    import shutil

    shutil.copytree(K_CATALOG_PACKAGE_FIXTURE_ROOT, fixture)
    lesson_path = fixture / "lesson.md"
    lesson_path.write_text(lesson_path.read_text() + "\nChanged locally.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="approved_source_hash"):
        validate_approved_source(fixture)
