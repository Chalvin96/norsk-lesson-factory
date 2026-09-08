"""Entry points: ``verify_exercises``, ``verify_attempt_surface``, ``verify_open_rubrics``, and ``verify_exercise_package`` check exercises before parking.

The verifier is deliberately exercise-only. It receives the compiled lesson's
learner-visible payload through the existing keyless answer-review projection,
compares the reviewer's independently solved answers with the authored key,
and blocks semantic findings about factual premises, competing answers, distractors,
retrieval scaffolding, reconstructed output, or rehearsal claims for human review.
and never has permission to edit lesson prose.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from lesson_builder.application.operations.review_lesson import answer_review
from lesson_builder.application.operations.review_lesson import attempt_review
from lesson_builder.application.operations.review_lesson import open_rubric_review
from lesson_builder.application.operations.review_lesson import open_semantic_review
from lesson_builder.domain.lesson.models.reviewer import ReviewerAgent
from lesson_builder.domain.lesson.validation.checks.validators.answer_valid import answer_valid_check
from lesson_builder.domain.lesson.validation.review_payloads import build_expected_answers

K_EXERCISE_REVIEW_PASS = "pass"
K_EXERCISE_REVIEW_NEEDS_HUMAN = "needs_human"
K_EXERCISE_REVIEW_UNAVAILABLE = "reviewer_unavailable"
K_EXERCISE_REVIEW_UNVERIFIED_OPEN = "unverified_open"
K_EXERCISE_REVIEW_OPEN_OPERATIONS = frozenset({"speak", "write"})


class ExerciseReviewError(ValueError):
    """A strict fresh-package exercise review did not pass."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(
            f"exercise package verifier {report['status']}: {json.dumps(report, ensure_ascii=False, sort_keys=True)}"
        )


class AttemptSurfaceReviewError(ExerciseReviewError):
    """A strict standalone attempt-surface review did not pass."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        ValueError.__init__(
            self, f"attempt surface review {report['status']}: {json.dumps(report, ensure_ascii=False, sort_keys=True)}"
        )


class OpenRubricReviewError(ExerciseReviewError):
    """A strict open-write rubric review did not pass."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        ValueError.__init__(
            self,
            f"open rubric review {report['status']}: {json.dumps(report, ensure_ascii=False, sort_keys=True)}",
        )


