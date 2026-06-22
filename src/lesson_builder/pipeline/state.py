"""Lesson-QA graph state. Not a check itself — defines the per-slug back-half
state schema, its reducers, and the routing helpers the graph wires up.

Entry point: ``LessonQAState`` (the TypedDict the graph is parameterized over)
plus the pure helpers ``route_after_aggregate``, ``route_after_fix``,
``blocking_fingerprint``, and ``lesson_hash``.
"""

from __future__ import annotations

import hashlib
import json
import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel

from lesson_builder.pipeline.checks.result import CheckResult

IssueDict = dict[str, Any]

AttemptKind = Literal["fix", "regenerate", "signoff_retry"]

ParkStatus = Literal["running", "parked", "deferred", "accepted", "abandoned"]

K_ROUTER_HUMAN = "human_gate"
K_ROUTER_FIX = "fix"
K_ROUTER_REGRESSION = "regression"
K_ROUTER_EXPORT = "export"
K_ROUTER_REGENERATE = "regenerate"

K_GRAPH_MAX_FIXES = 2
K_GRAPH_MAX_REGENERATES = 1
K_GRAPH_MAX_SIGNOFF_RETRIES = 1

# AttemptRecord.failure_kind values. Distinguishes a genuine LLM-side failure
# (the model never produced a usable repair) from a model that returned the
# lesson unchanged because it chose to no-op. ``None`` = success (changed lesson).
K_FIXER_FAILURE_BACKEND_DOWN = "llm_backend_down"
K_FIXER_FAILURE_QUOTA = "llm_quota"
K_FIXER_FAILURE_PARSE = "llm_parse"
K_FIXER_NO_CHANGE = "no_change"


class AttemptRecord(BaseModel):
    """One entry in the ``attempts`` ledger: a bounded retry with its reason.

    ``failure_kind`` distinguishes a genuine author-side LLM failure
    (``"llm_backend_down"`` / ``"llm_quota"`` / ``"llm_parse"``) from a model that
    returned the lesson unchanged because it chose to no-op (``"no_change"``).
    ``None`` means the attempt produced a changed lesson (success).
    """

    kind: AttemptKind
    reason: str
    ts: str
    lesson_hash_before: str
    lesson_hash_after: str
    blocking_fingerprint: str | None = None
    failure_kind: str | None = None


class LessonQAState(TypedDict, total=False):
    """Per-slug Lesson-QA back-half state. Blob-in-state; single writer per field."""

    # Inputs resolved once at entry -> downstream nodes are pure functions of state.
    slug: str
    run_id: str
    requirements: dict[str, Any] | None
    baseline: dict[str, Any] | None
    recorded_requirements_hash: str | None

    # The lesson under review. Blob-in-state for self-contained resume.
    lesson: dict[str, Any]

    # The router reads only current_issues. issue_history is append-only audit.
    current_issues: list[IssueDict]
    issue_history: Annotated[list[IssueDict], operator.add]

    # Typed retry ledger; collapses fix_count/regenerate_count/signoff_retry.
    attempts: Annotated[list[dict[str, Any]], operator.add]

    # Advisory review surfaces supplied by the caller when available.
    pedagogy_review: dict[str, Any] | None
    objective_alignment_review: dict[str, Any] | None
    answer_review: dict[str, Any] | None
    naturalness_review: dict[str, Any] | None
    signoff_score: float | None

    # LLM surface health: maps surface name ("pedagogy"/"objective_alignment"/
    # "answer"/"fixer") to a failure kind ("backend_down"/"quota"/"parse").
    # Absent/empty means no known failures. Surfaced in the human-gate payload
    # so a reviewer-outage does not silently read as "clean".
    llm_status: dict[str, str]

    # Post-fix regression verdict (structured), set by the regression node.
    regression_result: dict[str, Any] | None

    # Park/terminal bookkeeping for the human-gate lifecycle.
    park_status: ParkStatus
    human_decision: dict[str, Any] | None
    ledger_status: str
    export_path: str | None

    # Run-scoped IO paths (repo root + acceptance-log path). Resolved at entry so
    # nodes stay pure and testable without touching the repo tree by default.
    repo_root: str
    acceptance_log_path: str

    # Optional write root for exported artifacts (``data/lessons``, ``dist/lessons``).
    # When set, the exporter writes there instead of under ``repo_root`` -- the
    # loader still reads from ``repo_root`` either way. Used to scratch-isolate
    # ad-hoc/demo runs from the tracked repo tree (see ``cli.py`` --commit).
    output_root: str | None


