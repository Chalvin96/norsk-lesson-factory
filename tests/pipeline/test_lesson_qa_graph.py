"""Tests for the Lesson-QA graph: routing, state behavior, and resume.

Routing and state tests are pure (no graph, no disk). The resume test drives the
compiled graph with a MemorySaver and a fake exporter to keep it off the repo
tree. A clean real lesson (ordinal_numbers) exercises the happy path; a seeded
bad lesson exercises the convergence guard.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_qa_graph import (
    GraphDeps,
    LoadedLesson,
    build_lesson_qa_graph,
    default_loader,
    noop_fixer,
    regression_node,
    run_checks_node,
)
from lesson_builder.pipeline.state import (
    END_LITERAL,
    K_GRAPH_MAX_FIXES,
    K_GRAPH_MAX_REGENERATES,
    K_ROUTER_EXPORT,
    K_ROUTER_FIX,
    K_ROUTER_HUMAN,
    K_ROUTER_REGENERATE,
    K_ROUTER_REGRESSION,
    actionable_issues,
    blocking_fingerprint,
    blocking_issues,
    lesson_hash,
    route_after_aggregate,
    route_after_fix,
    route_after_human,
    to_issue_dict,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ordinal_lesson() -> dict[str, Any]:
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def _ordinal_requirements() -> dict[str, Any]:
    return json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text())


def _ordinal_baseline() -> dict[str, Any]:
    return json.loads((ROOT / "dist/lessons/ordinal_numbers.json").read_text())


def _seeded_blocker_issue(check_id: str = "judge_feedback", unit_id: str = "ex1") -> dict[str, Any]:
    return {
        "check_id": check_id,
        "severity": "blocker",
        "unit_id": unit_id,
        "message": "judge false has empty feedback",
        "fix_hint": "add feedback",
        "advisory": False,
        "is_blocking": True,
    }


def _advisory_blocker_issue() -> dict[str, Any]:
    return {
        "check_id": "pedagogy_check",
        "severity": "blocker",
        "unit_id": "",
        "message": "P1 answerable",
        "fix_hint": "rewrite stem",
        "advisory": True,
        "is_blocking": False,
    }


def _revision_target_issue(check_id: str = "rubric_floor", unit_id: str = "depth") -> dict[str, Any]:
    return {
        "check_id": check_id,
        "severity": "blocker",
        "unit_id": unit_id,
        "message": "pedagogy axis below promoted floor",
        "fix_hint": "raise score",
        "advisory": False,
        "revision_target": True,
        "is_blocking": False,
    }


def _attempt(kind: str, *, same_hash: bool = False, fingerprint: str | None = None) -> dict[str, Any]:
    h = "sha256:x"
    return {
        "kind": kind,
        "reason": "blocking_issues" if kind == "fix" else "fix_budget_exhausted",
        "ts": "2026-06-19T00:00:00+00:00",
        "lesson_hash_before": h,
        "lesson_hash_after": h if same_hash else "sha256:y",
        "blocking_fingerprint": fingerprint,
    }


class _RecordingExporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> str:
        self.calls.append(kw)
        return f"dist/lessons/{kw['slug']}.json"


def _deps_for_clean_lesson(exporter: _RecordingExporter | None = None) -> GraphDeps:
    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson(),
            requirements=_ordinal_requirements(),
            baseline_export=_ordinal_baseline(),
            recorded_requirements_hash=None,
        )

    return GraphDeps(loader=loader, fixer=noop_fixer, judge=noop_judge, exporter=exporter or _RecordingExporter())


def _ordinal_lesson_with_structural_blocker() -> dict[str, Any]:
    """A real ordinal lesson with one exercise pointing at an unknown objective_id.

    Trips the deterministic ``objective_structural`` check (a load-bearing
    blocker), so the graph must route to the fixer to clear it.
    """
    lesson = _ordinal_lesson()
    for element in lesson["elements"]:
        if element.get("element_kind") == "exercise":
            element["objective_id"] = "does_not_exist_obj"
            break
    return lesson


class _CleanReturningFixer:
    """A fixer that 'repairs' by returning the known-clean lesson; records calls.

    Stands in for the real ``author_fixer`` (unit-tested in test_fixers.py) so the
    graph fix -> re-check -> converge -> export loop can be exercised hermetically.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, slug: str, lesson: dict[str, Any], issues: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        self.calls.append({"slug": slug, "kind": kind, "issue_count": len(issues)})
        return _ordinal_lesson()


