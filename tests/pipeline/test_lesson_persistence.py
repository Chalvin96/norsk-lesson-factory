"""Tests for the export/persistence sink: ``default_exporter``.

Validates the load-bearing ordering of the durable sink (validate -> write
dist/internal -> ledger -> manifest), the idempotency no-op on unchanged
content, and the manifest upsert behavior. Each test writes to ``tmp_path``
only; no repo-tree writes.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from lesson_builder.pipeline.lesson_persistence import default_exporter

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ordinal_lesson() -> dict[str, Any]:
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def _ordinal_requirements() -> dict[str, Any]:
    return json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text())


def _write_tmp_repo_with_ordinal_lesson(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path
    lesson_path = repo_root / "data" / "lessons" / "ordinal_numbers.json"
    requirements_path = repo_root / "data" / "concept_requirements" / "ordinal_numbers.json"
    lesson_path.parent.mkdir(parents=True, exist_ok=True)
    requirements_path.parent.mkdir(parents=True, exist_ok=True)
    lesson_path.write_text(json.dumps(_ordinal_lesson(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    requirements_path.write_text(
        json.dumps(_ordinal_requirements(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    acceptance_log_path = repo_root / "data" / "lesson_acceptance_log.jsonl"
    return repo_root, acceptance_log_path


# ---------------------------------------------------------------------------
# default_exporter: validate-before-write, no corruption on validation failure
# ---------------------------------------------------------------------------


def test_default_exporter_given_invalid_lesson_expect_internal_store_unchanged(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    internal_path = repo_root / "data" / "lessons" / "ordinal_numbers.json"
    export_path = repo_root / "dist" / "lessons" / "ordinal_numbers.json"
    prior_internal_text = internal_path.read_text(encoding="utf-8")

    invalid_lesson = deepcopy(_ordinal_lesson())
    del invalid_lesson["objectives"]  # required field -> _validate_lesson raises

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError, kept generic
        default_exporter(
            slug="ordinal_numbers",
            lesson=invalid_lesson,
            run_id="run-invalid",
            signoff_score=None,
            blocking_issues_messages=[],
            corrections_applied=[],
            repo_root=repo_root,
            acceptance_log_path=acceptance_log_path,
        )

    # internal store untouched -- validation ran before any write
    assert internal_path.read_text(encoding="utf-8") == prior_internal_text
    # no dist file, no ledger entry
    assert not export_path.exists()
    assert not acceptance_log_path.exists()


def test_default_exporter_given_valid_lesson_expect_dist_then_internal_then_ledger(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    internal_path = repo_root / "data" / "lessons" / "ordinal_numbers.json"
    export_path = repo_root / "dist" / "lessons" / "ordinal_numbers.json"

    result = default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-valid",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    assert result == "dist/lessons/ordinal_numbers.json"
    assert internal_path.exists()
    assert export_path.exists()
    log_lines = acceptance_log_path.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    assert json.loads(log_lines[0])["status"] == "accepted"


def test_default_exporter_given_override_expect_accepted_override_status(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-override",
        signoff_score=None,
        blocking_issues_messages=["unresolved blocker"],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
        override=True,
    )

    log_lines = acceptance_log_path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(log_lines[0])
    assert entry["status"] == "accepted_override"
    assert entry["blocking_issues"] == ["unresolved blocker"]


# ---------------------------------------------------------------------------
# default_exporter: idempotency (design §12) -- unchanged content re-export is
# a no-op (no duplicate ledger line, no re-write), and the manifest is updated
# exactly once per real export.
# ---------------------------------------------------------------------------


def test_default_exporter_given_unchanged_content_exported_twice_expect_single_ledger_line(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    lesson = _ordinal_lesson()

    first_path = default_exporter(
        slug="ordinal_numbers",
        lesson=lesson,
        run_id="run-1",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )
    second_path = default_exporter(
        slug="ordinal_numbers",
        lesson=lesson,
        run_id="run-2",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    log_lines = acceptance_log_path.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    assert first_path == second_path


def test_default_exporter_given_changed_content_expect_second_ledger_line(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-1",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )
    changed_lesson = deepcopy(_ordinal_lesson())
    changed_lesson["title"] = "A genuinely different title for round two"
    default_exporter(
        slug="ordinal_numbers",
        lesson=changed_lesson,
        run_id="run-2",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    log_lines = acceptance_log_path.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 2


# ---------------------------------------------------------------------------
# default_exporter: manifest upsert (design §12)
# ---------------------------------------------------------------------------


def test_default_exporter_given_real_export_expect_manifest_upserted_once(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    manifest_path = repo_root / "dist" / "manifest.json"

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-1",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [entry for entry in manifest["lessons"] if entry["slug"] == "ordinal_numbers"]
    assert len(entries) == 1
    assert manifest["lesson_count"] == len(manifest["lessons"])

    # a second real export (changed content) must upsert, not duplicate, the entry
    changed_lesson = deepcopy(_ordinal_lesson())
    changed_lesson["title"] = "A new title for the manifest upsert check"
    default_exporter(
        slug="ordinal_numbers",
        lesson=changed_lesson,
        run_id="run-2",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    manifest_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries_after = [entry for entry in manifest_after["lessons"] if entry["slug"] == "ordinal_numbers"]
    assert len(entries_after) == 1
    assert entries_after[0]["title"] == "A new title for the manifest upsert check"
    assert manifest_after["lesson_count"] == len(manifest_after["lessons"])


def test_default_exporter_given_existing_manifest_with_other_lessons_expect_preserved(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    manifest_path = repo_root / "dist" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "lesson_count": 1,
                "lessons": [
                    {
                        "slug": "noun_plurals",
                        "path": "dist/lessons/noun_plurals.json",
                        "source": "generated/a1-real-g1/lessons/noun_plurals.json",
                        "title": "Norwegian noun plurals",
                        "cefr_level": "A1",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-1",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    slugs = {entry["slug"] for entry in manifest["lessons"]}
    assert slugs == {"noun_plurals", "ordinal_numbers"}
    assert manifest["lesson_count"] == 2


def test_default_exporter_given_curriculum_structure_order_expect_manifest_follows_structure_json(
    tmp_path: Path,
):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    structure_path = repo_root / "curriculum" / "structure.json"
    structure_path.parent.mkdir(parents=True, exist_ok=True)
    structure_path.write_text(
        json.dumps(
            {
                "chapters": {
                    "a1": {
                        "title": "A1",
                        "lessons": [
                            {"key": "noun_plurals", "order": 1},
                            {"key": "ordinal_numbers", "order": 2},
                            {"key": "adjective_agreement", "order": 3},
                        ],
                    }
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_path = repo_root / "dist" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "lesson_count": 2,
                "lessons": [
                    {
                        "slug": "adjective_agreement",
                        "path": "dist/lessons/adjective_agreement.json",
                        "source": "generated/a1/adjective_agreement.json",
                        "title": "Adjective agreement",
                        "cefr_level": "A1",
                    },
                    {
                        "slug": "noun_plurals",
                        "path": "dist/lessons/noun_plurals.json",
                        "source": "generated/a1/noun_plurals.json",
                        "title": "Noun plurals",
                        "cefr_level": "A1",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-ordered",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["slug"] for entry in manifest["lessons"]] == [
        "noun_plurals",
        "ordinal_numbers",
        "adjective_agreement",
    ]


def test_default_exporter_given_missing_curriculum_structure_expect_manifest_preserves_existing_order(
    tmp_path: Path,
):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    manifest_path = repo_root / "dist" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "3.0",
                "lesson_count": 2,
                "lessons": [
                    {
                        "slug": "z_lesson",
                        "path": "dist/lessons/z_lesson.json",
                        "source": "generated/z_lesson.json",
                        "title": "Z lesson",
                        "cefr_level": "A1",
                    },
                    {
                        "slug": "a_lesson",
                        "path": "dist/lessons/a_lesson.json",
                        "source": "generated/a_lesson.json",
                        "title": "A lesson",
                        "cefr_level": "A1",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-preserve-order",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [entry["slug"] for entry in manifest["lessons"]] == [
        "z_lesson",
        "a_lesson",
        "ordinal_numbers",
    ]


def test_default_exporter_given_scratch_output_root_expect_tracked_manifest_untouched(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    tracked_manifest_path = repo_root / "dist" / "manifest.json"
    tracked_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tracked_manifest_path.write_text(
        json.dumps({"schema_version": "3.0", "lesson_count": 0, "lessons": []}, indent=2),
        encoding="utf-8",
    )
    scratch_root = repo_root / "store" / "scratch-run"

    default_exporter(
        slug="ordinal_numbers",
        lesson=_ordinal_lesson(),
        run_id="run-1",
        signoff_score=None,
        blocking_issues_messages=[],
        corrections_applied=[],
        repo_root=repo_root,
        acceptance_log_path=acceptance_log_path,
        output_root=scratch_root,
    )

    tracked_manifest = json.loads(tracked_manifest_path.read_text(encoding="utf-8"))
    assert tracked_manifest["lessons"] == []
    scratch_manifest_path = scratch_root / "dist" / "manifest.json"
    assert scratch_manifest_path.exists()
    scratch_manifest = json.loads(scratch_manifest_path.read_text(encoding="utf-8"))
    assert any(entry["slug"] == "ordinal_numbers" for entry in scratch_manifest["lessons"])