def verify_exercises(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Verify every deterministic exercise using a blinded independent solve.

    Open ``speak``/``write`` tasks are not part of the closed answer oracle and
    therefore do not make the review unavailable. A package whose exercises are
    all open reports ``unverified_open`` instead of a generic pass: open tasks
    stay at the human review boundary. A reviewer outage is never treated as a
    pass in strict mode.
    """
    expected = build_expected_answers(lesson)
    lesson_hash = _lesson_hash(lesson)
    open_handles = _open_exercise_handles(lesson)
    if not expected:
        if open_handles:
            return {
                "status": K_EXERCISE_REVIEW_UNVERIFIED_OPEN,
                "lesson_hash": lesson_hash,
                "total": 0,
                "matches": 0,
                "open_handles": open_handles,
                "message": (
                    "package contains only open write/speak tasks; open tasks stay "
                    "at the human review boundary and are not machine-verified"
                ),
            }
        return {
            "status": K_EXERCISE_REVIEW_PASS,
            "lesson_hash": lesson_hash,
            "total": 0,
            "matches": 0,
            "message": "no closed exercises require an answer review",
        }

    payload = answer_review(lesson=lesson, agent=reviewer_agent)
    if payload is None:
        report = {
            "status": K_EXERCISE_REVIEW_UNAVAILABLE,
            "lesson_hash": lesson_hash,
            "total": len(expected),
            "matches": 0,
            "message": "answer reviewer returned no usable response",
        }
        _raise_if_strict(report, strict)
        return report

    _results, comparison = answer_valid_check(lesson, payload, strict=True)
    semantic_issues = _semantic_issue_report(payload)
    status = K_EXERCISE_REVIEW_PASS if comparison["passed"] and not semantic_issues else K_EXERCISE_REVIEW_NEEDS_HUMAN
    report = {
        "status": status,
        "lesson_hash": lesson_hash,
        "total": comparison["total"],
        "matches": comparison["matches"],
        "missing": comparison["missing"],
        "mismatches": comparison["mismatches"],
        "ambiguous": comparison["ambiguous"],
        "unanswerable": comparison["unanswerable"],
        "extra": comparison["extra"],
        "duplicates": comparison["duplicates"],
        "semantic_issues": semantic_issues,
        "open_handles": open_handles,
        "open_tasks_unverified": bool(open_handles),
    }
    _raise_if_strict(report, strict)
    return report


def verify_attempt_surface(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Require every exercise to be clear from its true attempt-time projection."""
    handles = _exercise_handles(lesson)
    if not handles:
        return {
            "status": "not_applicable",
            "total": 0,
            "clear": 0,
            "missing": [],
            "ambiguous": [],
            "unanswerable": [],
            "extra": [],
            "duplicates": [],
        }
    payload = attempt_review(lesson=lesson, agent=reviewer_agent)
    if payload is None:
        report = _build_unavailable_surface_report(handles)
        _raise_surface_if_strict(report, strict, AttemptSurfaceReviewError)
        return report
    report = _build_surface_report(handles, payload, count_key="clear")
    _raise_surface_if_strict(report, strict, AttemptSurfaceReviewError)
    return report


def verify_open_rubrics(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Require every open-write rubric to cover visible obligations both ways."""
    handles = _open_write_handles(lesson)
    if not handles:
        return {"status": "not_applicable", "total": 0, "open_write_handles": []}
    payload = open_rubric_review(lesson=lesson, agent=reviewer_agent)
    if payload is None:
        report = _build_unavailable_surface_report(handles, include_open_write_handles=True)
        _raise_surface_if_strict(report, strict, OpenRubricReviewError)
        return report
    report = _build_surface_report(
        handles,
        payload,
        count_key="passed",
        include_open_write_handles=True,
    )
    _raise_surface_if_strict(report, strict, OpenRubricReviewError)
    return report


def verify_open_semantics(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Block grounded semantic defects in open-task evidence claims."""
    handles = _open_exercise_handles(lesson)
    if not handles:
        return {"status": "not_applicable", "total": 0, "semantic_issues": []}
    payload = open_semantic_review(lesson=lesson, agent=reviewer_agent)
    if payload is None:
        report = {
            "status": K_EXERCISE_REVIEW_UNAVAILABLE,
            "total": len(handles),
            "missing": handles,
            "semantic_issues": [],
        }
        _raise_if_strict(report, strict)
        return report
    report = _build_semantic_report(handles, payload)
    if strict and report["status"] != K_EXERCISE_REVIEW_PASS:
        raise ExerciseReviewError(report)
    return report


def verify_exercise_package(
    lesson: dict[str, Any],
    *,
    reviewer_agent: ReviewerAgent,
    strict: bool = False,
) -> dict[str, Any]:
    """Run every exercise review surface and raise one atomic strict report.

    A mixed closed/open package is unverified_open only after closed answers,
    the standalone attempt surface, and open-write rubrics all pass. That status
    describes the evidence boundary; it never verifies arbitrary learner prose
    or speech responses.
    """
    answer_report = verify_exercises(lesson, reviewer_agent=reviewer_agent, strict=False)
    attempt_report = verify_attempt_surface(lesson, reviewer_agent=reviewer_agent, strict=False)
    rubric_report = verify_open_rubrics(lesson, reviewer_agent=reviewer_agent, strict=False)
    semantic_report = verify_open_semantics(lesson, reviewer_agent=reviewer_agent, strict=False)
    reports = {
        "answer": answer_report,
        "attempt_surface": attempt_report,
        "open_rubrics": rubric_report,
        "open_semantics": semantic_report,
    }
    statuses = [report.get("status") for report in reports.values()]
    open_handles = answer_report.get("open_handles", [])
    if not isinstance(open_handles, list):
        open_handles = []
    if "reviewer_unavailable" in statuses:
        status = "reviewer_unavailable"
    elif any(report_status == "needs_human" for report_status in statuses):
        status = "needs_human"
    elif K_EXERCISE_REVIEW_UNVERIFIED_OPEN in statuses or open_handles:
        # Open responses have passed their standalone and rubric surfaces, but
        # remain explicitly outside the closed answer oracle. This applies to
        # mixed packages too, where the answer surface itself still passes for
        # the closed subset.
        status = K_EXERCISE_REVIEW_UNVERIFIED_OPEN
    else:
        status = "pass"
    report = {
        "status": status,
        "lesson_hash": _lesson_hash(lesson),
        "total": len(_exercise_handles(lesson)),
        "exercise_count": len(_exercise_handles(lesson)),
        "matches": answer_report.get("matches", 0),
        "open_handles": open_handles,
        "open_tasks_unverified": bool(open_handles),
        "answer_review": answer_report,
        "attempt_surface": attempt_report,
        "open_rubrics": rubric_report,
        "open_semantics": semantic_report,
        "missing": _merge_report_lists(reports, "missing"),
        "mismatches": _merge_report_lists(reports, "mismatches"),
        "semantic_issues": _merge_report_lists(reports, "semantic_issues"),
        "ambiguous": _merge_report_lists(reports, "ambiguous"),
        "unanswerable": _merge_report_lists(reports, "unanswerable"),
        "duplicates": _merge_report_lists(reports, "duplicates"),
        "extra": _merge_report_lists(reports, "extra"),
    }
    if strict and status not in {K_EXERCISE_REVIEW_PASS, K_EXERCISE_REVIEW_UNVERIFIED_OPEN}:
        raise ExerciseReviewError(report)
    return report


def _build_surface_report(
    handles: list[str],
    payload: dict[str, Any],
    *,
    count_key: str,
    include_open_write_handles: bool = False,
) -> dict[str, Any]:
    """Build the common report for an attempt or open-rubric review."""
    duplicates, by_id = _surface_check_index(payload)
    missing = [handle for handle in handles if handle not in by_id]
    unclear = [handle for handle in handles if handle in by_id and by_id[handle].get("status") != "clear"]
    extra = sorted(set(by_id) - set(handles), key=str)
    report: dict[str, Any] = {
        "status": "pass" if not missing and not unclear and not extra and not duplicates else "needs_human",
        "total": len(handles),
        count_key: len(handles) - len(missing) - len(unclear),
        "missing": missing,
        "ambiguous": [handle for handle in unclear if by_id[handle].get("status") == "ambiguous"],
        "unanswerable": [handle for handle in unclear if by_id[handle].get("status") == "unanswerable"],
        "extra": extra,
        "duplicates": duplicates,
        "reasons": {handle: by_id[handle].get("reason", "") for handle in unclear},
    }
    if include_open_write_handles:
        report["open_write_handles"] = handles
    return report


def _semantic_issue_report(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return grounded semantic findings emitted for closed exercises."""
    answers = payload.get("answers", []) if isinstance(payload, dict) else []
    if not isinstance(answers, list):
        return []
    return [
        {
            "id": item.get("id"),
            "issues": item.get("semantic_issues"),
        }
        for item in answers
        if isinstance(item, dict) and item.get("semantic_issues")
    ]


def _build_semantic_report(handles: list[str], payload: dict[str, Any]) -> dict[str, Any]:
    """Index open-task semantic findings and fail on any grounded issue."""
    duplicates, by_id = _surface_check_index(payload)
    missing = [handle for handle in handles if handle not in by_id]
    extra = sorted(set(by_id) - set(handles), key=str)
    findings = [
        {"id": handle, "issues": by_id[handle].get("semantic_issues", [])}
        for handle in handles
        if handle in by_id and by_id[handle].get("semantic_issues")
    ]
    status = (
        K_EXERCISE_REVIEW_PASS
        if not missing and not extra and not duplicates and not findings
        else K_EXERCISE_REVIEW_NEEDS_HUMAN
    )
    return {
        "status": status,
        "total": len(handles),
        "clear": len(handles) - len(missing),
        "missing": missing,
        "extra": extra,
        "duplicates": duplicates,
        "semantic_issues": findings,
        "open_handles": handles,
    }


def _surface_check_index(payload: dict[str, Any]) -> tuple[list[Any], dict[Any, dict[str, Any]]]:
    """Index reviewer checks and identify duplicate response IDs."""
    checks = payload.get("checks", []) if isinstance(payload, dict) else []
    check_ids = [item.get("id") for item in checks if isinstance(item, dict) and item.get("id") is not None]
    duplicates = sorted((item_id for item_id, count in Counter(check_ids).items() if count > 1), key=str)
    by_id = {item.get("id"): item for item in checks if isinstance(item, dict) and item.get("id") is not None}
    return duplicates, by_id


def _build_unavailable_surface_report(
    handles: list[str],
    *,
    include_open_write_handles: bool = False,
) -> dict[str, Any]:
    """Build a fail-closed report when a reviewer cannot respond."""
    report: dict[str, Any] = {"status": "reviewer_unavailable", "total": len(handles), "missing": handles}
    if include_open_write_handles:
        report["open_write_handles"] = handles
    return report


def _raise_surface_if_strict(
    report: dict[str, Any],
    strict: bool,
    error_type: type[ExerciseReviewError],
) -> None:
    """Raise the surface-specific error when strict mode requires a pass."""
    if strict and report["status"] != "pass":
        raise error_type(report)


def _raise_if_strict(report: dict[str, Any], strict: bool) -> None:
    """Fail closed for a fresh package while preserving a structured report."""
    if not strict:
        return
    if report["status"] == K_EXERCISE_REVIEW_UNVERIFIED_OPEN:
        # Open writing/speaking tasks are intentionally handed to the artifact
        # boundary.  They are not a verifier outage or a closed-answer failure.
        return
    if report["status"] != K_EXERCISE_REVIEW_PASS:
        raise ExerciseReviewError(report)


def _open_exercise_handles(lesson: dict[str, Any]) -> list[str]:
    """Return handles of open tasks that no closed answer oracle can verify."""
    handles: list[str] = []
    elements = lesson.get("elements")
    for element in elements if isinstance(elements, list) else []:
        if not isinstance(element, dict):
            continue
        if element.get("element_kind") != "exercise":
            continue
        operation = element.get("operation")
        if operation in K_EXERCISE_REVIEW_OPEN_OPERATIONS:
            handle = element.get("id")
            if isinstance(handle, str) and handle:
                handles.append(handle)
    return sorted(handles)


def _exercise_handles(lesson: dict[str, Any]) -> list[str]:
    """Return every exercise handle in stable compiled order."""
    return [
        element["id"]
        for element in lesson.get("elements", [])
        if isinstance(element, dict)
        and element.get("element_kind") == "exercise"
        and isinstance(element.get("id"), str)
    ]


def _open_write_handles(lesson: dict[str, Any]) -> list[str]:
    """Return write handles in stable lesson order."""
    return [
        element["id"]
        for element in lesson.get("elements", [])
        if isinstance(element, dict)
        and element.get("element_kind") == "exercise"
        and element.get("operation") == "write"
        and isinstance(element.get("id"), str)
    ]


def _merge_report_lists(reports: dict[str, dict[str, Any]], key: str) -> list[Any]:
    """Merge one report field while preserving stable first-seen order."""
    merged: list[Any] = []
    for report in reports.values():
        values = report.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if value not in merged:
                merged.append(value)
    return merged


def _lesson_hash(lesson: dict[str, Any]) -> str:
    """Hash the immutable compiled lesson for exercise-only repair checks."""
    encoded = json.dumps(lesson, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "AttemptSurfaceReviewError",
    "ExerciseReviewError",
    "OpenRubricReviewError",
    "verify_exercise_package",
    "K_EXERCISE_REVIEW_UNVERIFIED_OPEN",
    "verify_attempt_surface",
    "verify_open_rubrics",
    "verify_open_semantics",
    "verify_exercises",
]