def _deps_for_blocking_then_fixed(fixer: _CleanReturningFixer, exporter: _RecordingExporter) -> GraphDeps:
    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson_with_structural_blocker(),
            requirements=_ordinal_requirements(),
            baseline_export=_ordinal_baseline(),
            recorded_requirements_hash=None,
        )

    return GraphDeps(loader=loader, fixer=fixer, judge=noop_judge, exporter=exporter)


def _graph_input(run_id: str) -> dict[str, str]:
    return {
        "slug": "ordinal_numbers",
        "run_id": run_id,
        "repo_root": str(ROOT),
        "acceptance_log_path": str(ROOT / "data" / "lesson_acceptance_log.jsonl"),
    }


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
# Routing: route_after_aggregate
# ---------------------------------------------------------------------------


def test_route_after_aggregate_given_no_blocking_issues_expect_regression():
    state = {"current_issues": [], "attempts": []}
    assert route_after_aggregate(state) == K_ROUTER_REGRESSION


def test_route_after_aggregate_given_warning_only_expect_regression():
    state = {
        "current_issues": [{"check_id": "op_depth", "severity": "warning", "is_blocking": False}],
        "attempts": [],
    }
    assert route_after_aggregate(state) == K_ROUTER_REGRESSION


def test_route_after_aggregate_given_blocking_under_fix_cap_expect_fix():
    state = {"current_issues": [_seeded_blocker_issue()], "attempts": []}
    assert route_after_aggregate(state) == K_ROUTER_FIX


def test_route_after_aggregate_given_blocking_fixes_exhausted_expect_regenerate():
    attempts = [_attempt("fix", same_hash=False) for _ in range(K_GRAPH_MAX_FIXES)]
    state = {"current_issues": [_seeded_blocker_issue()], "attempts": attempts}
    assert route_after_aggregate(state) == K_ROUTER_REGENERATE


def test_route_after_aggregate_given_blocking_all_exhausted_expect_human():
    attempts = [_attempt("fix") for _ in range(K_GRAPH_MAX_FIXES)]
    attempts += [_attempt("regenerate") for _ in range(K_GRAPH_MAX_REGENERATES)]
    state = {"current_issues": [_seeded_blocker_issue()], "attempts": attempts}
    assert route_after_aggregate(state) == K_ROUTER_HUMAN


def test_route_after_aggregate_given_advisory_blocker_only_expect_regression():
    state = {"current_issues": [_advisory_blocker_issue()], "attempts": []}
    assert route_after_aggregate(state) == K_ROUTER_REGRESSION


def test_route_after_aggregate_given_revision_target_issue_expect_fix():
    state = {"current_issues": [_revision_target_issue()], "attempts": []}
    assert route_after_aggregate(state) == K_ROUTER_FIX


# ---------------------------------------------------------------------------
# Routing: route_after_fix (convergence guard)
# ---------------------------------------------------------------------------


def test_route_after_fix_given_no_attempts_expect_human():
    assert route_after_fix({"attempts": []}) == K_ROUTER_HUMAN


def test_route_after_fix_given_unchanged_lesson_hash_expect_human():
    state = {"attempts": [_attempt("fix", same_hash=True)]}
    assert route_after_fix(state) == K_ROUTER_HUMAN


def test_route_after_fix_given_changed_lesson_expect_recheck():
    state = {"attempts": [_attempt("fix", same_hash=False)]}
    assert route_after_fix(state) == "checks"


def test_route_after_fix_given_repeated_fingerprint_expect_human():
    fp = "sha256:abc"
    attempts = [_attempt("fix", same_hash=False, fingerprint=fp), _attempt("fix", same_hash=False, fingerprint=fp)]
    assert route_after_fix({"attempts": attempts}) == K_ROUTER_HUMAN


