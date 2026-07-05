"""Entry point: `load_node`.

Node functions and node-only helpers for the lesson QA graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from lesson_builder.pipeline.calibration.rubric_floors import load_rubric_floors
from lesson_builder.pipeline.checks.gate_manager import (
    gate_advisory_results,
    gate_lesson_results,
)
from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.checks.validators.pedagogy import (
    pedagogy_check_from_review,
    pedagogy_percent,
)
from lesson_builder.pipeline.checks.validators.regression import regression_check
from lesson_builder.pipeline.graph_deps import (
    K_PARK_ACCEPTED,
    K_PARK_DEFERRED,
    K_PARK_PARKED,
    K_PARK_RUNNING,
    GraphDeps,
    _required_path,
)
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.state import (
    K_FIXER_FAILURE_BACKEND_DOWN,
    K_FIXER_FAILURE_PARSE,
    K_FIXER_FAILURE_QUOTA,
    K_FIXER_NO_CHANGE,
    AttemptKind,
    AttemptRecord,
    LessonQAState,
    actionable_issues,
    blocking_fingerprint,
    blocking_issues,
    lesson_hash,
    to_issue_dict,
)


def load_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Resolve lesson + requirements + baseline once at entry."""
    slug = state["slug"]
    loaded = deps.loader(slug, repo_root=_required_path(state, "repo_root"))
    return {
        "lesson": loaded.lesson,
        "requirements": loaded.requirements,
        "baseline": loaded.baseline_export,
        "recorded_requirements_hash": loaded.recorded_requirements_hash,
        "current_issues": [],
        "park_status": K_PARK_RUNNING,
    }


def judge_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Run the injected judge panel once; populate the advisory review keys.

    Runs only on the initial graph entry (load -> judge -> run_checks). It does
    NOT re-run inside the fix/regenerate loop, nor on human-edit re-entry
    (route_after_fix / route_after_human both re-enter at "run_checks" directly)
    -- advisory judge findings do not drive the deterministic fix loop, and
    re-judging every iteration would be costly. The review payloads computed
    here are carried in state and re-folded by every subsequent run_checks
    pass. Pedagogy is re-derived post-fix when rubric floors are load-bearing
    (Task A: see ``_maybe_rejudge_pedagogy``), so the original "Revisit when a
    judge becomes load-bearing" caveat is now resolved.

    Task B (cost): the deterministic gate runs FIRST. When the lesson has
    load-bearing hard blockers, the reviewer panel is SKIPPED (all reviews
    ``None``) -- spending three reviewer LLM calls on a structurally broken
    lesson is waste. The fix loop clears the blockers; pedagogy is then
    re-derived post-fix when floors are load-bearing (Task A), so a broken-then-
    fixed lesson still gets its pedagogy gate without burning the panel up-front.

    Task C: LLM surface failures are surfaced first-class via ``llm_status`` --
    a reviewer outage no longer silently reads as "clean" (see
    ``run_checks_node`` for the load-bearing + no-pedagogy-review human-gate
    reason).
    """
    lesson = state["lesson"]
    if _has_deterministic_hard_blockers(
        lesson,
        requirements=state.get("requirements"),
        baseline_export=state.get("baseline"),
        recorded_requirements_hash=state.get("recorded_requirements_hash"),
    ):
        # Task B: skip the reviewer panel on a structurally broken lesson.
        return {
            "pedagogy_review": None,
            "objective_alignment_review": None,
            "answer_review": None,
            "naturalness_review": None,
            "llm_status": {},
        }
    reviews = deps.judge(lesson)
    llm_status = dict(reviews.get("_llm_status") or {})
    return {
        "pedagogy_review": reviews.get("pedagogy_review"),
        "objective_alignment_review": reviews.get("objective_alignment_review"),
        "answer_review": reviews.get("answer_review"),
        "naturalness_review": reviews.get("naturalness_review"),
        "llm_status": llm_status,
    }


def run_checks_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Run deterministic gate + advisory folds; emit current_issues (overwrite).

    ``current_issues`` is overwritten each pass. ``issue_history`` keeps the
    append-only audit trail.

    Task C: when rubric floors are load-bearing AND there is no pedagogy review
    (the judge was skipped or failed), emit a human-gate revision target so the
    lesson does NOT silently read as "clean" -- a reviewer outage must surface.
    """
    lesson = state["lesson"]
    results = gate_lesson_results(
        lesson,
        requirements=state.get("requirements"),
        baseline_export=state.get("baseline"),
        recorded_requirements_hash=state.get("recorded_requirements_hash"),
    )
    results.extend(
        gate_advisory_results(
            lesson,
            pedagogy_review=state.get("pedagogy_review"),
            objective_alignment_review=state.get("objective_alignment_review"),
            answer_review=state.get("answer_review"),
            naturalness_review=state.get("naturalness_review"),
            repo_root=state.get("repo_root"),
        )
    )
    results.extend(_pedagogy_unavailable_issues(state))

    current_issues = [to_issue_dict(result) for result in results]
    return {"current_issues": current_issues, "issue_history": current_issues}


