"""Tests for rich authoring stages through the production graph."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from lesson_builder.application.operations import rich_authoring_prompts as rich_prompts
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.rich_authoring import ExercisePackage
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.formats.yaml import quote_unquoted_yaml_scalars
from lesson_builder.workflow.lesson_generation import rich_authoring as rich_workflow
from lesson_builder.workflow.lesson_generation import rich_authoring_content as rich
from lesson_builder.workflow.lesson_generation.generation_log import hash_text
from lesson_builder.workflow.lesson_generation.stage_attestations import read_stage_attestations
from lesson_builder.workflow.lesson_generation.stage_attestations import text_content_hash
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import FakeClosedTaskVerifier
from tests.workflow.lesson_generation.fakes import FakeRichAuthorAgent
from tests.workflow.lesson_generation.fakes import FakeStandaloneVerifier
from tests.workflow.lesson_generation.fakes import SequenceClosedTaskVerifier
from tests.workflow.lesson_generation.fakes import SequenceStandaloneVerifier
from tests.workflow.lesson_generation.fakes import fixture_exercise_requests
from tests.workflow.lesson_generation.fakes import needs_repair_preservation_review
from tests.workflow.lesson_generation.fakes import needs_repair_quality_review
from tests.workflow.lesson_generation.fakes import valid_quality_review
from tests.workflow.lesson_generation.graph_fakes import run_rich_generation_graph


def test_closed_task_verifier_given_author_boundary_expect_composite_exercise_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reviewer_agent = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr(rich_workflow, "reviewer", lambda **_kwargs: reviewer_agent)

    def composite_gate(lesson: dict, *, reviewer_agent: object, strict: bool) -> dict[str, str]:
        observed.update({"lesson": lesson, "reviewer_agent": reviewer_agent, "strict": strict})
        return {"status": "pass"}

    monkeypatch.setattr(rich_workflow, "verify_exercise_package", composite_gate)
    verifier = rich_workflow.closed_task_verifier_for(tmp_path)

    assert verifier({"elements": []}) == {"status": "pass"}
    assert observed == {"lesson": {"elements": []}, "reviewer_agent": reviewer_agent, "strict": True}


def test_complete_exercises_given_categorize_mapping_alias_without_id_expect_public_buckets():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "sort-supplies",
                "op": "categorize",
                "prompt_md": "Sort the supplies.",
                "categories": [{"label": "countable things"}, {"name": "amounts"}],
                "items": [
                    {"id": "cups", "text": "kopper", "category": "countable things"},
                    {"id": "coffee", "text": "kaffe", "category": "amounts"},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-supplies"}]},
        )
    )[0]

    assert repaired["buckets"] == [
        {"bucket_id": "countable-things", "label": "countable things"},
        {"bucket_id": "amounts", "label": "amounts"},
    ]
    assert repaired["items"] == [
        {"text": "kopper", "item_id": "cups", "bucket_id": "countable-things"},
        {"text": "kaffe", "item_id": "coffee", "bucket_id": "amounts"},
    ]
    assert "categories" not in repaired


def test_rich_generation_graph_given_two_real_stage_shapes_expect_compilable_source(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    verifier = FakeClosedTaskVerifier()

    result = run_rich_generation_graph(
        plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "run",
        repo_root=tmp_path,
        run_id="rich-test",
        closed_task_verifier=verifier,
    )

    assert (result.source_dir / "lesson.md").exists()
    assert (result.source_dir / "exercises.yaml").exists()
    assert (tmp_path / "run" / "draft" / "rich.md").exists()
    assert (tmp_path / "run" / "review" / "lesson_review.json").exists()
    assert (tmp_path / "run" / "normalization" / "preservation_review.json").exists()
    assert (result.source_dir / "stage_attestations.json").exists()
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["mode"] == "rich_three_stage"
    assert [stage["name"] for stage in receipt["stages"]] == [
        "draft_author",
        "lesson_review",
        "checkpoint_split",
        "source_normalizer",
        "normalization_review",
        "intent_review",
        "intent_extraction",
        "exercise_author",
        "source_compile",
        "exercise_diagnostics",
        "exercise_verifier",
        "coverage",
    ]
    assert receipt["stages"][-1]["status"] == "pass"
    assert receipt["lesson_integrity"]["unchanged"] is True
    assert receipt["normalizer_mode"] == "edit"
    assert receipt["normalization_repaired"] is False
    assert receipt["stage_receipts"]["lesson_review"]["status"] == "pass"
    assert receipt["stage_receipts"]["normalization_review"]["status"] == "pass"
    assert receipt["stage_receipts"]["intent_review"]["status"] == "pass"
    assert receipt["stage_receipts"]["exercise_verification"]["status"] == "pass"
    assert receipt["stage_attestations"] == {
        "lesson_review": "pass",
        "normalization_review": "pass",
        "intent_review": "pass",
        "exercise_verification": "pass",
        "coverage": "pass",
        "artifact_compilation": "pass",
    }
    attestations = read_stage_attestations(result.source_dir)
    assert attestations is not None
    assert attestations.stages["lesson_review"].input_hash == text_content_hash(FakeRichAuthorAgent.draft_text)
    assert attestations.stages["normalization_review"].input_hash == text_content_hash(FakeRichAuthorAgent.draft_text)


def test_rich_generation_graph_given_open_handles_expect_attestation_preserves_them(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    verifier = FakeClosedTaskVerifier(
        {
            "status": "unverified_open",
            "lesson_hash": "sha256:fake-closed-task-verifier",
            "total": 7,
            "matches": 7,
            "open_handles": ["write-transfer"],
            "attempt_surface": {"status": "pass"},
            "open_rubrics": {"status": "pass"},
        }
    )

    result = run_rich_generation_graph(
        plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
        output_root=tmp_path / "run",
        repo_root=tmp_path,
        run_id="rich-open-attestation",
        closed_task_verifier=verifier,
    )

    attestations = read_stage_attestations(result.source_dir)
    assert attestations is not None
    exercise_attestation = attestations.stages["exercise_verification"]
    assert exercise_attestation.status == "unverified_open"
    assert exercise_attestation.details["open_handles"] == ["write-transfer"]


def test_rich_generation_graph_given_cached_normalization_expect_reused_receipt_status(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    run_root = tmp_path / "run"
    run_kwargs = {
        "plan_source": K_CATALOG_PACKAGE_FIXTURE_ROOT,
        "output_root": run_root,
        "repo_root": tmp_path,
        "run_id": "rich-cache-receipt",
        "job": "author",
        "closed_task_verifier": FakeClosedTaskVerifier(),
    }

    run_rich_generation_graph(**run_kwargs)

    second = run_rich_generation_graph(**run_kwargs)

    assert second.receipt["normalizer_response"]["status"] == "reused"


def test_rich_generation_graph_given_normalized_checkpoint_metadata_expect_boundary_failure_before_preservation(
    tmp_path: Path,
):
    _write_job_config(tmp_path)
    lesson_md = (
        (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md")
        .read_text(encoding="utf-8")
        .replace(
            "{{exercise: identify-question}}",
            "Checkpoint identify-question [objective obj-question-order, bloom understand]: Hidden.\n\n"
            "{{exercise: identify-question}}",
            1,
        )
    )
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.normalized_payload = {
        "lesson_md": lesson_md,
        "exercise_requests_yaml": fixture_exercise_requests(),
    }

    try:
        with pytest.raises(rich_workflow.ContentRemediationRequired, match="internal checkpoint metadata"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="checkpoint-metadata-leak",
                closed_task_verifier=FakeClosedTaskVerifier(),
            )
    finally:
        FakeRichAuthorAgent.normalized_payload = None

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stages"][-1] == {
        "name": "checkpoint_boundary",
        "status": "failed",
        "error": "normalized checkpoint boundary is invalid: learner-facing lesson Markdown contains internal checkpoint metadata",
    }


def test_rich_generation_graph_given_semantic_repair_with_spacing_defect_expect_mechanical_repair_before_compile(
    tmp_path: Path,
):
    """A verifier repair cannot bypass the deterministic exercise source audit."""
    _write_job_config(tmp_path)
    fixture_exercises = yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"))
    initial_exercises = json.loads(json.dumps(fixture_exercises))
    recall = next(item for item in initial_exercises if item["handle"] == "recall-question")
    recall["segments"][1]["text_md"] = " du norsk ?"
    repaired_item = json.loads(json.dumps(recall))
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.exercise_payloads = [
        {"exercises_yaml": yaml.safe_dump(initial_exercises, allow_unicode=True, sort_keys=False)},
        {"exercises_yaml": yaml.safe_dump([repaired_item], allow_unicode=True, sort_keys=False)},
    ]
    verifier = SequenceClosedTaskVerifier(
        [
            rich_workflow.ExerciseReviewError(
                {
                    "status": "needs_human",
                    "mismatches": [{"id": "recall-question"}],
                }
            ),
            {"status": "pass", "total": 7, "matches": 7},
        ]
    )

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-semantic-repair-preflight",
            closed_task_verifier=verifier,
        )
    finally:
        FakeRichAuthorAgent.exercise_payloads = None

    generated = yaml.safe_load((result.source_dir / "exercises.yaml").read_text(encoding="utf-8"))
    repaired = next(item for item in generated if item["handle"] == "recall-question")
    assert repaired["segments"][1]["text_md"] == " du norsk?"
    assert len(verifier.calls) == 2


def test_rich_generation_graph_given_unresolvable_verifier_failure_expect_fails_closed_without_strict_flag(
    tmp_path: Path,
):
    """Strict closed-task verification is unconditional, not a caller switch."""
    _write_job_config(tmp_path)
    fixture_exercises = yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"))
    recall = next(item for item in fixture_exercises if item["handle"] == "recall-question")
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.exercise_payloads = [
        {"exercises_yaml": yaml.safe_dump(fixture_exercises, allow_unicode=True, sort_keys=False)},
        {"exercises_yaml": yaml.safe_dump([recall], allow_unicode=True, sort_keys=False)},
    ]
    verifier = SequenceClosedTaskVerifier(
        [
            rich_workflow.ExerciseReviewError({"status": "needs_human", "mismatches": [{"id": "recall-question"}]}),
            rich_workflow.ExerciseReviewError({"status": "needs_human", "mismatches": [{"id": "recall-question"}]}),
        ]
    )

    try:
        with pytest.raises(rich_workflow.ContentRemediationRequired) as excinfo:
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-unresolvable-verifier",
                closed_task_verifier=verifier,
            )
    finally:
        FakeRichAuthorAgent.exercise_payloads = None

    assert excinfo.value.stage == "exercise_verification"

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["exercise_verification"]["status"] == "needs_human"
    assert receipt["stages"][-1]["name"] == "exercise_verifier"
    assert receipt["stages"][-1]["status"] == "failed"
    assert len(verifier.calls) == 2


def test_load_cached_normalized_package_given_routeless_cached_handoff_expect_cache_miss(
    tmp_path: Path,
):
    """A cached pre-route handoff is re-normalized, never replayed as legacy."""
    requests = yaml.safe_dump(
        [
            {
                "handle": "legacy-request",
                "objective_ref": "obj-question-order",
                "evidence_family": "recognition",
                "bloom": "understand",
                "intent": "Recognise the taught question pattern in context.",
                "context": "A short exchange from the model section.",
                "learner_action": "Choose the matching meaning.",
                "success_criteria": ["The selected meaning matches the sentence."],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )
    package = NormalizedPackage(
        lesson_md=(K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
        exercise_requests_yaml=requests,
    )
    response_path = tmp_path / "response.json"
    input_path = tmp_path / "stage_input.json"
    response_path.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    plan_text = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "plan.md").read_text(encoding="utf-8")
    rich_workflow._write_stage_input(
        input_path,
        draft_hash="sha256:draft",
        plan_hash=hash_text(plan_text),
        prompt_hash="sha256:prompt",
        policy_version=rich_workflow.K_RICH_NORMALIZATION_CACHE_POLICY_VERSION,
        job_hash="sha256:job",
    )

    assert (
        rich_workflow._load_cached_normalized_package(
            response_path,
            plan_text,
            draft_hash="sha256:draft",
            input_path=input_path,
            prompt_hash="sha256:prompt",
            job_hash="sha256:job",
        )
        is None
    )


def test_rich_generation_graph_given_needs_repair_lesson_review_expect_one_bounded_repair_then_rereview(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    repaired_sentence = "This draft explains the question pattern with corrected examples and practice."
    repaired_draft = FakeRichAuthorAgent.draft_text.replace(
        "This draft explains the question pattern with examples and practice.",
        repaired_sentence,
    )
    FakeRichAuthorAgent.repair_edit_response = {
        "edits": [
            {
                "finding_ref": "meaning_changing_translation:1",
                "old_text": "This draft explains the question pattern with examples and practice.",
                "new_text": repaired_sentence,
                "reason": "Keep the corrected explanation while preserving the draft structure.",
            }
        ]
    }
    FakeRichAuthorAgent.review_responses = [
        needs_repair_quality_review(),
        valid_quality_review("grammar"),
        valid_quality_review("grammar"),
        {"verdict": "pass", "summary": "Every reviewed teaching unit survived.", "findings": []},
    ]

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-draft-repair",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.repair_draft_text = None
        FakeRichAuthorAgent.repair_edit_response = None
        FakeRichAuthorAgent.review_responses = None

    assert (tmp_path / "run" / "draft" / "rich.pre_repair.md").read_text(
        encoding="utf-8"
    ).strip() == FakeRichAuthorAgent.draft_text
    assert (tmp_path / "run" / "draft" / "rich.md").read_text(encoding="utf-8").strip() == repaired_draft
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert [stage["name"] for stage in receipt["stages"][:4]] == [
        "draft_author",
        "lesson_review_repair",
        "lesson_review",
        "checkpoint_split",
    ]
    assert receipt["stage_receipts"]["lesson_review"]["repaired"] is True
    assert receipt["stage_receipts"]["lesson_review"]["reviewer_calls"] == 2
    attestations = read_stage_attestations(result.source_dir)
    assert attestations is not None
    assert attestations.stages["lesson_review"].input_hash == text_content_hash(repaired_draft)
    assert attestations.stages["normalization_review"].input_hash == text_content_hash(repaired_draft)


def test_rich_generation_graph_given_invalid_edit_transport_expect_one_correction_without_regeneration(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    repaired_sentence = "This draft explains the question pattern with corrected examples and practice."
    repaired_draft = FakeRichAuthorAgent.draft_text.replace(
        "This draft explains the question pattern with examples and practice.",
        repaired_sentence,
    )
    valid_edit = {
        "edits": [
            {
                "finding_ref": "meaning_changing_translation:1",
                "old_text": "This draft explains the question pattern with examples and practice.",
                "new_text": repaired_sentence,
                "reason": "Apply the exact local correction.",
            }
        ]
    }
    FakeRichAuthorAgent.repair_edit_responses = ["not JSON", valid_edit]
    FakeRichAuthorAgent.review_responses = [
        needs_repair_quality_review(),
        valid_quality_review("grammar"),
        valid_quality_review("grammar"),
        {"verdict": "pass", "summary": "The restored example survived.", "findings": []},
    ]

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-edit-transport-reask",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.repair_edit_responses = None
        FakeRichAuthorAgent.review_responses = None

    assert (tmp_path / "run" / "review" / "repair_response-1.txt").read_text(encoding="utf-8") == "not JSON"
    assert (tmp_path / "run" / "review" / "repair_response-2.txt").exists()
    assert (tmp_path / "run" / "draft" / "rich.md").read_text(encoding="utf-8").strip() == repaired_draft
    review_record = json.loads((tmp_path / "run" / "review" / "lesson_review.json").read_text(encoding="utf-8"))
    edit_attempts = [attempt for attempt in review_record["attempts"] if attempt.get("role") == "draft_edit"]
    assert [attempt["status"] for attempt in edit_attempts] == ["invalid", "valid"]
    assert result.source_dir.joinpath("lesson.md").exists()


def test_rich_generation_graph_given_unresolved_lesson_review_expect_fails_closed_before_normalization(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = [
        needs_repair_quality_review(),
        needs_repair_quality_review(),
        needs_repair_quality_review(),
    ]
    FakeRichAuthorAgent.repair_edit_response = {
        "edits": [
            {
                "finding_ref": "meaning_changing_translation:1",
                "old_text": "This draft explains the question pattern with examples and practice.",
                "new_text": "This draft explains the question pattern with corrected examples and practice.",
                "reason": "Apply the diagnosed local correction without rewriting the draft.",
            }
        ]
    }

    try:
        with pytest.raises(ValueError, match="remained unresolved after one bounded"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-unresolved-review",
                closed_task_verifier=FakeClosedTaskVerifier(),
            )
    finally:
        FakeRichAuthorAgent.review_responses = None
        FakeRichAuthorAgent.repair_edit_response = None
        FakeRichAuthorAgent.normalized_edit_response = None

    assert not (tmp_path / "run" / "normalization" / "response.json").exists()
    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["lesson_review"]["status"] == "fail"
    assert receipt["stages"][-1]["name"] == "lesson_review"
    assert receipt["stages"][-1]["status"] == "failed"
    review_record = json.loads((tmp_path / "run" / "review" / "lesson_review.json").read_text(encoding="utf-8"))
    assert review_record["status"] == "fail"
    assert review_record["reviewer_calls"] == 2
    assert len(review_record["attempts"]) == 3
    assert review_record["attempts"][1]["role"] == "draft_edit"


def test_rich_generation_graph_given_unavailable_lesson_reviewer_expect_fails_closed(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = [BackendDownException("reviewer offline")]

    try:
        with pytest.raises(BackendDownException, match="reviewer offline"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-reviewer-outage",
            )
    finally:
        FakeRichAuthorAgent.review_responses = None
        FakeRichAuthorAgent.normalized_edit_response = None

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["lesson_review"]["status"] == "unavailable"


def test_rich_generation_graph_given_invalid_lesson_review_response_expect_fails_closed_after_reask(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = ["not a JSON object", "{still not a review}"]

    try:
        with pytest.raises(LlmParseException, match="lesson review response remained invalid"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-invalid-review",
            )
    finally:
        FakeRichAuthorAgent.review_responses = None

    assert len(FakeRichAuthorAgent.instances[1].prompts) == 2
    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["lesson_review"]["status"] == "invalid"


def test_rich_generation_graph_given_needs_repair_preservation_review_expect_one_representation_repair(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = [
        valid_quality_review("grammar"),
        needs_repair_preservation_review(),
        {"verdict": "pass", "summary": "The restored example survived.", "findings": []},
    ]

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-preservation-repair",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.review_responses = None
        FakeRichAuthorAgent.normalized_edit_response = None

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["normalization_repaired"] is True
    assert receipt["stage_receipts"]["normalization_review"]["repaired"] is True
    assert receipt["stage_receipts"]["normalization_review"]["attempts"] == 2
    cached = json.loads((tmp_path / "run" / "normalization" / "response.json").read_text(encoding="utf-8"))
    assert cached["lesson_md"] == (result.source_dir / "lesson.md").read_text(encoding="utf-8")


def test_rich_generation_graph_given_invalid_normalization_edit_expect_persisted_transport_retry(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = [
        valid_quality_review("grammar"),
        needs_repair_preservation_review(),
        {"verdict": "pass", "summary": "The restored example survived.", "findings": []},
    ]
    FakeRichAuthorAgent.normalized_edit_responses = [
        "not a JSON object",
        {
            "edits": [
                {
                    "artifact": "lesson_md",
                    "finding_ref": "dropped_example:1",
                    "old_text": "The two Norwegian sentences contain the same main words.",
                    "new_text": (
                        "The two Norwegian sentences contain the same main words. "
                        "The restored example remains part of the model."
                    ),
                    "reason": "Restore the reviewed model explanation without rewriting the chapter.",
                }
            ]
        },
    ]

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-preservation-transport-retry",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.review_responses = None
        FakeRichAuthorAgent.normalized_edit_response = None
        FakeRichAuthorAgent.normalized_edit_responses = None

    assert result.source_dir.joinpath("lesson.md").exists()
    normalization_dir = tmp_path / "run" / "normalization"
    assert (normalization_dir / "normalization_repair_response-1.txt").read_text() == ("not a JSON object")
    assert (normalization_dir / "normalization_repair_response-2.txt").exists()
    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    repair_stage = next(stage for stage in receipt["stages"] if stage["name"] == "normalization_review_repair")
    assert [attempt["status"] for attempt in repair_stage["attempts"]] == [
        "invalid",
        "valid",
    ]


def test_rich_generation_graph_given_unresolved_preservation_review_expect_fails_closed_before_exercise_author(
    tmp_path: Path,
):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.review_responses = [
        valid_quality_review("grammar"),
        needs_repair_preservation_review(),
        needs_repair_preservation_review(),
    ]

    try:
        with pytest.raises(ValueError, match="normalization preservation review remained unresolved"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-unresolved-preservation",
                closed_task_verifier=FakeClosedTaskVerifier(),
            )
    finally:
        FakeRichAuthorAgent.review_responses = None

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["normalization_review"]["status"] == "fail"
    assert receipt["stages"][-1]["name"] == "normalization_review"
    assert receipt["stages"][-1]["status"] == "failed"


def test_rich_generation_graph_given_vague_intent_expect_fails_before_exercise_author(tmp_path: Path):
    _write_job_config(tmp_path)
    requests = yaml.safe_load(fixture_exercise_requests())
    requests[0]["evidence"] = "Do it."
    original_draft = FakeRichAuthorAgent.draft_text
    FakeRichAuthorAgent.draft_text = original_draft.replace(
        "evidence: Collect observable learner evidence for the choose target.",
        "evidence: Do it.",
        1,
    )
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.normalized_payload = {
        "lesson_md": (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
        "exercise_requests_yaml": yaml.safe_dump(requests, allow_unicode=True, sort_keys=False),
    }

    try:
        with pytest.raises(ValueError, match="exercise intent review rejected the handoff"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-vague-intent",
                closed_task_verifier=FakeClosedTaskVerifier(),
            )
    finally:
        FakeRichAuthorAgent.draft_text = original_draft
        FakeRichAuthorAgent.normalized_payload = None

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    findings = receipt["stage_receipts"]["intent_review"]["findings"]
    assert "has a vague evidence statement" in findings[0]
    assert receipt["stages"][-1]["name"] == "intent_review"
    assert receipt["stages"][-1]["status"] == "failed"


def test_exercise_intent_findings_given_concrete_fixture_requests_expect_no_findings():
    assert rich._exercise_intent_findings(fixture_exercise_requests()) == []


def test_exercise_intent_findings_given_operation_payload_leak_expect_finding():
    requests = yaml.safe_dump(
        [
            {
                "handle": "leaky-request",
                "objective_ref": "obj-question-order",
                "evidence_route": "meaning_selection",
                "bloom": "understand",
                "evidence": "Recognise the taught question pattern in context.",
                "op": "choose",
                "options": [{"id": "yes", "text": "A question", "correct": True}],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    findings = rich._exercise_intent_findings(requests)

    assert findings == [
        "request 'leaky-request' leaks operation fields ['op', 'options']; the handoff "
        "must stay operation-free until the exercise author runs"
    ]


def test_exercise_intent_findings_given_legacy_verbose_request_expect_finding():
    requests = yaml.safe_dump(
        [
            {
                "handle": "legacy-request",
                "objective_ref": "obj-question-order",
                "evidence_route": "meaning_selection",
                "bloom": "understand",
                "evidence": "Recognise the taught question pattern in context.",
                "intent": "Recognise the taught question pattern in context.",
                "context": "A short exchange from the model section.",
                "learner_action": "Choose the matching meaning.",
                "success_criteria": ["The selected meaning matches the sentence."],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    findings = rich._exercise_intent_findings(requests)

    assert findings == [
        "request 'legacy-request' carries verbose legacy field(s) ['context', 'intent', "
        "'learner_action', 'success_criteria']; the compact handoff allows only "
        "['handle', 'objective_ref', 'bloom', 'evidence_route', 'evidence']"
    ]


def test_exercise_intent_findings_given_backward_looking_evidence_expect_finding():
    requests = yaml.safe_dump(
        [
            {
                "handle": "backward-request",
                "objective_ref": "obj-question-order",
                "evidence_route": "open_production",
                "bloom": "apply",
                "evidence": "Return to the dialogue and reuse the first turn.",
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    findings = rich._exercise_intent_findings(requests)

    assert findings == [
        "request 'backward-request' evidence contains a backward-looking reference; the "
        "checkpoint must name observable evidence without referring to earlier lesson material"
    ]


def test_exercise_intent_findings_given_thin_evidence_expect_finding():
    requests = yaml.safe_dump(
        [
            {
                "handle": "thin-request",
                "objective_ref": "obj-question-order",
                "evidence_route": "meaning_selection",
                "bloom": "understand",
                "evidence": "ok",
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    findings = rich._exercise_intent_findings(requests)

    assert findings == [
        "request 'thin-request' has a vague evidence statement: state the observable "
        "learner evidence in at least 16 characters"
    ]


def test_apply_lesson_draft_edits_given_duplicate_finding_codes_expect_distinct_refs():
    review_data = needs_repair_quality_review()
    review_data["findings"].append(
        {
            "code": "meaning_changing_translation",
            "severity": "blocking",
            "artifact": "lesson.md",
            "location": "second examples block",
            "evidence": "A second translation changes the intended meaning.",
            "repair_instruction": "Restore the second translation.",
        }
    )
    review = LessonQualityReview.model_validate(review_data)
    response = LessonDraftEditResponse.model_validate(
        {
            "edits": [
                {
                    "finding_ref": "meaning_changing_translation:1",
                    "old_text": "first bad translation",
                    "new_text": "first corrected translation",
                    "reason": "Correct the first occurrence.",
                },
                {
                    "finding_ref": "meaning_changing_translation:2",
                    "old_text": "second bad translation",
                    "new_text": "second corrected translation",
                    "reason": "Correct the second occurrence.",
                },
            ]
        }
    )

    repaired = rich.apply_lesson_draft_edits("first bad translation\nsecond bad translation", review, response)

    assert repaired == "first corrected translation\nsecond corrected translation"


def test_apply_lesson_draft_edits_given_one_finding_with_two_locations_expect_both_edits():
    review = LessonQualityReview.model_validate(needs_repair_quality_review())
    response = LessonDraftEditResponse.model_validate(
        {
            "edits": [
                {
                    "finding_ref": "meaning_changing_translation:1",
                    "old_text": "first bad translation",
                    "new_text": "first corrected translation",
                    "reason": "Correct the first diagnosed occurrence.",
                },
                {
                    "finding_ref": "meaning_changing_translation:1",
                    "old_text": "second bad translation",
                    "new_text": "second corrected translation",
                    "reason": "Correct the repeated diagnosed occurrence.",
                },
            ]
        }
    )

    repaired = rich.apply_lesson_draft_edits("first bad translation\nsecond bad translation", review, response)

    assert repaired == "first corrected translation\nsecond corrected translation"


def test_apply_lesson_draft_edits_given_noop_reviewer_edits_expect_original_text():
    review = LessonQualityReview.model_validate(needs_repair_quality_review())
    response = LessonDraftEditResponse.model_validate(
        {
            "edits": [
                {
                    "finding_ref": "meaning_changing_translation:1",
                    "old_text": "already correct",
                    "new_text": "already correct",
                    "reason": "The cited text is already correct.",
                }
            ]
        }
    )

    repaired = rich.apply_lesson_draft_edits("already correct", review, response)

    assert repaired == "already correct"


def test_apply_lesson_draft_edits_given_multiple_invalid_edits_expect_all_diagnostics():
    review = LessonQualityReview.model_validate(needs_repair_quality_review())
    response = LessonDraftEditResponse.model_validate(
        {
            "edits": [
                {
                    "finding_ref": "meaning_changing_translation:1",
                    "old_text": "missing text",
                    "new_text": "corrected text",
                    "reason": "The literal is not present twice.",
                },
                {
                    "finding_ref": "unknown:1",
                    "old_text": "first bad translation",
                    "new_text": "first corrected translation",
                    "reason": "The reference is not in the review.",
                },
            ]
        }
    )

    with pytest.raises(ValueError) as error:
        rich.apply_lesson_draft_edits("first bad translation", review, response)

    message = str(error.value)
    assert "unknown finding ref(s)" in message
    assert "requires a unique old_text anchor, found 0 occurrence(s)" in message


def test_build_lesson_review_prompt_given_plan_without_kind_expect_rejects():
    with pytest.raises(ValueError, match="no catalog kind"):
        rich_prompts.build_lesson_review_prompt(
            "---\ncefr_level: A1\n---\n# Plan\n",
            "draft",
            plan_kind=None,
        )


def test_load_cached_normalized_package_given_changed_draft_expect_cache_miss(tmp_path: Path):
    package = NormalizedPackage(
        lesson_md=(K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
        exercise_requests_yaml=fixture_exercise_requests(),
    )
    response_path = tmp_path / "response.json"
    input_path = tmp_path / "stage_input.json"
    response_path.write_text(
        json.dumps(package.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rich_workflow._write_stage_input(
        input_path,
        draft_hash="sha256:old-draft",
        plan_hash="sha256:old-plan",
        prompt_hash="sha256:old-prompt",
        policy_version=rich_workflow.K_RICH_NORMALIZATION_CACHE_POLICY_VERSION,
        job_hash="sha256:old-job",
    )
    plan_text = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "plan.md").read_text(encoding="utf-8")

    assert (
        rich_workflow._load_cached_normalized_package(
            response_path,
            plan_text,
            draft_hash="sha256:new-draft",
            input_path=input_path,
            prompt_hash="sha256:new-prompt",
            job_hash="sha256:new-job",
        )
        is None
    )
    rich_workflow._write_stage_input(
        input_path,
        draft_hash="sha256:new-draft",
        plan_hash=hash_text(plan_text),
        prompt_hash="sha256:new-prompt",
        policy_version=rich_workflow.K_RICH_NORMALIZATION_CACHE_POLICY_VERSION,
        job_hash="sha256:new-job",
    )
    cached = rich_workflow._load_cached_normalized_package(
        response_path,
        plan_text,
        draft_hash="sha256:new-draft",
        input_path=input_path,
        prompt_hash="sha256:new-prompt",
        job_hash="sha256:new-job",
    )
    assert cached is not None
    assert cached.exercise_requests_yaml == package.exercise_requests_yaml


def test_build_exercise_author_prompt_given_compact_requests_expect_self_contained_contract():
    lesson = """---