def test_route_after_fix_given_distinct_fingerprints_expect_recheck():
    attempts = [
        _attempt("fix", same_hash=False, fingerprint="sha256:a"),
        _attempt("fix", same_hash=False, fingerprint="sha256:b"),
    ]
    assert route_after_fix({"attempts": attempts}) == "checks"


# ---------------------------------------------------------------------------
# Routing: route_after_human (resume)
# ---------------------------------------------------------------------------


def test_route_after_human_given_accept_decision_expect_export():
    state = {"human_decision": {"status": "accept"}}
    assert route_after_human(state) == K_ROUTER_EXPORT


def test_route_after_human_given_revision_target_only_and_accept_expect_export():
    state = {
        "human_decision": {"status": "accept"},
        "current_issues": [_revision_target_issue()],
    }
    assert route_after_human(state) == K_ROUTER_EXPORT


def test_route_after_human_given_edit_decision_expect_recheck():
    state = {"human_decision": {"status": "edit", "lesson": {}}}
    assert route_after_human(state) == "checks"


def test_route_after_human_given_defer_decision_expect_end():
    state = {"human_decision": {"status": "defer"}}
    assert route_after_human(state) == END_LITERAL


def test_route_after_human_given_respond_decision_expect_end():
    state = {"human_decision": {"status": "respond", "message": "needs a note"}}
    assert route_after_human(state) == END_LITERAL


def test_route_after_human_given_no_decision_expect_end():
    assert route_after_human({}) == END_LITERAL


def test_route_after_human_given_deterministic_blocker_and_accept_expect_repark():
    state = {
        "human_decision": {"status": "accept"},
        "current_issues": [_seeded_blocker_issue()],
    }
    assert route_after_human(state) == K_ROUTER_HUMAN


# ---------------------------------------------------------------------------
# State behavior: is_blocking serialization + advisory split
# ---------------------------------------------------------------------------


def test_to_issue_dict_given_blocking_check_result_expect_is_blocking_true():
    result = CheckResult(check_id="x", severity="blocker", message="m")
    issue = to_issue_dict(result)
    assert issue["is_blocking"] is True
    assert issue["advisory"] is False


def test_to_issue_dict_given_advisory_blocker_expect_is_blocking_false():
    result = CheckResult(check_id="x", severity="blocker", advisory=True, message="m")
    issue = to_issue_dict(result)
    assert issue["is_blocking"] is False
    assert issue["advisory"] is True


def test_to_issue_dict_given_revision_target_expect_revision_target_true_and_is_blocking_false():
    result = CheckResult(check_id="x", severity="blocker", revision_target=True, message="m")
    issue = to_issue_dict(result)
    assert issue["revision_target"] is True
    assert issue["is_blocking"] is False


def test_blocking_issues_given_mixed_list_expect_only_load_bearing_blockers():
    issues = [_seeded_blocker_issue(), _advisory_blocker_issue(), _revision_target_issue()]
    blockers = blocking_issues(issues)
    assert len(blockers) == 1
    assert blockers[0]["check_id"] == "judge_feedback"


def test_actionable_issues_given_mixed_list_expect_blockers_and_revision_targets():
    issues = [_seeded_blocker_issue(), _advisory_blocker_issue(), _revision_target_issue()]
    actionable = actionable_issues(issues)
    assert [issue["check_id"] for issue in actionable] == ["judge_feedback", "rubric_floor"]


def test_blocking_fingerprint_given_same_blockers_expect_stable_hash():
    issues_a = [_seeded_blocker_issue(check_id="a", unit_id="u1")]
    issues_b = [_seeded_blocker_issue(check_id="a", unit_id="u1")]
    assert blocking_fingerprint(issues_a) == blocking_fingerprint(issues_b)


def test_blocking_fingerprint_given_advisory_only_expect_empty_key_hash():
    # advisory blockers must not contribute to the fingerprint (would let an
    # uncalibrated judge make the convergence guard escalate).
    fp_no_issues = blocking_fingerprint([])
    fp_advisory_only = blocking_fingerprint([_advisory_blocker_issue()])
    assert fp_no_issues == fp_advisory_only


