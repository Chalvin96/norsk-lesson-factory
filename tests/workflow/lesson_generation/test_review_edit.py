"""Behavior tests for bounded post-generation review edits."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.repair_source import repair_exercise_source
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.workflow.lesson_generation import review_edit as review_module
from lesson_builder.workflow.lesson_generation.dependencies import approved_content_hash
from lesson_builder.workflow.lesson_generation.dependencies import validate_approved_source
from lesson_builder.workflow.lesson_generation.review_edit import ReviewEditFinding
from lesson_builder.workflow.lesson_generation.review_edit import ReviewEditOperation
from lesson_builder.workflow.lesson_generation.review_edit import ReviewEditPackage
from lesson_builder.workflow.lesson_generation.review_edit import review_existing_package
from lesson_builder.workflow.lesson_generation.runner import review_lesson_package_edit
from lesson_builder.workflow.lesson_generation.runner import run_lesson_package
from tests.clients.llm.fakes import FakeLlmClient
from tests.clients.llm.fakes import fake_job_runner
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_review_existing_package_given_first_attempt_expect_shared_structured_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from lesson_builder.clients.llm.invocation import build_structured_json_prompt

    review = ReviewEditPackage(
        verdict="pass",
        summary="No local edits are needed.",
        audited_handles=audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
    )
    client = FakeLlmClient.responding(review.model_dump_json())
    monkeypatch.setattr(review_module, "runner_for_job", lambda job, repo_root: fake_job_runner(client))
    result = review_existing_package(
        source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "review",
        repo_root=tmp_path,
        run_id="shared-structured-prompt",
    )

    assert result.status == "edited"
    prompt_path = result.source_dir.parent / "prompt.md"
    assert client.calls[0][0] == build_structured_json_prompt(
        prompt_path.read_text(encoding="utf-8"),
        ReviewEditPackage,
    )


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
        verdict="pass",
        summary="No local edits are needed.",
        audited_handles=audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
    )
    client = FakeLlmClient.responding(review.model_dump_json())
    monkeypatch.setattr(review_module, "runner_for_job", lambda job, repo_root: fake_job_runner(client))
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
    assert prepared_exercises in client.calls[0][0]
    validate_approved_source(result.source_dir)


def test_review_existing_package_given_exact_edit_expect_copied_compiled_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    original_lesson = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8")
    review = _edit_review(
        "Norwegian main-clause word order: questions and fronting",
        "Norwegian questions and fronting",
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "edited",
        repo_root=tmp_path,
        run_id="review-1",
    )

    assert result.status == "edited"
    assert result.review is not None
    assert len(result.applied_edits) == 1
    assert "Norwegian questions and fronting" in (result.source_dir / "lesson.md").read_text(encoding="utf-8")
    assert (result.source_dir / "plan.md").read_text(encoding="utf-8").count("approved_source_hash:") == 1
    assert (result.review_path).exists()
    assert (result.receipt_path).exists()
    assert (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8") == original_lesson


def test_review_existing_package_given_ambiguous_edit_expect_fail_closed_without_original_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    review = _edit_review("finite verb", "finite verb form")
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))
    original = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_bytes()

    with pytest.raises(ValueError, match="expected 1 occurrence"):
        review_existing_package(
            source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "ambiguous",
            repo_root=tmp_path,
            run_id="review-ambiguous",
        )

    assert (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_bytes() == original
    receipt = tmp_path / "ambiguous" / "review_edit" / "receipt.json"
    assert '"status": "edit_failed"' in receipt.read_text(encoding="utf-8")


def test_review_existing_package_given_edit_with_yaml_colon_expect_transport_repair_and_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    handles = review_module.audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles
    review = ReviewEditPackage(
        verdict="needs_edit",
        summary="Clarify the learner instruction.",
        audited_handles=handles,
        findings=[
            ReviewEditFinding(
                code="prompt-clarity",
                severity="major",
                artifact="exercises.yaml",
                location="recall-question",
                evidence="Complete the direct yes/no question.",
                explanation="The instruction needs a short explicit cue.",
            )
        ],
        edits=[
            ReviewEditOperation(
                finding_code="prompt-clarity",
                artifact="exercises.yaml",
                old_text="prompt_md: Complete the direct yes/no question.",
                new_text="prompt_md: Complete the question: use the direct order.",
                reason="Clarify the intended word order.",
            )
        ],
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "yaml-colon",
        repo_root=tmp_path,
        run_id="yaml-colon",
    )

    assert result.status == "edited"
    assert result.post_edit_mechanical_audit is not None
    assert result.post_edit_mechanical_audit.status == "clean"
    assert "prompt_md: 'Complete the question: use the direct order.'" in (
        result.source_dir / "exercises.yaml"
    ).read_text(encoding="utf-8")


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


def test_build_review_edit_prompt_given_verifier_context_expect_handle_scoped_instruction():
    """Verifier findings are visible to the reviewer without widening its scope."""
    audit = MechanicalAudit(
        status="clean",
        exercise_handles=["recall-one"],
        marker_handles=["recall-one"],
    )
    prompt = review_module.build_review_edit_prompt(
        lesson_md="# Lesson\n\nA short Norwegian lesson.",
        exercises_yaml='- handle: recall-one\n  op: choose\n  prompt_md: "Choose one."',
        mechanical_audit=audit,
        review_context='{"unanswerable": ["recall-one"]}',
    )

    assert "INDEPENDENT EXERCISE-VERIFIER FINDINGS" in prompt
    assert '"unanswerable": ["recall-one"]' in prompt
    assert "Repair only the reported exercise handles" in prompt
    assert "Do not emit block-scalar markers" in prompt


def test_review_existing_package_given_pass_expect_safe_source_repair_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    review = ReviewEditPackage(
        verdict="pass",
        summary="No local edits are needed.",
        audited_handles=review_module.audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "pass",
        repo_root=tmp_path,
        run_id="review-pass",
    )

    assert result.status == "edited"
    assert result.applied_edits == ()
    assert (result.source_dir / "lesson.md").read_bytes() == (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_bytes()
    assert result.receipt_path.read_text(encoding="utf-8").find("applied_repairs") >= 0


def test_review_existing_package_given_mechanical_finding_and_pass_expect_needs_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("text: hjemme.", "text: hjemme", 1),
        encoding="utf-8",
    )
    handles = audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles
    review = ReviewEditPackage(
        verdict="pass",
        summary="The semantic review found no edits.",
        audited_handles=handles,
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    # The source hash is intentionally refreshed because this test changes the
    # source before invoking the review boundary.
    _refresh_approved_source_hash_for_test(source)
    result = review_existing_package(
        source_dir=source,
        output_root=tmp_path / "review",
        repo_root=tmp_path,
        run_id="mechanical-pass",
    )

    assert result.status == "needs_human"
    receipt = result.receipt_path.read_text(encoding="utf-8")
    assert "mechanical preflight findings remain unresolved" in receipt
    assert result.mechanical_audit.status == "blocked"


def test_review_existing_package_given_edit_that_introduces_mechanical_defect_expect_needs_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    review = review_module.ReviewEditPackage(
        verdict="needs_edit",
        summary="One edit accidentally removes target punctuation.",
        audited_handles=audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles,
        findings=[
            review_module.ReviewEditFinding(
                code="punctuation-edit",
                severity="major",
                artifact="exercises.yaml",
                location="build-fronted-time",
                evidence="hjemme.",
                explanation="The edit is intentionally unsafe for the regression test.",
            )
        ],
        edits=[
            review_module.ReviewEditOperation(
                finding_code="punctuation-edit",
                artifact="exercises.yaml",
                old_text="text: hjemme.",
                new_text="text: hjemme",
                reason="Regression fixture.",
            )
        ],
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_existing_package(
        source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "review",
        repo_root=tmp_path,
        run_id="mechanical-post-edit",
    )

    assert result.status == "needs_human"
    assert result.post_edit_mechanical_audit is not None
    assert result.post_edit_mechanical_audit.status == "blocked"
    assert not (tmp_path / "review" / "export").exists()


def test_review_existing_package_given_missing_handle_acknowledgement_expect_invalid_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    review = ReviewEditPackage(verdict="pass", summary="No edits.")
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    with pytest.raises(LlmParseException, match="audited_handles mismatch"):
        review_existing_package(
            source_dir=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "review",
            repo_root=tmp_path,
            run_id="missing-ack",
        )


def test_review_catalog_package_edit_given_parked_run_expect_revalidation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_lesson_package(repo_root=tmp_path, run_id="original")
    review = _edit_review(
        "Norwegian main-clause word order: questions and fronting",
        "Norwegian questions and fronting",
    )
    monkeypatch.setattr(review_module, "_invoke_review", lambda **_: (review, []))

    result = review_lesson_package_edit(
        "original",
        repo_root=tmp_path,
        run_id="original-review-edit",
    )

    assert result["review_edit_status"] == "needs_revalidation"
    assert result["stage"] == "not_reached"
    assert result["next"] == []
    assert result["original_run_id"] == "original"
    assert result["run_id"] == "original-review-edit"
    assert Path(result["review_edit_path"]).exists()
    assert result["review_edit_revalidation_issues"]
    assert not Path(result["review_edit_source_dir"] + "/stage_attestations.json").exists()
    assert not (tmp_path / "store/scratch/catalog_generation/original/export").exists()


def test_review_edit_operation_given_same_text_expect_validation_error():
    with pytest.raises(ValueError, match="must change"):
        ReviewEditOperation(
            finding_code="same",
            artifact="lesson.md",
            old_text="same",
            new_text="same",
            reason="No change",
        )


def test_review_edit_package_given_unknown_finding_reference_expect_validation_error():
    with pytest.raises(ValueError, match="unknown finding"):
        ReviewEditPackage(
            verdict="needs_edit",
            summary="Needs a patch.",
            findings=[
                ReviewEditFinding(
                    code="known",
                    severity="major",
                    artifact="lesson.md",
                    location="x",
                    evidence="x",
                    explanation="x",
                )
            ],
            edits=[
                ReviewEditOperation(
                    finding_code="missing",
                    artifact="lesson.md",
                    old_text="a",
                    new_text="b",
                    reason="patch",
                )
            ],
        )


def _edit_review(old_text: str, new_text: str) -> ReviewEditPackage:
    """Return one bounded source edit for reviewer-stage tests."""
    handles = review_module.audit_source_directory(K_CATALOG_PACKAGE_FIXTURE_ROOT).exercise_handles
    return ReviewEditPackage(
        verdict="needs_edit",
        summary="One local source correction is needed.",
        audited_handles=handles,
        findings=[
            ReviewEditFinding(
                code="naturalness-1",
                severity="major",
                artifact="lesson.md",
                location="lesson title",
                evidence=old_text,
                explanation="The title is unnecessarily repetitive.",
            )
        ],
        edits=[
            ReviewEditOperation(
                finding_code="naturalness-1",
                artifact="lesson.md",
                old_text=old_text,
                new_text=new_text,
                reason="Use the shorter learner-facing title.",
            )
        ],
    )


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy the fixture's authored source into a disposable test directory."""
    destination = tmp_path / "source"
    destination.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, destination / name)
    return destination


def _refresh_approved_source_hash_for_test(source: Path) -> None:
    """Refresh a copied fixture's public approval hash for test setup."""
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