slug: grounded
---
## First checkpoint {#first role=model}

Teach the first target here.

## Unrelated checkpoint {#unrelated role=contrast}

Another taught target.
"""
    requests = """- handle: first-check
  objective_ref: obj-first
  evidence_route: sentence_construction
  bloom: apply
  evidence: Construct the taught target in a new everyday context.
"""

    prompt = rich_prompts.build_exercise_author_prompt(
        plan_text="plan",
        lesson_md=lesson,
        exercise_requests_yaml=requests,
    )

    assert "Teach the first target here." in prompt
    assert "Another taught target." in prompt
    assert "build (fields:" in prompt
    assert "categorize (fields:" not in prompt
    assert "Apply answer-policy precedence in this order" in prompt
    assert "Examples and sample answers are illustrative" in prompt
    assert "missing one of those features is not an equivalent answer" in prompt


def test_build_exercise_repair_prompt_given_failed_handle_expect_scoped_retry_context():
    lesson = """---
slug: scoped
---
## First checkpoint {#first role=model}

Teach the first target here.

## Unrelated checkpoint {#unrelated role=contrast}

Do not send this unrelated target to the repair author.
"""
    requests = """- handle: first-check
  objective_ref: obj-first
  evidence_route: sentence_construction
  bloom: apply
  evidence: Construct the taught target in a new everyday context.