def fix_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Apply one author fix. Records an AttemptRecord; convergence guard reads it.

    Task A: when rubric floors are load-bearing, re-derive the pedagogy review
    from the post-fix lesson so ``rubric_floor_check`` scores the CURRENT review
    (not the stale pre-fix one) -- otherwise the loop cannot converge.
    Task C: LLM failures from the fixer are caught and recorded on the
    AttemptRecord as ``failure_kind`` (distinguished from a genuine no-op).
    """
    return _author_attempt(state, deps, kind="fix", reason="blocking_issues")


def regenerate_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Regenerate attempt (cap K_GRAPH_MAX_REGENERATES). Same shape as fix."""
    return _author_attempt(state, deps, kind="regenerate", reason="fix_budget_exhausted")


def regression_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Post-fix regression vs trusted baseline.

    The in-loop gate already ran ``regression_check``; this node re-runs it on the
    final lesson and stores a structured verdict for signoff/audit visibility.

    ``regression_check`` only ever emits ``severity="warning"`` results (it is
    non-blocking by design), so ``blocking_passed`` is always ``True`` here --
    there is never a load-bearing regression failure. ``has_warnings`` /
    ``severity`` make the non-blocking diff visible instead of reading as "no
    differences" when a diff is in fact present.
    """
    baseline = state.get("baseline")
    if not baseline:
        return {
            "regression_result": {
                "blocking_passed": True,
                "has_warnings": False,
                "severity": None,
                "diff": None,
                "reason": "no_baseline",
            }
        }
    results = regression_check(state["slug"], state["lesson"], baseline)
    if not results:
        return {
            "regression_result": {
                "blocking_passed": True,
                "has_warnings": False,
                "severity": None,
                "diff": None,
                "reason": "matches_baseline",
            }
        }
    return {
        "regression_result": {
            "blocking_passed": True,
            "has_warnings": True,
            "severity": "warning",
            "diff": [result.model_dump() for result in results],
            "reason": "warning_only",
        }
    }


def signoff_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Pedagogy signoff.

    Computes ``signoff_score`` from a pre-computed pedagogy review if one is
    present. Human review remains the final decision point.
    """
    pedagogy_review = state.get("pedagogy_review")
    if not pedagogy_review:
        return {"signoff_score": None}
    _, review = pedagogy_check_from_review(pedagogy_review)
    return {"signoff_score": pedagogy_percent(review)}


