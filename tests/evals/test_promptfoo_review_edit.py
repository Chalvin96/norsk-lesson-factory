"""Behavior tests for advisory package review and deterministic source repair."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from evals.promptfoo.lesson_generation import review_edit as review_module
from evals.promptfoo.lesson_generation.dependencies import approved_content_hash
from evals.promptfoo.lesson_generation.dependencies import validate_approved_source
from evals.promptfoo.lesson_generation.review_edit import ReviewEditFinding
from evals.promptfoo.lesson_generation.review_edit import ReviewEditPackage
from evals.promptfoo.lesson_generation.review_edit import review_existing_package
from evals.promptfoo.lesson_generation.source_repair import repair_exercise_source
from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from tests.clients.llm.fakes import FakeLlmClient
from tests.clients.llm.fakes import fake_job_runner
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_review_existing_package_given_language_finding_expect_advisory_without_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        repair_exercise_source(
            exercises_path.read_text(encoding="utf-8"),
            lesson_text=(source / "lesson.md").read_text(encoding="utf-8"),
        ).exercises_yaml,
        encoding="utf-8",
    )
    _refresh_approved_source_hash_for_test(source)
    prepared_files = {name: (source / name).read_bytes() for name in ("plan.md", "lesson.md", "exercises.yaml")}
    handles = audit_source_directory(source).exercise_handles
    review = ReviewEditPackage(
        summary="One language concern needs owner inspection.",
        audited_handles=handles,
        findings=[
            ReviewEditFinding(
                code="possible-naturalness",
                severity="minor",
                artifact="lesson.md",
                location="lesson introduction",
                rationale="This phrasing may sound unusual in Norwegian; verify it before changing the source.",
            )
        ],
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=source,
        output_root=tmp_path / "review",
        repo_root=tmp_path,
        run_id="advisory-review",
    )

    assert result.status == "unchanged"
    assert result.review is not None
    assert result.review.findings[0].rationale.startswith("This phrasing may sound unusual")
    assert "rationale" in result.review_path.read_text(encoding="utf-8")
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        assert (result.source_dir / name).read_bytes() == prepared_files[name]


def test_review_existing_package_given_yaml_repair_expect_prompt_uses_prepared_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    raw_exercises = exercises_path.read_text(encoding="utf-8")
    malformed = raw_exercises.replace(
        "  prompt_md: Complete the direct yes/no question.",
        "  prompt_md: Complete the question using the lesson's rule:\n    place the finite verb before the subject.",
        1,
    )
    exercises_path.write_text(malformed, encoding="utf-8")
    review = ReviewEditPackage(
        summary="No advisory concern found.",
        audited_handles=audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))
    _refresh_approved_source_hash_for_test(source)

    result = review_existing_package(
        source_dir=source,
        output_root=tmp_path / "prepared",
        repo_root=tmp_path,
        run_id="prepared-prompt",
    )

    expected_repair = repair_exercise_source(
        malformed,
        lesson_text=(source / "lesson.md").read_text(encoding="utf-8"),
    )
    prepared_exercises = (result.source_dir / "exercises.yaml").read_text(encoding="utf-8")
    assert prepared_exercises == expected_repair.exercises_yaml
    assert prepared_exercises in (result.source_dir.parent / "prompt.md").read_text(encoding="utf-8")
    validate_approved_source(result.source_dir)


def test_repair_exercise_source_given_wrapped_text_colon_expect_quoted_scalar():
    source = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8")
    wrapped = source.replace(
        "  prompt_md: Complete the direct yes/no question.",
        "  prompt_md: Complete the question using the lesson's rule:\n    place the finite verb before the subject.",
        1,
    )

    repaired = repair_exercise_source(wrapped)

    assert repaired.audit.status == "clean"
    assert any(item.code == "yaml-scalar-quoting" for item in repaired.repairs)
    normalized = " ".join(repaired.exercises_yaml.split())
    assert "Complete the question using the lesson''s rule: place the finite verb before the subject." in normalized


def test_build_review_edit_prompt_given_advisory_context_expect_no_edit_instructions():
    prompt = review_module.build_review_edit_prompt(
        lesson_md="# Lesson\n\nA short Norwegian lesson.",
        exercises_yaml='- handle: recall-one\n  op: choose\n  prompt_md: "Choose one."',
        mechanical_audit=MechanicalAudit(
            status="clean",
            exercise_handles=["recall-one"],
            marker_handles=["recall-one"],
        ),
        review_context='{"possible_issue": "Inspect this phrase."}',
    )

    assert "unverified AI suggestion" in prompt
    assert "never authorize source edits" in prompt
    assert "Do not include edits or replacement source" in prompt
    assert "Inspect this phrase." in prompt


def test_review_existing_package_given_unresolved_deterministic_finding_expect_review_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("text: hjemme.", "text: hjemme", 1),
        encoding="utf-8",
    )
    _refresh_approved_source_hash_for_test(source)
    monkeypatch.setattr(
        review_module,
        "_invoke_review",
        lambda **_: pytest.fail("AI review must not run while deterministic findings remain"),
    )

    result = review_existing_package(
        source_dir=source,
        output_root=tmp_path / "review",
        repo_root=tmp_path,
        run_id="deterministic-blocker",
    )

    assert result.status == "needs_human"
    receipt = result.receipt_path.read_text(encoding="utf-8")
    assert "deterministic preflight findings remain unresolved" in receipt
    assert result.mechanical_audit.material_findings


def test_review_existing_package_given_missing_handle_acknowledgement_expect_invalid_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = FakeLlmClient.responding('{"summary":"No advisory concern found.","findings":[]}')
    monkeypatch.setattr(review_module, "runner_for_job", lambda *args, **kwargs: fake_job_runner(client))

    with pytest.raises(LlmParseException, match="audited_handles mismatch"):
        review_existing_package(
            source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "review",
            repo_root=tmp_path,
            run_id="missing-ack",
        )


def test_review_existing_package_given_deterministic_repair_expect_repaired_bytes_revalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    malformed = exercises_path.read_text(encoding="utf-8").replace(
        "  prompt_md: Complete the direct yes/no question.",
        "  prompt_md: Complete the question using the lesson's rule:\n    place the finite verb before the subject.",
        1,
    )
    exercises_path.write_text(malformed, encoding="utf-8")
    _refresh_approved_source_hash_for_test(source)
    review = ReviewEditPackage(
        summary="No advisory concern found.",
        audited_handles=audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=source,
        output_root=tmp_path / "repaired",
        repo_root=tmp_path,
        run_id="deterministic-repair",
    )

    assert result.status == "edited"
    assert result.mechanical_audit.status == "clean"
    assert "yaml-scalar-quoting" in result.receipt_path.read_text(encoding="utf-8")
    validate_approved_source(result.source_dir)


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the fixture's authored source into a disposable test directory."""
    destination = tmp_path / "source"
    destination.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, destination / name)
    return destination


def _refresh_approved_source_hash_for_test(source: Path) -> None:
    """Refresh a copied fixture's approval hash after test setup edits."""
    plan_path = source / "plan.md"
    plan = plan_path.read_text(encoding="utf-8")
    updated = re.sub(
        r"(?m)^approved_source_hash:\s*.*$",
        f"approved_source_hash: {approved_content_hash(source)}",
        plan,
        count=1,
    )
    if updated == plan:
        raise AssertionError("fixture plan must contain approved_source_hash")
    plan_path.write_text(updated, encoding="utf-8")