- handle: unrelated-check
  objective_ref: obj-first
  evidence_route: meaning_selection
  bloom: understand
  evidence: Recognise another taught target in isolation.
"""

    prompt = rich_prompts.build_exercise_repair_prompt(
        plan_text="plan",
        lesson_md=lesson,
        exercise_requests_yaml=requests,
        failed_handles=["first-check"],
        verification={"mismatches": [{"id": "first-check"}]},
        learner_visible_payload=[{"id": "first-check", "operation": "build", "tokens": []}],
    )

    assert "Teach the first target here." in prompt
    assert "unrelated-check" not in prompt


def test_repair_exercise_yaml_given_authored_option_order_expect_preserves_order():
    source = yaml.safe_dump(
        [
            {
                "handle": "choose-answer",
                "op": "choose",
                "prompt_md": "Choose one.",
                "options": [
                    {"id": "yes", "text": "Ja", "correct": True},
                    {"id": "no", "text": "Nei", "correct": False},
                ],
            },
            {
                "handle": "build-answer",
                "op": "build",
                "prompt_md": "Build it.",
                "tokens": [
                    {"token_id": "first", "text": "Jeg"},
                    {"token_id": "second", "text": "går."},
                ],
                "answer_order": ["first", "second"],
            },
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich_workflow.repair_exercise_yaml_if_needed(ExercisePackage(exercises_yaml=source)).exercises_yaml
    )

    assert [option["id"] for option in repaired[0]["options"]] == ["yes", "no"]
    assert [token["token_id"] for token in repaired[1]["tokens"]] == ["first", "second"]
    assert repaired[1]["answer_order"] == ["first", "second"]


def test_merge_failed_exercises_given_one_failed_handle_expect_unaffected_item_preserved():
    current = yaml.safe_dump(
        [
            {
                "handle": "keep",
                "op": "choose",
                "prompt_md": "Keep this.",
                "options": [
                    {"id": "a", "text": "A", "correct": True},
                    {"id": "b", "text": "B", "correct": False},
                ],
            },
            {
                "handle": "repair",
                "op": "choose",
                "prompt_md": "Repair this.",
                "options": [
                    {"id": "a", "text": "A", "correct": True},
                    {"id": "b", "text": "B", "correct": False},
                ],
            },
        ],
        sort_keys=False,
    )
    replacement = yaml.safe_dump(
        [
            {
                "handle": "repair",
                "op": "choose",
                "prompt_md": "Repair this clearly.",
                "options": [
                    {"id": "a", "text": "A", "correct": True},
                    {"id": "b", "text": "B", "correct": False},
                ],
            }
        ],
        sort_keys=False,
    )

    merged = yaml.safe_load(rich.merge_failed_exercises(current, replacement, ["repair"]))

    assert merged[0] == yaml.safe_load(current)[0]
    assert merged[1]["prompt_md"] == "Repair this clearly."


def test_merge_failed_exercises_given_extra_replacement_handle_expect_rejection():
    current = yaml.safe_dump(
        [{"handle": "repair", "op": "choose", "options": []}],
        sort_keys=False,
    )
    replacement = yaml.safe_dump(
        [
            {"handle": "repair", "op": "choose", "options": []},
            {"handle": "unrelated", "op": "choose", "options": []},
        ],
        sort_keys=False,
    )

    with pytest.raises(ValueError, match="unexpected handles"):
        rich.merge_failed_exercises(current, replacement, ["repair"])


def test_build_exercise_repair_prompt_given_failed_payload_expect_answer_keys_hidden():
    prompt = rich_prompts.build_exercise_repair_prompt(
        "base author prompt",
        failed_handles=["repair"],
        verification={"mismatches": [{"id": "repair", "expected": "b", "actual": "a"}]},
        learner_visible_payload=[
            {
                "id": "repair",
                "operation": "choose",
                "prompt": "Choose the reply.",
                "options": [
                    {"option_id": "a", "text": "Ja"},
                    {"option_id": "b", "text": "Nei"},
                ],
            }
        ],
    )

    assert "Choose the reply." in prompt
    assert "answer_index" not in prompt
    assert "answer_order" not in prompt
    assert "correct" not in prompt


def test_validate_exercise_source_for_retry_given_duplicate_build_id_expect_diagnostic():
    lesson = "{{exercise: build-answer}}\n"
    exercises = yaml.safe_dump(
        [
            {
                "handle": "build-answer",
                "op": "build",
                "objective": "obj-test",
                "bloom": "apply",
                "prompt_md": "Build it.",
                "tokens": [
                    {"token_id": "word", "text": "Jeg"},
                    {"token_id": "verb", "text": "går."},
                ],
                "answer_order": ["word", "word"],
            }
        ],
        sort_keys=False,
    )

    with pytest.raises(ValueError, match="exercise-source-invalid"):
        rich_workflow._validate_exercise_source_for_retry(lesson, exercises)


def test_validate_normalized_lesson_shape_given_dangling_example_expect_rejects_source():
    with pytest.raises(ValueError, match="no.*en"):
        rich.validate_normalized_lesson_shape("::: examples\n- no: Han løper rask. ✗\n:::\n")


def test_validate_normalized_lesson_shape_given_multiple_example_lists_expect_rejects_source():
    source = """---