def lesson_hash(lesson: dict[str, Any]) -> str:
    """Stable content hash of the lesson aggregate, used by the convergence guard."""
    payload = json.dumps(lesson, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def actionable_issues(issues: list[IssueDict]) -> list[IssueDict]:
    """Filter to issues that should drive revision routing/convergence."""
    return [issue for issue in issues if issue.get("is_blocking") or issue.get("revision_target")]


def blocking_fingerprint(issues: list[IssueDict]) -> str:
    """Stable fingerprint of the actionable-issue set (check_id+unit_id, sorted).

    Two passes with the same fingerprint mean the fix did not change which
    actionable issues remain — the convergence guard escalates to human.
    """
    keys = sorted(
        f"{issue.get('check_id', '')}+{issue.get('unit_id', '')}"
        for issue in issues
        if issue.get("is_blocking") or issue.get("revision_target")
    )
    return "sha256:" + hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()


def blocking_issues(issues: list[IssueDict]) -> list[IssueDict]:
    """Filter to load-bearing blockers only (advisory verdicts never route)."""
    return [issue for issue in issues if issue.get("is_blocking")]


def count_attempts(attempts: list[dict[str, Any]], kind: AttemptKind) -> int:
    """Count prior attempts of one kind (fix / regenerate / signoff_retry)."""
    return sum(1 for attempt in attempts if attempt.get("kind") == kind)


def route_after_aggregate(state: LessonQAState) -> str:
    """Router: actionable issues → fix (if under cap, else regenerate → human).

    Advisory/log-only issues never route.
    """
    actionable = actionable_issues(state.get("current_issues", []))
    if not actionable:
        return K_ROUTER_REGRESSION

    fixes_used = count_attempts(state.get("attempts", []), "fix")
    if fixes_used < K_GRAPH_MAX_FIXES:
        return K_ROUTER_FIX
    regenerates_used = count_attempts(state.get("attempts", []), "regenerate")
    if regenerates_used < K_GRAPH_MAX_REGENERATES:
        return K_ROUTER_REGENERATE
    return K_ROUTER_HUMAN


def route_after_fix(state: LessonQAState) -> str:
    """Re-enter checks unless the fix stalled.

    Same lesson-hash before/after, or the same actionable fingerprint appearing
    twice in ``attempts``, means the loop is stuck → human (``convergence_failure``).

    Robustness: ``attempts[-1]`` is NOT assumed to be the fix just run. A
    human-edit resume or a foreign checkpoint can leave a stale/non-fix entry at
    the tail. Scan backwards for the latest ``fix`` attempt and apply the
    convergence check against THAT; if none exists, escalate to human.
    """
    attempts = state.get("attempts", [])
    if not attempts:
        return K_ROUTER_HUMAN
    fix_indices = [i for i, a in enumerate(attempts) if a.get("kind") == "fix"]
    if not fix_indices:
        return K_ROUTER_HUMAN
    last_fix = attempts[fix_indices[-1]]
    if last_fix.get("lesson_hash_before") == last_fix.get("lesson_hash_after"):
        return K_ROUTER_HUMAN
    fingerprint = last_fix.get("blocking_fingerprint")
    if fingerprint is not None:
        prior_fix_fingerprints = [
            attempts[i].get("blocking_fingerprint") for i in fix_indices[:-1]
        ]
        if fingerprint in prior_fix_fingerprints:
            return K_ROUTER_HUMAN
    return "checks"


def route_after_human(state: LessonQAState) -> str:
    """Human-gate resume router. Accept → export; edit → re-check; defer/respond → END.

    ``accept`` is refused (re-parked back to ``human_gate``) when load-bearing
    blockers remain in ``current_issues`` and the decision did not set
    ``"override": True``. This cannot infinite-loop on its own: re-parking only
    re-issues the interrupt, and the human must change the decision (set
    ``override`` or address the blockers) to make forward progress. ``override``
    lets ``accept`` proceed anyway; ``export_node`` records that as a
    non-baseline ``accepted_override`` ledger status (see
    ``lesson_acceptance_log.py``).
    """
    decision = state.get("human_decision") or {}
    status = decision.get("status")
    if status == "accept":
        remaining_blockers = blocking_issues(state.get("current_issues", []))
        if remaining_blockers and not decision.get("override"):
            return K_ROUTER_HUMAN
        return K_ROUTER_EXPORT
    if status == "edit":
        return "checks"
    # Respond is treated the same as defer.
    return END_LITERAL


END_LITERAL = "END"


def thread_id_for(slug: str, run_id: str) -> str:
    """Stable thread id: ``f"{slug}:{run_id}"``.

    Resilient to a ``run_id`` that already carries a ``slug:`` prefix. The chat
    operator LLM can echo the full thread_id it saw in its prompt (e.g.
    ``"adjective_agreement:f5067cffb455"``) back as the ``run_id``; without this
    guard ``thread_id_for`` would produce a duplicated-slug id like
    ``"adjective_agreement:adjective_agreement:f5067cffb455"`` that never matches
    a checkpointer thread. Stripping a leading ``slug:`` keeps the stored thread
    ids consistent regardless of which caller feeds the run_id.
    """
    prefix = f"{slug}:"
    if run_id.startswith(prefix):
        run_id = run_id[len(prefix):]
    return f"{slug}:{run_id}"


def to_issue_dict(result: CheckResult) -> IssueDict:
    """Project a CheckResult into the dict form stored in state.

    ``is_blocking`` is a computed property on CheckResult (severity=blocker AND not
    advisory AND not revision_target), so it is NOT in ``model_dump()``. Routers
    read ``issue["is_blocking"]`` and ``issue["revision_target"]`` so we serialize
    the computed blocker bit explicitly here once, at the boundary.
    """
    issue = result.model_dump()
    issue["is_blocking"] = result.is_blocking
    return issue


__all__ = [
    "AttemptKind",
    "AttemptRecord",
    "ParkStatus",
    "LessonQAState",
    "IssueDict",
    "lesson_hash",
    "actionable_issues",
    "blocking_fingerprint",
    "blocking_issues",
    "count_attempts",
    "route_after_aggregate",
    "route_after_fix",
    "route_after_human",
    "thread_id_for",
    "to_issue_dict",
    "K_ROUTER_HUMAN",
    "K_ROUTER_FIX",
    "K_ROUTER_REGRESSION",
    "K_ROUTER_EXPORT",
    "K_ROUTER_REGENERATE",
    "K_GRAPH_MAX_FIXES",
    "K_GRAPH_MAX_REGENERATES",
    "K_GRAPH_MAX_SIGNOFF_RETRIES",
    "K_FIXER_FAILURE_BACKEND_DOWN",
    "K_FIXER_FAILURE_QUOTA",
    "K_FIXER_FAILURE_PARSE",
    "K_FIXER_NO_CHANGE",
    "END_LITERAL",
]