def human_gate_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Park for human decision. Resumes via Command(resume=decision)."""
    payload = _human_interrupt_payload(state)
    decision = interrupt(payload)
    return _resume_update_from_decision(state, decision)


def export_node(state: LessonQAState, deps: GraphDeps) -> dict[str, Any]:
    """Terminal export after human accept.

    Reached only when ``route_after_human`` let the accept through: either no
    load-bearing blockers remained, or the human decision set
    ``"override": True``. The override flag is forwarded to the exporter so it
    can record a non-baseline ``accepted_override`` ledger status instead of
    ``accepted`` (see ``lesson_acceptance_log.py``).
    """
    issues = blocking_issues(state.get("current_issues", []))
    corrections_applied = [
        attempt.get("kind", "") for attempt in state.get("attempts", []) if attempt.get("kind") in {"fix", "regenerate"}
    ]
    decision = state.get("human_decision") or {}
    override = bool(issues) and bool(decision.get("override"))
    output_root = state.get("output_root")
    export_path = deps.exporter(
        slug=state["slug"],
        lesson=state["lesson"],
        run_id=state.get("run_id", ""),
        signoff_score=state.get("signoff_score"),
        blocking_issues_messages=[issue.get("message", "") for issue in issues],
        corrections_applied=corrections_applied,
        repo_root=_required_path(state, "repo_root"),
        acceptance_log_path=_required_path(state, "acceptance_log_path"),
        override=override,
        output_root=Path(output_root) if output_root else None,
    )
    return {
        "park_status": K_PARK_ACCEPTED,
        "ledger_status": "accepted_override" if override else "accepted",
        "export_path": export_path,
    }


def _human_interrupt_payload(state: LessonQAState) -> dict[str, Any]:
    """Build the interrupt payload for human review.

    The surface fetches the full lesson separately. ``llm_status`` (Task C) makes
    a reviewer/fixer outage visible to the human reviewer instead of silently
    reading as "clean".
    """
    issues = state.get("current_issues", [])
    blocking = blocking_issues(issues)
    advisory = [issue for issue in issues if issue.get("advisory")]
    return {
        "action_request": {
            "action": "review_lesson",
            "args": {"slug": state.get("slug", "")},
        },
        "config": {
            "allow_accept": True,
            "allow_edit": True,
            "allow_respond": False,
            "allow_ignore": True,
        },
        "description": {
            "blocking_issues": [issue.get("message", "") for issue in blocking],
            "advisory_issues": [issue.get("message", "") for issue in advisory],
            "regression_result": state.get("regression_result"),
            "signoff_score": state.get("signoff_score"),
            "attempts": state.get("attempts", []),
            "llm_status": state.get("llm_status", {}),
        },
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _park_thread_node(state: LessonQAState) -> dict[str, Any]:
    """Persist the parked lifecycle status before the interrupting node runs."""
    return {"park_status": K_PARK_PARKED}


def _resume_update_from_decision(state: LessonQAState, decision: dict[str, Any] | None) -> dict[str, Any]:
    """Project the human resume payload into graph state before routing."""
    decision_payload = decision or {}
    status = decision_payload.get("status")
    update: dict[str, Any] = {
        "human_decision": decision_payload,
        "park_status": K_PARK_RUNNING if status in {"accept", "edit"} else K_PARK_DEFERRED,
    }
    edited_lesson = decision_payload.get("lesson")
    if status == "edit" and isinstance(edited_lesson, dict):
        update["lesson"] = edited_lesson
    return update


def _author_attempt(state: LessonQAState, deps: GraphDeps, *, kind: AttemptKind, reason: str) -> dict[str, Any]:
    """Shared fix/regenerate body: call the fixer, record the attempt, rejudge.

    Task A: when ``load_rubric_floors(repo_root).load_bearing`` is true and
    ``deps.rejudge_pedagogy`` is wired, re-derive the pedagogy review from the
    post-fix lesson so the rubric-floor check scores the CURRENT review.
    Task C: distinguish an LLM failure (the fixer raised) from a model no-op.
    """
    lesson_before = state["lesson"]
    issues = actionable_issues(state.get("current_issues", []))
    failure_kind: str | None = None
    try:
        lesson_after = deps.fixer(slug=state["slug"], lesson=lesson_before, issues=issues, kind=kind)
    except (BackendDownException, LlmQuotaException, LlmParseException) as exc:
        # Task C: the fixer failed first-class. Record the kind; no-op the lesson
        # so the convergence guard escalates to a human instead of shipping a
        # silently-unrepaired lesson.
        failure_kind = _fixer_failure_kind(exc)
        lesson_after = lesson_before
    if failure_kind is None and lesson_hash(lesson_before) == lesson_hash(lesson_after):
        # The model chose to return the lesson unchanged (genuine no-op).
        failure_kind = K_FIXER_NO_CHANGE
    record = AttemptRecord(
        kind=kind,
        reason=reason,
        ts=_now_iso(),
        lesson_hash_before=lesson_hash(lesson_before),
        lesson_hash_after=lesson_hash(lesson_after),
        blocking_fingerprint=blocking_fingerprint(issues),
        failure_kind=failure_kind,
    )
    update: dict[str, Any] = {"lesson": lesson_after, "attempts": [record.model_dump()]}
    # Task A: only re-derive pedagogy when the lesson actually CHANGED. The spec
    # says "after fix/regenerate change the lesson" -- a failed/no-op fix leaves
    # the lesson (and thus the prior pedagogy review) unchanged, so re-deriving
    # would be a wasted LLM call. The convergence guard escalates either way.
    if failure_kind is None:
        update.update(_maybe_rejudge_pedagogy(state, deps, lesson_after))
    return update


def _maybe_rejudge_pedagogy(state: LessonQAState, deps: GraphDeps, lesson_after: dict[str, Any]) -> dict[str, Any]:
    """Task A: re-derive the pedagogy review post-fix when floors are load-bearing.

    Returns a state delta (``{"pedagogy_review": ...}``) when re-derivation runs,
    or ``{}`` when it is skipped (floors not load-bearing, or no seam wired, or
    the producer failed -- the rubric check is then simply skipped for that pass).
    """
    if deps.rejudge_pedagogy is None:
        return {}
    if not _rubric_floors_load_bearing(state.get("repo_root")):
        return {}
    review = deps.rejudge_pedagogy(lesson_after)
    return {"pedagogy_review": review}


def _rubric_floors_load_bearing(repo_root: str | None) -> bool:
    """Resolve whether the rubric floors are load-bearing for ``repo_root``."""
    if not repo_root:
        return load_rubric_floors().load_bearing
    return load_rubric_floors(_rubric_floors_path(Path(repo_root))).load_bearing


def _rubric_floors_path(repo_root: Path) -> Path:
    # Local import keeps the graph module independent of the calibration package
    # at import time (avoids a circular import surface for test-only wiring).
    from lesson_builder.pipeline.calibration.rubric_floors import rubric_floors_path_for_repo

    return rubric_floors_path_for_repo(repo_root)


def _has_deterministic_hard_blockers(
    lesson: dict[str, Any],
    *,
    requirements: dict[str, Any] | None,
    baseline_export: dict[str, Any] | None,
    recorded_requirements_hash: str | None,
) -> bool:
    """Task B: true when the lesson has any load-bearing deterministic blocker.

    Used by ``judge_node`` to skip the reviewer panel on a structurally broken
    lesson (the fix loop will clear the blockers; pedagogy is re-derived
    post-fix when floors are load-bearing via Task A).
    """
    results = gate_lesson_results(
        lesson,
        requirements=requirements,
        baseline_export=baseline_export,
        recorded_requirements_hash=recorded_requirements_hash,
    )
    return any(result.is_blocking for result in results)


def _pedagogy_unavailable_issues(state: LessonQAState) -> list[CheckResult]:
    """Task C: human-gate reason when pedagogy is load-bearing but the reviewer failed.

    When rubric floors are load-bearing AND there is no pedagogy review in state
    AND ``llm_status`` records a pedagogy-surface failure (the judge ran but the
    reviewer backend was down / quota-exhausted / returned unparseable output),
    emit a revision target so the lesson does NOT silently read as "clean". The
    reviewer cannot be assumed to have passed a lesson it never produced a review
    for.

    This does NOT fire for the offline noop judge (``llm_status`` is empty: the
    judge intentionally produces nothing, it did not fail) or when the judge was
    skipped due to deterministic hard blockers (Task B: the hard blockers drive
    routing on their own, and Task A re-derives pedagogy once they clear).
    """
    if not _rubric_floors_load_bearing(state.get("repo_root")):
        return []
    if state.get("pedagogy_review"):
        return []
    llm_status = state.get("llm_status") or {}
    if "pedagogy" not in llm_status:
        return []
    return [
        CheckResult(
            check_id="pedagogy_unavailable",
            severity="blocker",
            revision_target=True,
            message=(
                "pedagogy reviewer unavailable -- not verified (judge failed with "
                f"{llm_status['pedagogy']!r}; see llm_status)"
            ),
            fix_hint="re-run the pedagogy reviewer once the backend is healthy",
        )
    ]


def _fixer_failure_kind(exc: Exception) -> str:
    """Map a fixer LLM exception to its AttemptRecord.failure_kind value."""
    if isinstance(exc, BackendDownException):
        return K_FIXER_FAILURE_BACKEND_DOWN
    if isinstance(exc, LlmQuotaException):
        return K_FIXER_FAILURE_QUOTA
    if isinstance(exc, LlmParseException):
        return K_FIXER_FAILURE_PARSE
    # Defensive: unexpected exception kind -- record a generic marker so the
    # AttemptRecord still distinguishes failure from no-op.
    return "llm_unknown"


__all__ = [
    "load_node",
    "judge_node",
    "run_checks_node",
    "fix_node",
    "regenerate_node",
    "regression_node",
    "signoff_node",
    "human_gate_node",
    "export_node",
    "_park_thread_node",
]