default_lang: nb
---
::: examples
- no: Han løper.
- en: He runs.

The second pair is separated by prose.

- no: Hun går.
- en: She walks.
:::
"""

    with pytest.raises(ValueError, match="exactly one bullet list"):
        rich.validate_normalized_lesson_shape(source)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("> quoted learner text\n", "blockquotes"),
        ("```text\nunsupported\n```\n", "fences"),
        ("| English | Norwegian |\n|---|---|\n", "pipe tables"),
        ("::: {.rule}\nOne rule.\n\nA second paragraph.\n:::\n", "rule block"),
        ("- A point\n\n  ::: examples\n  - no: Hei\n  - en: Hi\n  :::\n", "typed blocks"),
        (
            '::: {.reading translation="" dialogue_id="d" speaker_id="s" speaker_name="S"}\nHei.\n:::\n',
            "translation attribute",
        ),
    ],
)
def test_validate_normalized_lesson_shape_given_unsupported_markdown_expect_rejects_source(source: str, message: str):
    with pytest.raises(ValueError, match=message):
        rich.validate_normalized_lesson_shape(source)


def test_normalize_compiler_unsafe_markdown_given_activity_handle_expect_hyphenated_display():
    normalized = rich.normalize_compiler_unsafe_markdown(
        "**Activity request: notice_known_objects**\n\n{{exercise: notice_known_objects}}\n"
    )

    assert "Activity request: notice-known-objects" in normalized
    assert "{{exercise: notice_known_objects}}" in normalized


def test_complete_exercises_given_numbered_inline_stem_expect_keeps_single_paragraph():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "meaning-check",
                "op": "choose",
                "bloom": "understand",
                "prompt_md": "Choose the meaning.",
                "stem_md": "1. Ja. 2. Jo.",
                "options": [
                    {"id": "one", "text": "The first meaning.", "correct": True},
                    {"id": "two", "text": "The other meaning.", "correct": False},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-meaning"}]},
        )
    )[0]

    assert repaired["stem_md"].startswith("Item 1:")


def test_complete_exercises_given_underscore_blank_expect_uses_contract_marker():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "fill-word",
                "op": "choose",
                "prompt_md": "Choose the missing word.",
                "stem_md": "Jeg er __ i dag.",
                "options": [
                    {"id": "tired", "text": "trøtt", "correct": True},
                    {"id": "happy", "text": "glad", "correct": False},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-fill"}]},
        )
    )[0]

    assert repaired["stem_md"] == "Jeg er [BLANK] i dag."


def test_complete_exercises_given_categorize_category_alias_expect_public_buckets():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "sort-supplies",
                "op": "categorize",
                "prompt_md": "Sort the supplies.",
                "categories": ["countable things", "amounts"],
                "items": [
                    {"id": "cups", "text": "kopper", "category": "countable things"},
                    {"id": "coffee", "text": "kaffe", "category": "amounts"},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-supplies"}]},
        )
    )[0]

    assert repaired["buckets"] == [
        {"bucket_id": "countable-things", "label": "countable things"},
        {"bucket_id": "amounts", "label": "amounts"},
    ]
    assert repaired["items"] == [
        {"text": "kopper", "item_id": "cups", "bucket_id": "countable-things"},
        {"text": "kaffe", "item_id": "coffee", "bucket_id": "amounts"},
    ]
    assert "categories" not in repaired


def test_complete_exercises_given_write_string_criteria_expect_restores_rubric_mappings():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "open-task",
                "op": "write",
                "prompt_md": "Write a short reply.",
                "criteria": ["Uses a greeting.", "Uses a clear request."],
                "judge_prompt": "Check the reply.",
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-open"}]},
        )
    )[0]

    assert repaired["criteria"] == [
        {"id": "criterion_1", "instruction": "Uses a greeting."},
        {"id": "criterion_2", "instruction": "Uses a clear request."},
    ]


def test_author_exercises_given_write_without_rubric_expect_targeted_retry_then_bounded_remediation(
    tmp_path: Path,
) -> None:
    _write_job_config(tmp_path)
    plan_source = tmp_path / "plan"
    plan_source.mkdir()
    (plan_source / "plan.md").write_text(
        "---\nobjectives:\n  - id: obj-open\n---\n# Open production\n",
        encoding="utf-8",
    )
    run = rich_workflow.initialize_rich_run(
        plan_source=plan_source,
        output_root=tmp_path / "run",
        repo_root=tmp_path,
        run_id="missing-write-rubric",
        job="author",
    )
    package = NormalizedPackage(
        lesson_md="---\nslug: open\n---\n{{exercise: open-task}}\n",
        exercise_requests_yaml=(
            "- handle: open-task\n"
            "  objective_ref: obj-open\n"
            "  bloom: apply\n"
            "  evidence_route: open_production\n"
            "  evidence: Produce a short Norwegian reply.\n"
        ),
    )
    missing_rubric = yaml.safe_dump(
        [
            {
                "handle": "open-task",
                "op": "write",
                "prompt_md": "Write a short reply.",
                "feedback": "Look for a greeting and a request.",
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.exercise_payloads = [
        {"exercises_yaml": missing_rubric},
        {"exercises_yaml": missing_rubric},
    ]

    try:
        with pytest.raises(rich_workflow.ContentRemediationRequired, match="judge_prompt") as excinfo:
            rich_workflow.author_exercises(
                run,
                package=package,
                requests_hash="sha256:requests",
                lesson_hash="sha256:lesson",
            )
    finally:
        FakeRichAuthorAgent.exercise_payloads = None

    assert excinfo.value.stage == "exercise_verification"
    exercise_agents = [agent for agent in FakeRichAuthorAgent.instances if agent.name == "exercise_author"]
    assert len(exercise_agents) == 2
    assert "judge_prompt" in exercise_agents[1].prompts[0]
    assert run.receipt["stages"][-1]["name"] == "exercise_alignment"
    assert run.receipt["stages"][-1]["status"] == "failed"


def test_complete_exercises_given_inline_recall_markers_expect_typed_slots_in_place():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "dated-update",
                "op": "recall_fill",
                "prompt_md": "Complete the update.",
                "segments": [
                    {"text_md": "Amir [BLANK] klokka ni. [BLANK]"},
                    {
                        "blank_id": "b1",
                        "options": ["ringte", "har ringt"],
                        "answer_index": 0,
                    },
                    {
                        "blank_id": "b2",
                        "options": ["Sendte", "Har sendt"],
                        "answer_index": 0,
                    },
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-update"}]},
        )
    )[0]

    assert repaired["segments"] == [
        {"text_md": "Amir "},
        {"blank_id": "b1", "options": ["ringte", "har ringt"], "answer_index": 0},
        {"text_md": " klokka ni. "},
        {"blank_id": "b2", "options": ["Sendte", "Har sendt"], "answer_index": 0},
        {"text_md": " "},
    ]


def test_complete_exercises_given_marker_count_mismatch_expect_rejects_recall_payload():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "broken-update",
                "op": "recall_fill",
                "prompt_md": "Complete the update.",
                "segments": [
                    {"text_md": "Amir [BLANK] klokka ni."},
                    {
                        "blank_id": "b1",
                        "options": ["ringte", "har ringt"],
                        "answer_index": 0,
                    },
                    {
                        "blank_id": "b2",
                        "options": ["sendte", "har sendt"],
                        "answer_index": 0,
                    },
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    with pytest.raises(ValueError, match="inline blank markers"):
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-update"}]},
        )


def test_repair_exercise_yaml_given_build_tokens_in_answer_order_expect_preserves_display_order():
    package = ExercisePackage(
        exercises_yaml=yaml.safe_dump(
            [
                {
                    "handle": "build-sentence",
                    "op": "build",
                    "objective": "obj",
                    "bloom": "apply",
                    "prompt_md": "Build the sentence.",
                    "tokens": [
                        {"token_id": "t1", "text": "Jeg"},
                        {"token_id": "t2", "text": "går."},
                    ],
                    "answer_order": ["t1", "t2"],
                }
            ],
            allow_unicode=True,
            sort_keys=False,
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    item = yaml.safe_load(repaired.exercises_yaml)[0]

    assert [token["token_id"] for token in item["tokens"]] == ["t1", "t2"]
    assert item["answer_order"] == ["t1", "t2"]


def test_complete_exercises_given_yaml_boolean_token_id_expect_string_token_id():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "confirm-answer",
                "op": "build",
                "prompt_md": "Build the answer.",
                "tokens": [
                    {"token_id": "ja", "text": "Ja,"},
                    {"token_id": True, "text": "jeg gjør det."},
                ],
                "answer_order": ["ja", True],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-answer"}]},
        )
    )[0]

    assert [token["token_id"] for token in repaired["tokens"]] == ["ja", "true"]
    assert repaired["answer_order"] == ["ja", "true"]


def test_complete_exercises_given_word_min_alias_expect_public_min_words():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "write-reply",
                "op": "write",
                "prompt_md": "Write a short reply in Norwegian.",
                "word_min": 18,
                "word_max": 55,
                "judge_prompt": "Check the reply.",
                "criteria": [{"id": "clear", "instruction": "It is clear."}],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-write"}]},
        )
    )[0]

    assert repaired["min_words"] == 18
    assert repaired["max_words"] == 55
    assert "word_min" not in repaired
    assert "word_max" not in repaired


def test_repair_exercise_yaml_given_unquoted_colon_scalar_expect_quotes_value():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: time-check\n"
            "  op: choose\n"
            "  prompt_md: Choose the time: morning or evening.\n"
            "  options:\n"
            "    - id: morning\n"
            "      text: 08:30\n"
            "      correct: true\n"
            "    - id: evening\n"
            "      text: 18:00\n"
            "      correct: false\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert parsed[0]["prompt_md"] == "Choose the time: morning or evening."
    assert parsed[0]["options"][0]["text"] == "08:30"


def test_repair_exercise_yaml_given_block_scalar_expect_preserves_body():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: explain\n"
            "  op: judge\n"
            "  prompt_md: >-\n"
            "    Explain this sentence: it describes a plan.\n"
            "  options:\n"
            "    - id: yes\n"
            "      text: Correct\n"
            "      correct: true\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert "Explain this sentence: it describes a plan." in parsed[0]["prompt_md"]


def test_repair_exercise_yaml_given_wrapped_colon_field_expect_quotes_joined_value():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: wrapped\n"
            "  op: choose\n"
            "  prompt_md: Choose the meaning: ability or possibility\n"
            "    and keep the whole distinction.\n"
            "  options:\n"
            "    - id: ability\n"
            "      text: Ability\n"
            "      correct: true\n"
            "    - id: possibility\n"
            "      text: Possibility\n"
            "      correct: false\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert parsed[0]["prompt_md"] == ("Choose the meaning: ability or possibility and keep the whole distinction.")


def test_repair_exercise_yaml_given_broken_text_quote_expect_canonical_scalar():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: options\n"
            "  op: choose\n"
            "  prompt_md: Choose one.\n"
            "  options:\n"
            "    - id: first\n"
            '      text: "en: en stol, en lampe, en bord\n'
            "      correct: true\n"
            "    - id: second\n"
            "      text: Another option\n"
            "      correct: false\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert parsed[0]["options"][0]["text"] == "en: en stol, en lampe, en bord"


def test_repair_exercise_yaml_given_embedded_double_quote_and_colon_expect_roundtrip():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: quoted-answer\n"
            "  op: choose\n"
            "  prompt_md: Choose the quoted reply: yes or no.\n"
            "  options:\n"
            "    - id: yes\n"
            '      text: He said "ja": use the positive answer.\n'
            "      correct: true\n"
            "    - id: no\n"
            "      text: He said no.\n"
            "      correct: false\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert parsed[0]["options"][0]["text"] == 'He said "ja": use the positive answer.'


def test_repair_exercise_yaml_given_build_distractor_tokens_expect_prunes_extras():
    package = ExercisePackage(
        exercises_yaml=(
            "- handle: phrase\n"
            "  op: build\n"
            "  prompt_md: Build the phrase.\n"
            "  tokens:\n"
            "    - token_id: this\n"
            "      text: denne\n"
            "    - token_id: chair\n"
            "      text: stolen\n"
            "    - token_id: distractor\n"
            "      text: den\n"
            "  answer_order: [this, chair]\n"
        )
    )

    repaired = rich_workflow.repair_exercise_yaml_if_needed(package)
    parsed = yaml.safe_load(repaired.exercises_yaml)

    assert [token["token_id"] for token in parsed[0]["tokens"]] == ["this", "chair"]
    assert parsed[0]["answer_order"] == ["this", "chair"]


def test_quote_unquoted_yaml_scalars_given_colon_in_list_item_expect_quotes_item():
    repaired = quote_unquoted_yaml_scalars("success_criteria:\n  - Preserve the meaning: do not add a new detail.\n")

    parsed = yaml.safe_load(repaired)

    assert parsed["success_criteria"] == ["Preserve the meaning: do not add a new detail."]


def test_complete_exercise_metadata_given_legacy_recall_segments_expect_public_shape():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "speaker-choice",
                "op": "recall_fill",
                "prompt_md": "Choose the speaker.",
                "segments": [
                    {"type": "text", "text": "Maja says: [BLANK] kommer."},
                    {
                        "type": "blank",
                        "options": [
                            {"text": "Jeg", "correct": True},
                            {"text": "Du", "correct": False},
                        ],
                    },
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-speaker"}]},
        )
    )[0]

    assert repaired["segments"] == [
        {"text_md": "Maja says: "},
        {
            "blank_id": "speaker-choice_blank_1",
            "options": ["Jeg", "Du"],
            "answer_index": 0,
        },
        {"text_md": " kommer. "},
    ]


def test_rich_generation_graph_given_invalid_normalized_source_expect_preserves_failure(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.normalized_payload = {"lesson_md": "not source", "exercises_yaml": "- no"}

    try:
        with pytest.raises(
            LlmParseException,
            match="lesson exercise markers and exercise request handles differ",
        ):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-invalid",
            )
    finally:
        FakeRichAuthorAgent.normalized_payload = None

    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stages"][-1]["name"] == "source_normalizer"
    assert receipt["stages"][-1]["status"] == "failed"
    assert (tmp_path / "run" / "draft" / "rich.md").exists()


def test_rich_generation_graph_given_compiler_metadata_and_markdown_gaps_expect_repairs_source(tmp_path: Path):
    _write_job_config(tmp_path)
    fixture_lesson = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8")
    fixture_lesson = fixture_lesson.replace(
        "requirements_ref: tests/fixtures/lesson_packages/doctor_appointment/grammar/brief.md\n",
        "",
    )
    fixture_lesson = fixture_lesson.replace("grounding_mode: grounded\n", "")
    fixture_lesson = fixture_lesson.replace(
        "## What you will learn in this lesson {#sec-orient role=orient}",
        "### What you will learn in this lesson\n\n## What you will learn in this lesson {#sec-orient role=orient}",
    )
    fixture_lesson = fixture_lesson.replace("  \n", "\n")
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.normalized_payload = {
        "lesson_md": fixture_lesson,
        "exercises_yaml": (K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"),
    }

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-metadata-repair",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.normalized_payload = None

    normalized = (result.source_dir / "lesson.md").read_text(encoding="utf-8")
    assert "grounding_mode: grounded" in normalized
    assert "requirements_ref: plan.md" in normalized
    assert "### What you will learn" not in normalized
    assert "  \n" not in normalized
    assert "approved_source_hash:" in (result.source_dir / "plan.md").read_text(encoding="utf-8")


def test_complete_metadata_given_quoted_grounding_mode_expect_requirements_ref_inserted():
    lesson = (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8")
    lesson = lesson.replace(
        "requirements_ref: tests/fixtures/lesson_packages/doctor_appointment/grammar/brief.md\n",
        "",
    )
    lesson = lesson.replace("grounding_mode: grounded\n", 'grounding_mode: "grounded"\n')
    package = NormalizedPackage(
        lesson_md=lesson,
        exercise_requests_yaml=fixture_exercise_requests(),
    )

    normalized = rich_workflow.complete_normalized_metadata(
        package,
        yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "plan.md").read_text(encoding="utf-8").split("---", 2)[1]),
    )

    assert "grounding_mode:" in normalized.lesson_md
    assert "requirements_ref: plan.md" in normalized.lesson_md


def test_refresh_approved_source_hash_given_catalog_plan_without_hash_expect_inserts_hash(
    tmp_path: Path,
):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "lesson.md").write_text("lesson", encoding="utf-8")
    (source_dir / "exercises.yaml").write_text("[]\n", encoding="utf-8")
    plan_path = source_dir / "plan.md"
    plan_path.write_text(
        "---\npackage_version: 0.1-catalog-batch\nlesson_id: example\n---\n\n# Plan\n",
        encoding="utf-8",
    )

    rich_workflow._refresh_approved_source_hash(plan_path, source_dir)

    plan = plan_path.read_text(encoding="utf-8")
    assert f"approved_source_hash: {rich_workflow.approved_content_hash(source_dir)}" in plan


def test_normalize_compiler_unsafe_markdown_given_unbalanced_strong_label_expect_plain_label():
    lesson = "## Section {#section role=orient objectives=obj}\n\n**Checkpoint without a close\n"

    normalized = rich.normalize_compiler_unsafe_markdown(lesson)

    assert "**" not in normalized
    assert "Checkpoint without a close" in normalized


def test_normalize_compiler_unsafe_markdown_given_escaped_marker_inside_strong_expect_plain_strong():
    lesson = "## Section {#section role=orient objectives=obj}\n\n**\\*Wrong order.**\n"

    normalized = rich.normalize_compiler_unsafe_markdown(lesson)

    assert "**Wrong order.**" in normalized
    assert "\\*" not in normalized


def test_normalize_compiler_unsafe_markdown_given_multi_objective_model_section_expect_one():
    lesson = "## Forms {#forms role=model objectives=obj-one,obj-two}\n\nUse the first objective here.\n"

    normalized = rich.normalize_compiler_unsafe_markdown(lesson)

    assert "objectives=obj-one}" in normalized
    assert "obj-two" not in normalized


def test_normalize_yaml_strings_given_inline_code_in_exercise_text_expect_plain_text():
    normalized = rich._normalize_yaml_strings({"prompt_md": "Use `ikke` before the verb."})

    assert normalized == {"prompt_md": "Use ikke before the verb."}


def test_normalize_yaml_strings_given_numbered_recall_text_expect_inline_label():
    normalized = rich._normalize_yaml_strings({"text_md": "1. Negative statement: subject + verb + ikke"})

    assert normalized == {"text_md": "Item 1: Negative statement: subject + verb + ikke"}


def test_normalize_compiler_unsafe_markdown_given_unmatched_code_marker_expect_plain_text():
    normalized = rich.normalize_compiler_unsafe_markdown(
        "## Section {#section role=orient objectives=obj}\n\nUse `ikke before the verb.\n"
    )

    assert "`" not in normalized
    assert "ikke before the verb." in normalized


def test_rich_generation_graph_given_collapsed_choose_slots_expect_restores_visible_slots(tmp_path: Path):
    _write_job_config(tmp_path)
    exercises = yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"))
    choose_items = [item for item in exercises if item.get("op") == "choose"][:3]
    third_item = next(item for item in exercises if item.get("op") == "judge")
    third_item["op"] = "choose"
    third_item.pop("answer", None)
    third_item["options"] = [
        {"id": "infinitive", "text": "infinitive", "correct": True},
        {"id": "present", "text": "present tense", "correct": False},
    ]
    choose_items.append(third_item)
    choose_items[0]["prompt_md"] = "Complete Jeg kan  i kveld."
    choose_items[1]["prompt_md"] = "Choose the correct form in Kan Nora ?"
    choose_items[2]["prompt_md"] = "Complete the rule: After `kan, use the  without å`."
    FakeRichAuthorAgent.instances.clear()
    route_by_operation = {
        "choose": "meaning_selection",
        "recall_fill": "bounded_retrieval",
        "judge": "sentence_judgement",
        "find_fix": "form_repair",
        "build": "sentence_construction",
        "match_pairs": "pair_matching",
        "categorize": "contrast_classification",
        "write": "open_production",
        "speak": "spoken_production",
    }
    normalized_requests = yaml.safe_load(fixture_exercise_requests())
    for request, exercise in zip(normalized_requests, exercises, strict=True):
        request["evidence_route"] = route_by_operation[exercise["op"]]
    FakeRichAuthorAgent.normalized_payload = {
        "lesson_md": (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
        "exercise_requests_yaml": yaml.safe_dump(normalized_requests, allow_unicode=True, sort_keys=False),
    }
    FakeRichAuthorAgent.exercise_payload = {
        "exercises_yaml": yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False)
    }

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-slot-repair",
            closed_task_verifier=FakeClosedTaskVerifier(),
        )
    finally:
        FakeRichAuthorAgent.normalized_payload = None
        FakeRichAuthorAgent.exercise_payload = None

    normalized = yaml.safe_load((result.source_dir / "exercises.yaml").read_text(encoding="utf-8"))
    repaired = [item for item in normalized if item["handle"] in {item["handle"] for item in choose_items}]
    prompts = [item["prompt_md"] for item in repaired[:3]]
    assert "[BLANK]" in prompts[0]
    assert "[BLANK]?" in prompts[1]
    assert "After kan, use the infinitive without å." in prompts[2]


def test_complete_exercises_given_write_model_extras_expect_preserves_example_as_explanation():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "write-note",
                "op": "write",
                "prompt_md": "Write a short note.",
                "sample_answer_md": "**Jeg kommer på fredag.**",
                "feedback": "Check the time expression.",
                "response_language": "nb",
                "judge_prompt": "Check the criteria only.",
                "criteria": [{"id": "meaning", "instruction": "The meaning is clear."}],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-writing"}]},
        )
    )[0]

    assert "sample_answer_md" not in repaired
    assert "feedback" not in repaired
    assert repaired["response_language"] == "no"
    assert "Example answer:" in repaired["explanation_md"]
    assert "Feedback:" in repaired["explanation_md"]


def test_validate_exercise_requests_given_operation_fields_expect_rejects_detail_leak():
    requests = """
