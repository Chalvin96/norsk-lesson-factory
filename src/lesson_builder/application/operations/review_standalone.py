"""Entry point: ``review_standalone_exercises`` gates self-contained exercises.

The standalone-quality boundary receives the exact compiled learner-visible
exercise projection with no ``derived_from`` section context and no other
hidden lesson material. A deterministic unresolved-reference scan and a
configured model reviewer both judge whether each exercise, for every
operation including open ``write``/``speak`` tasks, is understandable and
performable from its own payload. Deterministic diagnostics supplement the
semantic review; neither replaces the human artifact boundary.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.domain.lesson.models.reviewer import ReviewerAgent
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from lesson_builder.domain.lesson.validation.standalone_references import scan_unresolved_references

K_STANDALONE_REVIEW_PASS = "pass"
K_STANDALONE_REVIEW_NEEDS_HUMAN = "needs_human"
K_STANDALONE_REVIEW_UNAVAILABLE = "reviewer_unavailable"
K_STANDALONE_REVIEW_INVALID = "reviewer_invalid"


class StandaloneItemReview(BaseModel):
    """One reviewer verdict for one exact learner-visible exercise payload."""

    model_config = ConfigDict(extra="forbid")

    id: str
    verdict: Literal["standalone", "context_dependent"]
    reason: str = ""


class StandaloneExerciseReview(BaseModel):
    """The reviewer's own standalone judgment for every exercise payload."""

    model_config = ConfigDict(extra="forbid")

    results: list[StandaloneItemReview] = Field(min_length=1)


class StandaloneReviewError(ValueError):
    """A strict standalone exercise review did not pass."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(
            f"standalone exercise review {report['status']}: {json.dumps(report, ensure_ascii=False, sort_keys=True)}"
        )


def review_standalone_exercises(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Judge every exercise payload in isolation from its own learner view.

    The deterministic unresolved-reference scan runs first and its findings
    stand on their own. The model reviewer then receives the same exact
    payload with no lesson context and returns one verdict per exercise. Any
    context-dependent verdict or deterministic finding yields
    ``needs_human``; a reviewer outage is never a pass in strict mode.
    """
    payload = project_standalone_review_payload(lesson)
    diagnostics = collect_deterministic_standalone_findings(payload)
    diagnostic_handles = sorted({item["id"] for item in diagnostics})
    report: dict[str, Any] = {
        "total": len(payload),
        "deterministic_findings": diagnostics,
        "context_dependent": [],
    }
    if not payload:
        report["status"] = K_STANDALONE_REVIEW_PASS
        _raise_if_strict(report, strict)
        return report
    try:
        review = reviewer_agent.structured(StandaloneExerciseReview).invoke(build_standalone_review_prompt(payload))
        model_handles = _validate_and_collect_context_dependent_handles(review, [item["id"] for item in payload])
    except LlmParseException as exc:
        invalid = dict(report)
        invalid["status"] = K_STANDALONE_REVIEW_INVALID
        invalid["error"] = f"{type(exc).__name__}: {exc}"
        _raise_if_strict(invalid, strict)
        return invalid
    except LlmException as exc:
        unavailable = dict(report)
        unavailable["status"] = K_STANDALONE_REVIEW_UNAVAILABLE
        unavailable["error"] = f"{type(exc).__name__}: {exc}"
        _raise_if_strict(unavailable, strict)
        return unavailable
    report["context_dependent"] = model_handles
    report["standalone_review"] = [item.model_dump(mode="json") for item in review.results]
    report["status"] = (
        K_STANDALONE_REVIEW_NEEDS_HUMAN if model_handles or diagnostic_handles else K_STANDALONE_REVIEW_PASS
    )
    _raise_if_strict(report, strict)
    return report