def test_blocking_fingerprint_given_revision_target_only_expect_non_empty_key_hash():
    fp_no_issues = blocking_fingerprint([])
    fp_revision_target = blocking_fingerprint([_revision_target_issue()])
    assert fp_revision_target != fp_no_issues


def test_lesson_hash_given_nested_dict_key_order_changes_expect_same_hash():
    lesson_a = {"payload": {"b": 2, "a": 1}, "elements": [{"z": 1, "y": 2}]}
    lesson_b = {"elements": [{"y": 2, "z": 1}], "payload": {"a": 1, "b": 2}}
    assert lesson_hash(lesson_a) == lesson_hash(lesson_b)


# ---------------------------------------------------------------------------
# run_checks_node: current_issues overwrite + issue_history append + advisory fold
# ---------------------------------------------------------------------------


def test_run_checks_node_given_clean_lesson_expect_no_blocking_and_history_populated():
    deps = _deps_for_clean_lesson()
    state = {
        "slug": "ordinal_numbers",
        "lesson": _ordinal_lesson(),
        "requirements": _ordinal_requirements(),
        "baseline": _ordinal_baseline(),
        "issue_history": [],
    }
    update = run_checks_node(state, deps)
    assert update["current_issues"] is not None
    assert not any(issue["is_blocking"] for issue in update["current_issues"])
    # issue_history mirrors current_issues on the first pass (audit append)
    assert update["issue_history"] == update["current_issues"]


def test_run_checks_node_given_advisory_review_payloads_expect_folded_but_not_blocking():
    pedagogy_payload = {
        "scores": dict.fromkeys(
            ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"), 5
        ),
        "summary": "ok",
        "passes": [],
        "issues": [
            {"severity": "P1", "category": "answerable", "title": "x", "evidence": "e", "fix": "f"},
        ],
    }
    deps = _deps_for_clean_lesson()
    state = {
        "slug": "ordinal_numbers",
        "lesson": _ordinal_lesson(),
        "requirements": _ordinal_requirements(),
        "pedagogy_review": pedagogy_payload,
        "issue_history": [],
    }
    update = run_checks_node(state, deps)
    pedagogy_issues = [i for i in update["current_issues"] if i["check_id"] == "pedagogy_check"]
    assert pedagogy_issues
    assert all(i["advisory"] for i in pedagogy_issues)
    # advisory P1 blocker must not be load-bearing
    assert all(not i["is_blocking"] for i in pedagogy_issues)
    assert all(i["severity"] == "blocker" for i in pedagogy_issues)


def test_run_checks_node_given_promoted_rubric_floor_below_score_expect_revision_target(tmp_path: Path):
    # promoted rubric floors (floor 5 on every axis); lesson scores 4
    axes = ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth")
    floors_path = tmp_path / "data" / "calibration" / "rubric_floors.json"
    floors_path.parent.mkdir(parents=True, exist_ok=True)
    floors_path.write_text(
        json.dumps({"axis_floor": dict.fromkeys(axes, 5), "load_bearing": True}),
        encoding="utf-8",
    )
    deps = _deps_for_clean_lesson()
    state = {
        "slug": "ordinal_numbers",
        "repo_root": str(tmp_path),
        "lesson": _ordinal_lesson(),
        "requirements": _ordinal_requirements(),
        "pedagogy_review": {
            "scores": dict.fromkeys(axes, 4),
            "summary": "needs work",
            "passes": [],
            "issues": [
                {"severity": "P1", "category": "answerable", "title": "x", "evidence": "e", "fix": "f"},
            ],
        },
        "issue_history": [],
    }

    update = run_checks_node(state, deps)

    rubric_issues = [i for i in update["current_issues"] if i["check_id"] == "rubric_floor"]
    assert rubric_issues  # scores below floor produced findings
    assert all(i["advisory"] is False for i in rubric_issues)
    assert all(i["revision_target"] is True for i in rubric_issues)
    assert all(i["is_blocking"] is False for i in rubric_issues)
    # the issue-list pedagogy finding remains advisory (never promoted)
    pedagogy_issue = next(i for i in update["current_issues"] if i["check_id"] == "pedagogy_check")
    assert pedagogy_issue["advisory"] is True