- handle: say-question
  objective_ref: obj-question-order
  evidence_route: spoken_production
  bloom: apply
  evidence: Produce the spoken question for a taught cue.
  op: speak
"""
    with pytest.raises(ValidationError):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: say-question}}",
        )


def test_validate_exercise_requests_given_verbose_legacy_handoff_expect_rejects():
    requests = """
- handle: say-question
  objective_ref: obj-question-order
  evidence_route: spoken_production
  bloom: apply
  evidence: Produce the spoken question for a taught cue.
  intent: Produce a spoken question.
  context: A conversation.
  learner_action: Say the question.
  success_criteria:
    - The question is grammatical.
  source_section: sec-model
"""
    with pytest.raises(ValueError, match="verbose legacy field"):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: say-question}}",
        )


def test_validate_exercise_requests_given_compact_handoff_expect_canonical_yaml():
    requests = """
- handle: say-question
  objective_ref: obj-question-order
  evidence_route: spoken_production
  bloom: apply
  evidence: Produce the spoken question for a taught cue.
"""

    validated = rich.validate_exercise_requests(
        requests,
        {"obj-question-order"},
        "{{exercise: say-question}}",
    )

    assert yaml.safe_load(validated) == [
        {
            "handle": "say-question",
            "objective_ref": "obj-question-order",
            "bloom": "apply",
            "evidence_route": "spoken_production",
            "evidence": "Produce the spoken question for a taught cue.",
        }
    ]


def test_validate_exercise_requests_given_new_handoff_without_evidence_route_expect_rejects():
    requests = """
