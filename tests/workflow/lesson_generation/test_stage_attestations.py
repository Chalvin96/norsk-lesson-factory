"""Entry point: ``verify_source_attestations`` behavior tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_COVERAGE
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_EXERCISE_VERIFICATION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_INTENT_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_LESSON_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_NORMALIZATION_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import write_passing_stage_attestations


def _source_with_attestations(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source / name)
    write_passing_stage_attestations(source)
    return source


def test_verify_source_attestations_given_relabelled_stage_expect_refusal(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"][K_STAGE_LESSON_REVIEW]["stage"] = "normalization_review"
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert any("contains stage 'normalization_review'" in issue for issue in issues)


def test_verify_source_attestations_given_unknown_stage_expect_refusal(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"]["future_stage"] = payload["stages"][K_STAGE_LESSON_REVIEW]
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert any("unknown stage(s): future_stage" in issue for issue in issues)


def test_verify_source_attestations_given_missing_review_prompt_provenance_expect_refusal(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"][K_STAGE_LESSON_REVIEW].pop("prompt_hash")
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert any("stage 'lesson_review' is missing its prompt hash" in issue for issue in issues)


def test_verify_source_attestations_given_open_tasks_expect_human_boundary_admission(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"][K_STAGE_EXERCISE_VERIFICATION] = {
        **payload["stages"][K_STAGE_EXERCISE_VERIFICATION],
        "status": "unverified_open",
        "details": {
            "open_handles": ["write-transfer"],
            "attempt_surface_status": "pass",
            "open_rubrics_status": "pass",
            "standalone_status": "pass",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert issues == []


def test_verify_source_attestations_given_open_handles_without_surface_passes_expect_refusal(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"][K_STAGE_EXERCISE_VERIFICATION] = {
        **payload["stages"][K_STAGE_EXERCISE_VERIFICATION],
        "status": "unverified_open",
        "details": {"open_handles": ["write-transfer"]},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert any("attempt_surface_status" in issue for issue in issues)
    assert any("open_rubrics_status" in issue for issue in issues)
    assert any("standalone_status" in issue for issue in issues)


def test_verify_source_attestations_given_stale_changed_stage_versions_expect_only_those_stages_flagged(
    tmp_path: Path,
) -> None:
    source = _source_with_attestations(tmp_path)
    path = source / "stage_attestations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stages"][K_STAGE_NORMALIZATION_REVIEW]["policy_version"] = "2"
    payload["stages"][K_STAGE_NORMALIZATION_REVIEW]["prompt_version"] = "4"
    payload["stages"][K_STAGE_INTENT_REVIEW]["policy_version"] = "1"
    payload["stages"][K_STAGE_EXERCISE_VERIFICATION]["policy_version"] = "2"
    path.write_text(json.dumps(payload), encoding="utf-8")

    issues = verify_source_attestations(source)

    assert any("stage 'normalization_review' policy version '2' is stale" in issue for issue in issues)
    assert any("stage 'normalization_review' prompt version '4' is stale" in issue for issue in issues)
    assert any("stage 'intent_review' policy version '1' is stale" in issue for issue in issues)
    assert any("stage 'exercise_verification' policy version '2' is stale" in issue for issue in issues)
    assert not any("'lesson_review'" in issue for issue in issues)
    assert not any(f"'{K_STAGE_COVERAGE}'" in issue for issue in issues)