# ---------------------------------------------------------------------------
# regression_node: honest shape (blocking_passed vs has_warnings/severity)
# ---------------------------------------------------------------------------


def test_regression_node_given_no_baseline_expect_blocking_passed_no_warnings():
    deps = _deps_for_clean_lesson()
    state = {"slug": "ordinal_numbers", "lesson": _ordinal_lesson(), "baseline": None}
    update = regression_node(state, deps)
    result = update["regression_result"]
    assert result["blocking_passed"] is True
    assert result["has_warnings"] is False
    assert result["severity"] is None
    assert result["diff"] is None


def test_regression_node_given_matching_baseline_expect_blocking_passed_no_warnings():
    deps = _deps_for_clean_lesson()
    lesson = _ordinal_lesson()
    state = {"slug": "ordinal_numbers", "lesson": lesson, "baseline": _ordinal_baseline()}
    update = regression_node(state, deps)
    result = update["regression_result"]
    assert result["blocking_passed"] is True
    assert result["has_warnings"] is False
    assert result["diff"] is None


def test_regression_node_given_diverging_baseline_expect_warnings_not_misread_as_clean():
    deps = _deps_for_clean_lesson()
    lesson = deepcopy(_ordinal_lesson())
    lesson["title"] = "A drifted title that differs from the baseline export"
    baseline = _ordinal_baseline()
    state = {"slug": "ordinal_numbers", "lesson": lesson, "baseline": baseline}
    update = regression_node(state, deps)
    result = update["regression_result"]
    # non-blocking by design: blocking_passed stays True even with a real diff
    assert result["blocking_passed"] is True
    assert result["has_warnings"] is True
    assert result["severity"] == "warning"
    assert result["diff"]


# ---------------------------------------------------------------------------
# Resume / checkpoint: clean lesson parks at human_gate, accept resumes to export
# ---------------------------------------------------------------------------


def test_graph_given_clean_lesson_expect_parks_at_human_gate():
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run1"}}

    graph.invoke(_graph_input("run1"), config=config)
    state = graph.get_state(config)

    # the graph suspended inside human_gate (interrupt)
    assert state.next == ("human_gate",)
    # upstream state was computed and is visible on the parked thread
    values = state.values
    assert values["park_status"] == "parked"
    assert values["lesson"]["key"] == "ordinal_numbers"
    assert "current_issues" in values
    assert "issue_history" in values
    assert "regression_result" in values
    # exporter not called yet (parked, not accepted)
    assert exporter.calls == []


def test_graph_given_accept_resume_expect_exports_and_accepts():
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run2"}}

    graph.invoke(_graph_input("run2"), config=config)
    assert exporter.calls == []

    graph.invoke(Command(resume={"status": "accept"}), config=config)
    state = graph.get_state(config)

    assert state.next == ()
    assert state.values["park_status"] == "accepted"
    assert state.values["ledger_status"] == "accepted"
    assert len(exporter.calls) == 1
    assert exporter.calls[0]["slug"] == "ordinal_numbers"