- handle: say-question
  objective_ref: obj-question-order
  bloom: apply
  evidence: Produce the spoken question for a taught cue.
"""
    with pytest.raises(ValidationError):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: say-question}}",
        )


def test_validate_exercise_requests_given_unknown_evidence_route_expect_rejects():
    requests = """
- handle: say-question
  objective_ref: obj-question-order
  evidence_route: imaginary_route
  bloom: apply
  evidence: Produce the spoken question for a taught cue.
"""
    with pytest.raises(ValueError, match="unknown evidence_route"):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: say-question}}",
        )


def test_validate_exercise_requests_given_route_bloom_mismatch_expect_rejects_before_exercise_author():
    requests = """
- handle: match-question
  objective_ref: obj-question-order
  evidence_route: pair_matching
  bloom: understand
  evidence: Match the taught question with its meaning.
"""

    with pytest.raises(ValueError, match="incompatible with evidence_route"):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: match-question}}",
        )


def test_validate_exercise_requests_given_marker_order_changed_expect_rejects_silent_reorder():
    requests = """
- handle: first-request
  objective_ref: obj-question-order
  evidence_route: meaning_selection
  bloom: understand
  evidence: Recognise the taught pattern in isolation.
- handle: second-request
  objective_ref: obj-question-order
  evidence_route: sentence_construction
  bloom: apply
  evidence: Use the taught pattern in a new sentence.