def project_standalone_review_payload(lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the exact learner-visible payload without any lesson context.

    The attempt projection deliberately omits answer assignments, feedback,
    open-response rubrics, and hidden speak targets. The reviewer sees exactly
    what a learner sees for the exercise itself and nothing from neighboring
    lesson sections or internal provenance.
    """
    return extract_attempt_questions(lesson)


def collect_deterministic_standalone_findings(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the unresolved-reference scan over compiled learner-visible payloads."""
    findings: list[dict[str, Any]] = []
    for question in payload:
        prompt_fields = {
            key: question[key] for key in ("prompt", "stem", "sentence") if isinstance(question.get(key), str)
        }
        restatement_text = " ".join(str(value) for value in prompt_fields.values())
        restatement_text += " " + _collect_payload_restatement_text(question)
        for finding in scan_unresolved_references(prompt_fields, restatement_text=restatement_text):
            findings.append({"id": question.get("id"), **finding})
    return findings


def build_standalone_review_prompt(payload: list[dict[str, Any]]) -> str:
    """Ask the reviewer to judge each payload with no lesson context attached."""
    return (
        "You are the standalone exercise-quality reviewer for a Norwegian lesson "
        "package. Each exercise below is the exact learner-visible payload a "
        "consuming app can show: its prompt, choices, tokens, blanks, or response "
        "constraints. Hidden answer assignments, rubrics, and recording targets "
        "are intentionally absent. No lesson sections, dialogues, examples, or internal "
        "provenance are attached, and none may be assumed. Judge each exercise "
        "only on whether a learner can understand and perform the task from this "
        "payload alone. Every fact, item, phrase, or situation the task needs must "
        "be restated in the payload itself; document position or an earlier "
        "section is not runtime context. Return one {id, verdict, reason} object "
        "per exercise in the results array, preserving ids exactly. Use verdict=standalone when the "
        "payload names the complete task locally; use verdict=context_dependent "
        "when the payload refers to a dialogue, turn, checkpoint, section, or "
        "items that are not restated in the payload. For context_dependent "
        "items, name the missing reference in the reason. Judge open write and "
        "speak tasks by the same rule: their learner-visible prompt must carry the "
        "complete task. Personal details explicitly left for the learner to choose are not "
        "missing context; open tasks need a clear action, not one predetermined utterance. "
        "Do not judge answer correctness, naturalness, or "
        "pedagogy here and do not omit an exercise.\n\n"
        f"EXERCISES:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _collect_payload_restatement_text(question: dict[str, Any]) -> str:
    """Collect choice-like learner-visible material that can restate a reference."""
    parts: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"judge_prompt", "criteria", "lesson_context"}:
                    continue
                collect(child)

    collect(question)
    return " ".join(parts)


def _validate_and_collect_context_dependent_handles(
    review: StandaloneExerciseReview,
    expected_ids: list[str],
) -> list[str]:
    """Validate reviewer coverage and return the context-dependent handles."""
    handles = [item.id for item in review.results]
    missing = [handle for handle in expected_ids if handle not in handles]
    unknown = sorted(set(handles) - set(expected_ids))
    duplicates = sorted({handle for handle in handles if handles.count(handle) > 1})
    if missing or unknown or duplicates:
        raise LlmParseException(
            "standalone review did not return exactly one verdict per exercise: "
            f"missing={missing!r}, unknown={unknown!r}, duplicates={duplicates!r}"
        )
    return sorted(item.id for item in review.results if item.verdict == "context_dependent")


def _raise_if_strict(report: dict[str, Any], strict: bool) -> None:
    """Fail closed for a fresh package while preserving a structured report."""
    if strict and report.get("status") != K_STANDALONE_REVIEW_PASS:
        raise StandaloneReviewError(report)


__all__ = [
    "K_STANDALONE_REVIEW_INVALID",
    "K_STANDALONE_REVIEW_NEEDS_HUMAN",
    "K_STANDALONE_REVIEW_PASS",
    "K_STANDALONE_REVIEW_UNAVAILABLE",
    "StandaloneExerciseReview",
    "StandaloneItemReview",
    "StandaloneReviewError",
    "build_standalone_review_prompt",
    "collect_deterministic_standalone_findings",
    "project_standalone_review_payload",
    "review_standalone_exercises",
]