def test_graph_given_blocking_lesson_fix_clears_blocker_and_converges_to_export():
    # End-to-end fix loop (the gap the review flagged): a real deterministic
    # blocker -> the fixer is invoked -> re-check clears it -> converge -> park
    # clean -> accept -> export.
    fixer = _CleanReturningFixer()
    exporter = _RecordingExporter()
    deps = _deps_for_blocking_then_fixed(fixer, exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:fixrun"}}

    graph.invoke(_graph_input("fixrun"), config=config)
    parked = graph.get_state(config)

    # the fixer was invoked to repair the structural blocker
    assert len(fixer.calls) >= 1
    assert fixer.calls[0]["kind"] == "fix"
    # after the fix re-converged, the parked thread carries no load-bearing blockers
    assert parked.next == ("human_gate",)
    assert [issue for issue in parked.values["current_issues"] if issue.get("is_blocking")] == []
    # an attempt was recorded with a changing lesson hash (not a stalled loop)
    attempts = parked.values.get("attempts", [])
    assert attempts and attempts[0]["lesson_hash_before"] != attempts[0]["lesson_hash_after"]
    # exporter not called while parked
    assert exporter.calls == []

    # accept -> export
    graph.invoke(Command(resume={"status": "accept"}), config=config)
    state = graph.get_state(config)
    assert state.values["park_status"] == "accepted"
    assert len(exporter.calls) == 1
    assert exporter.calls[0]["slug"] == "ordinal_numbers"


def test_graph_given_resume_reuses_upstream_state_after_park():
    # The resume-from-reviewer invariant (design section 4): the parked thread
    # already ran load + checks + regression + signoff; resuming must NOT re-run
    # them. We assert by checking the issue_history length is stable across the
    # resume boundary (no second checks pass got appended on the accept path).
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run3"}}

    graph.invoke(_graph_input("run3"), config=config)
    parked_history_len = len(graph.get_state(config).values.get("issue_history", []))

    graph.invoke(Command(resume={"status": "accept"}), config=config)
    accepted_history_len = len(graph.get_state(config).values.get("issue_history", []))

    assert parked_history_len == accepted_history_len
    assert parked_history_len > 0


def test_graph_given_defer_resume_expect_ends_parked_without_export():
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run4"}}

    graph.invoke(_graph_input("run4"), config=config)
    graph.invoke(Command(resume={"status": "defer"}), config=config)
    state = graph.get_state(config)

    assert state.next == ()
    assert state.values["park_status"] == "deferred"
    assert exporter.calls == []


def test_graph_given_respond_resume_expect_deferred_without_export():
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run5"}}

    graph.invoke(_graph_input("run5"), config=config)
    graph.invoke(Command(resume={"status": "respond", "message": "leave a note"}), config=config)
    state = graph.get_state(config)

    assert state.next == ()
    assert state.values["park_status"] == "deferred"
    assert state.values["human_decision"]["status"] == "respond"
    assert exporter.calls == []


def test_graph_given_edit_resume_expect_rechecks_and_exports_edited_lesson():
    exporter = _RecordingExporter()
    deps = _deps_for_clean_lesson(exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run6"}}
    edited_lesson = deepcopy(_ordinal_lesson())
    edited_lesson["title"] = "Edited Ordinal Lesson"

    graph.invoke(_graph_input("run6"), config=config)
    graph.invoke(Command(resume={"status": "edit", "lesson": edited_lesson}), config=config)

    state_after_edit = graph.get_state(config)
    assert state_after_edit.next == ("human_gate",)
    assert state_after_edit.values["park_status"] == "parked"
    assert state_after_edit.values["lesson"]["title"] == "Edited Ordinal Lesson"

    graph.invoke(Command(resume={"status": "accept"}), config=config)
    accepted_state = graph.get_state(config)

    assert accepted_state.values["park_status"] == "accepted"
    assert len(exporter.calls) == 1
    assert exporter.calls[0]["lesson"]["title"] == "Edited Ordinal Lesson"


def test_graph_given_default_loader_and_exporter_expect_state_paths_drive_accept_export(tmp_path: Path):
    repo_root, acceptance_log_path = _write_tmp_repo_with_ordinal_lesson(tmp_path)
    deps = GraphDeps(loader=default_loader, fixer=noop_fixer, judge=noop_judge)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:run7"}}

    graph.invoke(
        {
            "slug": "ordinal_numbers",
            "run_id": "run7",
            "repo_root": str(repo_root),
            "acceptance_log_path": str(acceptance_log_path),
        },
        config=config,
    )
    graph.invoke(Command(resume={"status": "accept"}), config=config)
    accepted_state = graph.get_state(config)

    internal_path = repo_root / "data" / "lessons" / "ordinal_numbers.json"
    export_path = repo_root / "dist" / "lessons" / "ordinal_numbers.json"
    log_lines = acceptance_log_path.read_text(encoding="utf-8").splitlines()

    assert accepted_state.values["park_status"] == "accepted"
    assert accepted_state.values["export_path"] == "dist/lessons/ordinal_numbers.json"
    assert internal_path.exists()
    assert export_path.exists()
    assert len(log_lines) == 1


# ---------------------------------------------------------------------------
# route_after_human + export: refuse accept with remaining blockers unless
# the decision sets override; override exports as accepted_override (and is
# excluded from regression-baseline eligibility, see test_lesson_acceptance_log.py)
# ---------------------------------------------------------------------------


def test_graph_given_accept_with_remaining_blockers_no_override_expect_reparks_without_export():
    seeded = deepcopy(_ordinal_lesson())
    bad_ex = {
        "element_kind": "exercise",
        "id": "bad_judge_ex",
        "operation": "judge",
        "objective_id": seeded["objectives"][0]["id"],
        "bloom_level": "understand",
        "derived_from": [],
        "prompt": [{"kind": "text", "value": "Mark the sentence."}],
        "payload": {
            "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
            "is_correct": False,
            "feedback": "",
        },
    }
    seeded["elements"].append(bad_ex)

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=seeded, requirements=_ordinal_requirements())

    exporter = _RecordingExporter()
    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=noop_judge, exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:reparks"}}

    # convergence guard escalates to human with the blocker still present
    graph.invoke(_graph_input("reparks"), config=config)
    assert graph.get_state(config).next == ("human_gate",)

    # human accepts without override -> refused, re-parked at human_gate again
    graph.invoke(Command(resume={"status": "accept"}), config=config)
    state = graph.get_state(config)

    assert state.next == ("human_gate",)
    assert state.values["park_status"] == "parked"
    assert exporter.calls == []


def test_graph_given_accept_with_override_expect_exports_as_accepted_override():
    seeded = deepcopy(_ordinal_lesson())
    bad_ex = {
        "element_kind": "exercise",
        "id": "bad_judge_ex",
        "operation": "judge",
        "objective_id": seeded["objectives"][0]["id"],
        "bloom_level": "understand",
        "derived_from": [],
        "prompt": [{"kind": "text", "value": "Mark the sentence."}],
        "payload": {
            "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
            "is_correct": False,
            "feedback": "",
        },
    }
    seeded["elements"].append(bad_ex)

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=seeded, requirements=_ordinal_requirements())

    exporter = _RecordingExporter()
    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=noop_judge, exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:override"}}

    graph.invoke(_graph_input("override"), config=config)
    assert graph.get_state(config).next == ("human_gate",)

    graph.invoke(Command(resume={"status": "accept", "override": True}), config=config)
    state = graph.get_state(config)

    assert state.next == ()
    assert state.values["park_status"] == "accepted"
    assert state.values["ledger_status"] == "accepted_override"
    assert len(exporter.calls) == 1
    assert exporter.calls[0]["override"] is True


# ---------------------------------------------------------------------------
# Fix-loop convergence: noop fixer -> convergence guard escalates to human
# ---------------------------------------------------------------------------


def test_graph_given_blocking_lesson_and_noop_fixer_expect_convergence_to_human():
    seeded = deepcopy(_ordinal_lesson())
    bad_ex = {
        "element_kind": "exercise",
        "id": "bad_judge_ex",
        "operation": "judge",
        "objective_id": seeded["objectives"][0]["id"],
        "bloom_level": "understand",
        "derived_from": [],
        "prompt": [{"kind": "text", "value": "Mark the sentence."}],
        "payload": {
            "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
            "is_correct": False,
            "feedback": "",
        },
    }
    seeded["elements"].append(bad_ex)

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=seeded, requirements=_ordinal_requirements())

    exporter = _RecordingExporter()
    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=noop_judge, exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:conv"}}

    graph.invoke(_graph_input("conv"), config=config)
    state = graph.get_state(config)

    # Convergence guard: one noop fix (same hash) -> escalate to human.
    assert state.next == ("human_gate",)
    assert state.values["park_status"] == "parked"
    attempts = state.values.get("attempts", [])
    assert any(a["kind"] == "fix" for a in attempts)
    assert exporter.calls == []