"""
    with pytest.raises(ValueError, match="set or order"):
        rich.validate_exercise_requests(
            requests,
            {"obj-question-order"},
            "{{exercise: second-request}}\n{{exercise: first-request}}",
        )


def test_rich_generation_graph_given_invalid_route_bloom_handoff_expect_retries_before_exercise_author(tmp_path: Path):
    _write_job_config(tmp_path)
    requests = yaml.safe_load(fixture_exercise_requests())
    requests[0]["evidence_route"] = "pair_matching"
    requests[0]["bloom"] = "understand"
    FakeRichAuthorAgent.normalized_payload = {
        "lesson_md": (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
        "exercise_requests_yaml": yaml.safe_dump(requests, allow_unicode=True, sort_keys=False),
    }
    FakeRichAuthorAgent.instances.clear()

    try:
        with pytest.raises(LlmParseException, match="incompatible with evidence_route"):
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="invalid-route-bloom",
            )
    finally:
        FakeRichAuthorAgent.normalized_payload = None

    assert len(FakeRichAuthorAgent.instances) == 3
    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stages"][-1]["name"] == "source_normalizer"
    assert receipt["stages"][-1]["status"] == "failed"


def test_complete_exercise_metadata_given_compact_handoff_expect_no_derived_reference():
    exercises = """
- handle: identify-question
  op: choose
  bloom: understand
  prompt_md: Choose the question.
  stem_md: Snakker du norsk?
  options:
    - id: yes
      text: A question
      correct: true
    - id: no
      text: A statement
      correct: false
"""

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-question-order"}]},
        )
    )[0]

    assert "derived_from" not in repaired


def test_validate_exercise_alignment_given_incompatible_operation_expect_rejects_retagging():
    requests = """
- handle: analyse-question
  objective_ref: obj-question-order
  evidence_route: contrast_classification
  bloom: apply
  evidence: Classify taught sentences into the contrast groups.
"""
    exercises = """
- handle: analyse-question
  op: categorize
  objective: obj-question-order
  bloom: apply
  prompt_md: Sort the sentence into its group.
  buckets:
    - bucket_id: group-a
      label: Group A
    - bucket_id: group-b
      label: Group B
  items:
    - item_id: item-1
      text: Snakker du norsk?
      bucket_id: group-a
"""
    with pytest.raises(ValueError, match="incompatible Bloom"):
        rich.validate_exercise_alignment(exercises, requests)


def test_validate_exercise_alignment_given_wrong_route_operation_expect_rejects_evidence_loss():
    requests = """
- handle: build-question
  objective_ref: obj-question-order
  evidence_route: sentence_construction
  bloom: apply
  evidence: Construct the question in the taught order.
"""
    exercises = """
- handle: build-question
  op: recall_fill
  objective: obj-question-order
  bloom: apply
  prompt_md: Complete the question.
  segments:
    - text_md: ""
    - blank_id: question
      options: ["Snakker du norsk?", "Du snakker norsk?"]
      answer_index: 0
"""
    with pytest.raises(ValueError, match="evidence_route"):
        rich.validate_exercise_alignment(exercises, requests)


def test_validate_exercise_alignment_given_authored_provenance_expect_tolerates_derived_from():
    requests = """
- handle: identify-question
  objective_ref: obj-question-order
  evidence_route: meaning_selection
  bloom: understand
  evidence: Recognise the taught question pattern in isolation.
"""
    exercises = """
- handle: identify-question
  op: choose
  objective: obj-question-order
  bloom: understand
  prompt_md: Choose the question.
  derived_from:
    - section_id: sec-contrast
  options:
    - id: yes
      text: A question
      correct: true
    - id: no
      text: A statement
      correct: false
"""

    rich.validate_exercise_alignment(exercises, requests)


def test_complete_exercises_given_flattened_recall_options_expect_moves_them_to_blank():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "fill-passive",
                "op": "recall_fill",
                "prompt_md": "Complete the sentence.",
                "segments": [
                    {"text_md": "Billettene"},
                    {"blank_id": "passive"},
                    {"text_md": "i går."},
                ],
                "options": [
                    {"id": "past", "text": "ble kontrollert"},
                    {"id": "routine", "text": "kontrolleres"},
                ],
                "answer_index": 0,
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-passive"}]},
        )
    )[0]

    blank = repaired["segments"][1]
    assert blank["options"] == ["ble kontrollert", "kontrolleres"]
    assert blank["answer_index"] == 0
    assert repaired["segments"][0]["text_md"].endswith(" ")
    assert repaired["segments"][2]["text_md"].startswith(" ")
    assert "options" not in repaired


def test_complete_exercises_given_legacy_recall_segments_expect_adds_boundary_spacing():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "speaker-choice",
                "op": "recall_fill",
                "prompt_md": "Complete the reply.",
                "segments": [
                    {"text_md": "Maja says: [BLANK] kommer."},
                    {
                        "blank_id": "speaker-choice_blank_1",
                        "options": ["Jeg", "Du"],
                        "answer_index": 0,
                    },
                    {"text_md": "Oskar answers next."},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-speaker"}]},
        )
    )[0]

    assert repaired["segments"][3]["text_md"] == " Oskar answers next."


def test_complete_exercises_given_literal_newline_escape_expect_inline_spacing():
    exercises = yaml.safe_dump(
        [
            {
                "handle": "escaped-break",
                "op": "recall_fill",
                "prompt_md": "Complete the reply.",
                "segments": [
                    {"text_md": r"Maja svarer.\n\n"},
                    {
                        "blank_id": "escaped",
                        "options": ["Jeg", "Du"],
                        "answer_index": 0,
                    },
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-escaped"}]},
        )
    )[0]

    assert "\\n" not in repaired["segments"][0]["text_md"]
    assert repaired["segments"][0]["text_md"] == "Maja svarer. "


def test_complete_exercises_given_id_without_handle_expect_id_becomes_handle():
    exercises = yaml.safe_dump(
        [
            {
                "id": "choose-article",
                "op": "choose",
                "prompt_md": "Choose the article.",
                "options": [
                    {"id": "en", "text": "en", "correct": True},
                    {"id": "et", "text": "et", "correct": False},
                ],
            }
        ],
        allow_unicode=True,
        sort_keys=False,
    )

    repaired = yaml.safe_load(
        rich.complete_exercise_metadata(
            exercises,
            {"objectives": [{"id": "obj-articles"}]},
        )
    )[0]

    assert repaired["handle"] == "choose-article"
    assert "id" not in repaired


def test_rich_generation_graph_given_context_dependent_exercise_expect_one_bounded_standalone_repair(tmp_path: Path):
    _write_job_config(tmp_path)
    exercises = _fixture_author_exercises()
    broken = json.loads(json.dumps(exercises))
    broken[0]["prompt_md"] = "Return to the dialogue and answer the first turn."
    repaired = _fixture_author_exercises()
    repaired[0]["prompt_md"] = "Answer «Snakker du norsk?» with a short Norwegian reply."
    FakeRichAuthorAgent.instances.clear()
    FakeRichAuthorAgent.exercise_payloads = [
        {"exercises_yaml": yaml.safe_dump(broken, allow_unicode=True, sort_keys=False)},
        {"exercises_yaml": yaml.safe_dump([repaired[0]], allow_unicode=True, sort_keys=False)},
    ]
    standalone = SequenceStandaloneVerifier(
        [
            {
                "status": "needs_human",
                "total": 7,
                "context_dependent": [broken[0]["handle"]],
                "deterministic_findings": [
                    {"id": broken[0]["handle"], "kind": "return_to", "field": "prompt", "reference": "Return to"}
                ],
            },
            {"status": "pass", "total": 7, "context_dependent": [], "deterministic_findings": []},
        ]
    )

    try:
        result = run_rich_generation_graph(
            plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
            output_root=tmp_path / "run",
            repo_root=tmp_path,
            run_id="rich-standalone-repair",
            closed_task_verifier=FakeClosedTaskVerifier(),
            standalone_verifier=standalone,
        )
    finally:
        FakeRichAuthorAgent.exercise_payloads = None

    generated = yaml.safe_load((result.source_dir / "exercises.yaml").read_text(encoding="utf-8"))
    assert generated[0]["prompt_md"] == "Answer «Snakker du norsk?» with a short Norwegian reply."
    assert len(standalone.calls) == 2
    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["standalone_exercise_review"]["status"] == "pass"
    assert receipt["stage_receipts"]["exercise_verification"]["semantic_repairs"] == 1
    assert receipt["stage_receipts"]["exercise_verification"]["standalone_status"] == "pass"


def test_rich_generation_graph_given_unavailable_standalone_reviewer_expect_fails_closed(tmp_path: Path):
    _write_job_config(tmp_path)
    FakeRichAuthorAgent.instances.clear()
    standalone = SequenceStandaloneVerifier(
        [{"status": "reviewer_unavailable", "total": 7, "context_dependent": [], "deterministic_findings": []}]
    )

    try:
        with pytest.raises(rich_workflow.ContentRemediationRequired) as excinfo:
            run_rich_generation_graph(
                plan_source=K_CATALOG_PACKAGE_FIXTURE_ROOT,
                output_root=tmp_path / "run",
                repo_root=tmp_path,
                run_id="rich-standalone-unavailable",
                closed_task_verifier=FakeClosedTaskVerifier(),
                standalone_verifier=standalone,
            )
    finally:
        FakeRichAuthorAgent.exercise_payloads = None

    assert excinfo.value.stage == "exercise_verification"
    receipt = json.loads((tmp_path / "run" / "llm_receipt.json").read_text(encoding="utf-8"))
    assert receipt["stage_receipts"]["exercise_verification"]["status"] == "reviewer_unavailable"
    assert receipt["standalone_exercise_review"]["status"] == "reviewer_unavailable"


@pytest.fixture(autouse=True)
def _standalone_review_pass(monkeypatch: pytest.MonkeyPatch) -> FakeStandaloneVerifier:
    """Default the blocking standalone-quality gate to a passing offline fake."""
    verifier = FakeStandaloneVerifier()

    def fake_review(lesson: dict, **_kwargs: object) -> dict:
        return verifier(lesson)

    monkeypatch.setattr(rich_workflow, "review_standalone_exercises", fake_review)
    return verifier


@pytest.fixture(autouse=True)
def _job_runner_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every configured job to the deterministic stage fake."""

    def fake_runner(job_name: str, **_kwargs: object) -> FakeRichAuthorAgent:
        return FakeRichAuthorAgent(name=job_name)

    monkeypatch.setattr(rich_workflow, "runner_for_job", fake_runner)


def _write_job_config(root: Path) -> None:
    """Write a minimal strict job config and its local agent templates."""
    agents = root / ".opencode" / "agents"
    agents.mkdir(parents=True)
    for template in ("lesson-author", "catalog-review", "exercise-author"):
        (agents / f"{template}.md").write_text(f"# {template}\n", encoding="utf-8")
    (root / "config.yaml").write_text(
        "llm:\n"
        "  timeout_seconds: 1800\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/test-model\n"
        "      variant: medium\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n"
        "    reviewer:\n"
        "      tier: standard\n"
        "      agent_template: catalog-review\n"
        "    normalizer:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n"
        "    exercise_author:\n"
        "      tier: standard\n"
        "      agent_template: exercise-author\n",
        encoding="utf-8",
    )


def _fixture_author_exercises() -> list[dict]:
    """Return the fixture exercises in the provenance-free author shape."""
    import json as _json

    exercises = _json.loads(
        _json.dumps(yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8")))
    )
    for exercise in exercises:
        exercise.pop("derived_from", None)
        if exercise.get("op") == "judge":
            exercise["bloom"] = "understand"
    return exercises
