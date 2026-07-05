"""Pipeline hardening (spec 2026-06-21): Tasks A-F.

Entry point: the Lesson-QA back-half graph + its collaborators.

Covers the acceptance-required tests:
- Task A: fix-loop converges when a fake rejudge re-derives improving pedagogy
  scores post-fix (instead of re-folding the stale pre-fix review).
- Task C: an LLM reviewer outage is surfaced first-class via ``llm_status`` in
  the human-gate payload, and a load-bearing + judge-failed run does NOT read
  as "clean".
- Task F: ``noop`` is not a selectable CLI choice but stays importable for
  direct test injection.

Also covers Task B (judge skipped on hard blockers), Task D (parallel judge
return shape), and Task E (loader typed error + router robustness).
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from lesson_builder.pipeline.cli import _add_run_parser
from lesson_builder.pipeline.graph_runner import K_FIXERS, K_JUDGES, run_graph
from lesson_builder.pipeline.judges import (
    K_LLM_STATUS_BACKEND_DOWN,
    K_SURFACE_PEDAGOGY,
    default_judge,
    noop_judge,
)
from lesson_builder.pipeline.lesson_qa_graph import (
    GraphDeps,
    LessonLoadError,
    LoadedLesson,
    build_lesson_qa_graph,
    default_loader,
    judge_node,
    noop_fixer,
)
from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.pipeline.state import route_after_fix

ROOT = Path(__file__).resolve().parents[2]
K_AXES = ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ordinal_lesson() -> dict[str, Any]:
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text(encoding="utf-8"))


def _ordinal_requirements() -> dict[str, Any]:
    return json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text(encoding="utf-8"))


def _ordinal_baseline() -> dict[str, Any]:
    return json.loads((ROOT / "dist/lessons/ordinal_numbers.json").read_text(encoding="utf-8"))


def _write_load_bearing_floors(repo_root: Path, *, floor: int = 5) -> Path:
    """Write a rubric_floors.json with load_bearing=True under repo_root."""
    floors_path = repo_root / "data" / "calibration" / "rubric_floors.json"
    floors_path.parent.mkdir(parents=True, exist_ok=True)
    floors_path.write_text(
        json.dumps(
            {"axis_floor": dict.fromkeys(K_AXES, floor), "load_bearing": True}
        ),
        encoding="utf-8",
    )
    return floors_path


def _pedagogy_review(score: int) -> dict[str, Any]:
    return {
        "scores": dict.fromkeys(K_AXES, score),
        "summary": f"score {score}",
        "passes": [],
        "issues": [],
    }


def _graph_input(run_id: str, repo_root: Path) -> dict[str, str]:
    return {
        "slug": "ordinal_numbers",
        "run_id": run_id,
        "repo_root": str(repo_root),
        "acceptance_log_path": str(repo_root / "data" / "lesson_acceptance_log.jsonl"),
    }


class _RecordingExporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> str:
        self.calls.append(kw)
        return f"dist/lessons/{kw['slug']}.json"


# ---------------------------------------------------------------------------
# Task A: re-derive pedagogy post-fix so the loop converges
# ---------------------------------------------------------------------------


class _ImprovingRejudge:
    """Returns a pedagogy review at-or-above floor so the post-fix pass clears the floor."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any] | None:
        self.calls += 1
        return _pedagogy_review(5)


class _CleanReturningFixer:
    """Fixer that returns a slightly-modified clean lesson so the lesson hash
    changes (the convergence guard requires hash-before != hash-after). The
    rejudge then re-derives at-floor pedagogy scores and the rubric findings
    clear."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, slug: str, lesson: dict[str, Any], issues: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        self.calls += 1
        repaired = deepcopy(_ordinal_lesson())
        # A trivial content tweak so lesson_hash_after != lesson_hash_before.
        repaired["title"] = f"{repaired.get('title', 'Lesson')} (revised pass {self.calls})"
        return repaired


def test_task_a_given_load_bearing_floors_and_improving_rejudge_expect_loop_converges(
    tmp_path: Path,
):
    # The convergence bug: judge_node runs pedagogy once (below floor); without
    # re-derivation the fix loop keeps re-folding the STALE pre-fix review and
    # the same axes stay flagged -> the loop burns budget and escalates. With
    # the rejudge seam wired, the post-fix pass sees the improved scores and the
    # thread parks clean.
    repo_root = tmp_path
    _write_load_bearing_floors(repo_root, floor=5)

    weak_pedagogy = _pedagogy_review(3)  # below floor 5 -> revision target
    judge_calls: list[int] = [0]

    def judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        judge_calls[0] += 1
        # judge_node runs once on entry: return the WEAK pre-fix review.
        return {
            "pedagogy_review": weak_pedagogy,
            "objective_alignment_review": None,
            "answer_review": None,
            "naturalness_review": None,
        }

    rejudge = _ImprovingRejudge()
    fixer = _CleanReturningFixer()
    exporter = _RecordingExporter()

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson(),
            requirements=_ordinal_requirements(),
            baseline_export=_ordinal_baseline(),
        )

    deps = GraphDeps(
        loader=loader,
        fixer=fixer,
        judge=judge,
        rejudge_pedagogy=rejudge,
        exporter=exporter,
    )
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskA"}}

    graph.invoke(_graph_input("taskA", repo_root), config=config)
    state = graph.get_state(config)

    # The rejudge was invoked at least once (post-fix), producing an at-floor
    # review so the rubric-floor check stopped flagging the lesson.
    assert rejudge.calls >= 1
    # The thread parked at the human gate WITHOUT load-bearing blockers -- the
    # loop converged instead of escalating with stale rubric findings.
    assert state.next == ("human_gate",)
    rubric_or_unavailable = [
        i for i in state.values["current_issues"]
        if i.get("check_id") in {"rubric_floor", "pedagogy_unavailable"}
        and (i.get("revision_target") or i.get("is_blocking"))
    ]
    assert rubric_or_unavailable == [], (
        "expected the post-fix rejudge to clear the load-bearing rubric findings"
    )


def test_task_a_given_no_rejudge_seam_expect_no_re_derivation(tmp_path: Path):
    # Cost guard: when rejudge_pedagogy is None (the library/test default), the
    # fix loop does NOT attempt any re-derivation -- the seam is opt-in.
    repo_root = tmp_path
    _write_load_bearing_floors(repo_root, floor=5)

    fixer = _CleanReturningFixer()
    exporter = _RecordingExporter()

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=_ordinal_lesson(), requirements=_ordinal_requirements())

    deps = GraphDeps(loader=loader, fixer=fixer, judge=noop_judge, exporter=exporter)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskA_norejudge"}}

    graph.invoke(_graph_input("taskA_norejudge", repo_root), config=config)
    state = graph.get_state(config)

    # Floors are load-bearing but the noop judge produced no review and no
    # llm_status, so pedagogy_unavailable does not fire (the judge did not FAIL,
    # it intentionally produced nothing). The thread parks at the gate.
    assert state.next == ("human_gate",)


# ---------------------------------------------------------------------------
# Task B: skip the reviewer panel on a deterministically-broken lesson
# ---------------------------------------------------------------------------


def _ordinal_lesson_with_structural_blocker() -> dict[str, Any]:
    """Append a blocking judge exercise so the deterministic gate flags it."""
    lesson = _ordinal_lesson()
    bad_ex = {
        "element_kind": "exercise",
        "id": "bad_judge_ex",
        "operation": "judge",
        "objective_id": lesson["objectives"][0]["id"],
        "bloom_level": "understand",
        "derived_from": [],
        "prompt": [{"kind": "text", "value": "Mark the sentence."}],
        "explanation": None,
        "payload": {
            "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
            "is_correct": False,
            "feedback": "",
        },
    }
    lesson["elements"].append(bad_ex)
    return lesson


def test_task_b_given_deterministic_hard_blockers_expect_judge_panel_skipped(tmp_path: Path):
    # A lesson with a deterministic hard blocker must NOT burn the reviewer
    # panel on entry: judge_node runs the deterministic gate first and returns
    # all-None reviews when a hard blocker is present.
    judge_calls: list[int] = [0]

    def judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        judge_calls[0] += 1
        raise AssertionError("judge panel must not run when hard blockers are present")

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson_with_structural_blocker(),
            requirements=_ordinal_requirements(),
        )

    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=judge, exporter=_RecordingExporter())
    state: dict[str, Any] = {
        "slug": "ordinal_numbers",
        "repo_root": str(tmp_path),
        "lesson": _ordinal_lesson_with_structural_blocker(),
        "requirements": _ordinal_requirements(),
    }
    update = judge_node(state, deps)

    assert judge_calls[0] == 0
    assert update["pedagogy_review"] is None
    assert update["objective_alignment_review"] is None
    assert update["answer_review"] is None
    assert update["naturalness_review"] is None


def test_task_b_given_clean_lesson_expect_judge_panel_runs(tmp_path: Path):
    # A clean lesson (no deterministic blockers) runs the judge panel normally.
    judge_calls: list[int] = [0]

    def judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        judge_calls[0] += 1
        return {"pedagogy_review": _pedagogy_review(5), "objective_alignment_review": None, "answer_review": None, "naturalness_review": None}

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=_ordinal_lesson(), requirements=_ordinal_requirements())

    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=judge, exporter=_RecordingExporter())
    state: dict[str, Any] = {
        "slug": "ordinal_numbers",
        "repo_root": str(tmp_path),
        "lesson": _ordinal_lesson(),
        "requirements": _ordinal_requirements(),
    }
    update = judge_node(state, deps)

    assert judge_calls[0] == 1
    assert update["pedagogy_review"] is not None


# ---------------------------------------------------------------------------
# Task C: LLM failure first-class (llm_status in payload; fixer failure_kind)
# ---------------------------------------------------------------------------


def test_task_c_given_judge_records_failure_expect_llm_status_in_interrupt_payload(tmp_path: Path):
    # The judge reports a pedagogy-surface failure via _llm_status; the graph
    # stores it in state.llm_status and surfaces it in the human-gate payload.
    repo_root = tmp_path
    _write_load_bearing_floors(repo_root, floor=5)

    def judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        return {
            "pedagogy_review": None,
            "objective_alignment_review": None,
            "answer_review": None,
            "naturalness_review": None,
            "_llm_status": {K_SURFACE_PEDAGOGY: K_LLM_STATUS_BACKEND_DOWN},
        }

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=_ordinal_lesson(), requirements=_ordinal_requirements())

    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=judge, exporter=_RecordingExporter())
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskC"}}

    graph.invoke(_graph_input("taskC", repo_root), config=config)
    state = graph.get_state(config)

    # The pedagogy outage surfaced first-class in state + payload.
    assert state.values.get("llm_status", {}).get(K_SURFACE_PEDAGOGY) == K_LLM_STATUS_BACKEND_DOWN

    # And it produced a revision-target issue so the lesson does NOT silently
    # read as "clean" (load-bearing + judge-failed != clean pass).
    unavailable = [
        i for i in state.values["current_issues"] if i.get("check_id") == "pedagogy_unavailable"
    ]
    assert unavailable, "expected a pedagogy_unavailable revision target when the judge failed"
    assert unavailable[0]["revision_target"] is True
    assert unavailable[0]["is_blocking"] is False


def test_task_c_given_fixer_llm_failure_expect_failure_kind_recorded_on_attempt(tmp_path: Path):
    # The fixer raises on LLM failure (first-class). fix_node catches it, records
    # the failure kind on the AttemptRecord, and no-ops the lesson so the
    # convergence guard escalates.
    repo_root = tmp_path

    class _FailingFixer:
        def __call__(self, *, slug, lesson, issues, kind):
            raise BackendDownException("author backend down")

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson_with_structural_blocker(),
            requirements=_ordinal_requirements(),
        )

    deps = GraphDeps(loader=loader, fixer=_FailingFixer(), judge=noop_judge, exporter=_RecordingExporter())
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskC_fixer"}}

    graph.invoke(_graph_input("taskC_fixer", repo_root), config=config)
    state = graph.get_state(config)

    attempts = state.values.get("attempts", [])
    fix_attempts = [a for a in attempts if a.get("kind") == "fix"]
    assert fix_attempts, "expected at least one fix attempt"
    # The failure kind distinguishes a model FAILURE from a model no-op.
    assert fix_attempts[0]["failure_kind"] == "llm_backend_down"


def test_task_c_given_fixer_noop_expect_no_change_failure_kind(tmp_path: Path):
    # When the fixer returns the lesson unchanged (genuine no-op, no exception),
    # the AttemptRecord records "no_change" -- distinguishable from an LLM failure.
    repo_root = tmp_path

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(
            lesson=_ordinal_lesson_with_structural_blocker(),
            requirements=_ordinal_requirements(),
        )

    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=noop_judge, exporter=_RecordingExporter())
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskC_noop"}}

    graph.invoke(_graph_input("taskC_noop", repo_root), config=config)
    state = graph.get_state(config)

    fix_attempts = [a for a in state.values.get("attempts", []) if a.get("kind") == "fix"]
    assert fix_attempts
    assert fix_attempts[0]["failure_kind"] == "no_change"


# ---------------------------------------------------------------------------
# Task D: parallel judge panel preserves return shape + None-on-failure
# ---------------------------------------------------------------------------


class _SchemaAwareAgent:
    """Returns a payload appropriate to whichever schema is requested."""

    def __init__(self, payload_by_schema_name: dict[str, dict]):
        self._payload_by_schema_name = payload_by_schema_name
        self._schema = None

    def structured(self, schema):
        self._schema = schema
        return self

    def invoke(self, prompt, **kw):
        payload = self._payload_by_schema_name[self._schema.__name__]
        return self._schema.model_validate(payload)


def test_task_d_given_default_judge_expect_four_surfaces_returned_concurrently():
    # The parallel panel must still return the same four state keys with the
    # same payloads as the sequential version.
    good_ped = _pedagogy_review(4)
    agent = _SchemaAwareAgent(
        {
            "PedagogyReview": good_ped,
            "ObjectiveAlignmentReview": {"passed": True, "summary": "ok", "issues": []},
            "AnswerReview": {"answers": [{"id": "e1", "answer": True}]},
            "NaturalnessReview": {
                "scores": {
                    "idiomatic_phrasing": 4,
                    "register_appropriateness": 5,
                    "terminology_consistency": 4,
                },
                "issues": [],
            },
        }
    )
    lesson = {"concept_slug": "x", "objectives": [{"id": "o1"}], "elements": []}

    out = default_judge(lesson, agent=agent)

    assert set(out) == {
        "pedagogy_review",
        "objective_alignment_review",
        "answer_review",
        "naturalness_review",
    }
    assert out["pedagogy_review"] is not None
    assert out["objective_alignment_review"] is not None
    assert out["answer_review"] is not None
    assert out["naturalness_review"] is not None


def test_task_d_given_one_surface_fails_expect_none_for_that_surface_only():
    # Per-surface None-on-failure is preserved: one producer raising does not
    # poison the others, and the failure is recorded in _llm_status.
    class _SelectiveAgent:
        def __init__(self) -> None:
            self._schema = None

        def structured(self, schema):
            self._schema = schema
            return self

        def invoke(self, prompt, **kw):
            if self._schema.__name__ == "PedagogyReview":
                raise BackendDownException("pedagogy backend down")
            if self._schema.__name__ == "ObjectiveAlignmentReview":
                return self._schema.model_validate({"passed": True, "summary": "ok", "issues": []})
            if self._schema.__name__ == "AnswerReview":
                return self._schema.model_validate({"answers": [{"id": "e1", "answer": True}]})
            return self._schema.model_validate(
                {
                    "scores": {
                        "idiomatic_phrasing": 4,
                        "register_appropriateness": 5,
                        "terminology_consistency": 4,
                    },
                    "issues": [],
                }
            )

    out = default_judge({"objectives": [], "elements": []}, agent=_SelectiveAgent())

    assert out["pedagogy_review"] is None
    assert out["objective_alignment_review"] is not None
    assert out["answer_review"] is not None
    assert out["naturalness_review"] is not None
    assert out["_llm_status"]["pedagogy"] == K_LLM_STATUS_BACKEND_DOWN


# ---------------------------------------------------------------------------
# Task E: loader typed error + router robustness
# ---------------------------------------------------------------------------


def test_task_e_given_missing_lesson_file_expect_lesson_load_error(tmp_path: Path):
    with pytest.raises(LessonLoadError):
        default_loader("no_such_slug", repo_root=tmp_path)


def test_task_e_given_malformed_lesson_file_expect_lesson_load_error(tmp_path: Path):
    lesson_path = tmp_path / "data" / "lessons" / "broken.json"
    lesson_path.parent.mkdir(parents=True, exist_ok=True)
    lesson_path.write_text("{ not valid json", encoding="utf-8")

    with pytest.raises(LessonLoadError, match="not valid JSON"):
        default_loader("broken", repo_root=tmp_path)


def test_task_e_given_route_after_fix_with_stale_non_fix_tail_expect_scans_for_latest_fix():
    # Robustness: attempts[-1] is NOT assumed to be the fix just run. A stale/
    # foreign entry at the tail must not short-circuit the convergence check.
    # Here the tail is a regenerate, but a prior fix changed the lesson -> route
    # should still re-check (the latest FIX is what matters), NOT escalate.
    attempts = [
        {
            "kind": "fix",
            "reason": "blocking_issues",
            "ts": "t1",
            "lesson_hash_before": "sha256:a",
            "lesson_hash_after": "sha256:b",
            "blocking_fingerprint": "sha256:fp1",
        },
        {
            "kind": "regenerate",
            "reason": "fix_budget_exhausted",
            "ts": "t2",
            "lesson_hash_before": "sha256:b",
            "lesson_hash_after": "sha256:c",
            "blocking_fingerprint": "sha256:fp1",
        },
    ]
    # The latest fix changed the lesson (a != b) with a unique fingerprint, so
    # the convergence check against the latest FIX should pass (re-check).
    assert route_after_fix({"attempts": attempts}) == "checks"


def test_task_e_given_route_after_fix_with_no_fix_attempts_expect_human():
    # No fix attempt at all -> escalate to human (do not assume attempts[-1] is a fix).
    attempts = [
        {
            "kind": "regenerate",
            "reason": "fix_budget_exhausted",
            "ts": "t",
            "lesson_hash_before": "sha256:a",
            "lesson_hash_after": "sha256:b",
            "blocking_fingerprint": "sha256:fp1",
        }
    ]
    assert route_after_fix({"attempts": attempts}) == "human_gate"


# ---------------------------------------------------------------------------
# Task F: noop not a CLI choice; still importable + injectable
# ---------------------------------------------------------------------------


def test_task_f_given_cli_run_parser_expect_noop_not_a_choice():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    _add_run_parser(sub)
    run_parser = sub.choices["run"]
    fixer_action = next(a for a in run_parser._actions if a.dest == "fixer")
    judge_action = next(a for a in run_parser._actions if a.dest == "judge")

    assert "noop" not in fixer_action.choices
    assert "noop" not in judge_action.choices
    assert set(fixer_action.choices) == {"codex"}
    assert set(judge_action.choices) == {"real"}


def test_task_f_given_registries_expect_noop_absent():
    assert "noop" not in K_FIXERS
    assert "noop" not in K_JUDGES
    # The live choices remain selectable.
    assert "codex" in K_FIXERS
    assert "real" in K_JUDGES


def test_task_f_given_noop_importable_expect_injectable_into_graph_deps():
    # noop_judge and noop_fixer stay importable and constructible into GraphDeps.
    deps = GraphDeps(loader=lambda slug, *, repo_root: LoadedLesson(lesson={}), fixer=noop_fixer, judge=noop_judge)
    assert deps.fixer is noop_fixer
    assert deps.judge is noop_judge


def test_task_f_given_run_graph_default_expect_offline_safe_noop_deps(tmp_path: Path, monkeypatch):
    # The library default (no fixer_name/judge_name/deps) stays offline-safe:
    # run_graph builds noop deps directly without noop being a registry choice.
    repo = tmp_path
    lesson = _ordinal_lesson()
    (repo / "data" / "lessons").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "concept_requirements").mkdir(parents=True, exist_ok=True)
    (repo / "data" / "lessons" / "ordinal_numbers.json").write_text(
        json.dumps(lesson, ensure_ascii=False), encoding="utf-8"
    )
    (repo / "data" / "concept_requirements" / "ordinal_numbers.json").write_text(
        json.dumps(_ordinal_requirements(), ensure_ascii=False), encoding="utf-8"
    )
    import lesson_builder.pipeline.graph_runner as graph_runner

    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo / "store" / "checkpoints.db")

    result = run_graph("ordinal_numbers", repo_root=repo, run_id="taskF_default")

    # No live LLM call was made (offline noop deps); the thread parked at the gate.
    assert result["next"] == ["human_gate"]
    assert result["park_status"] == "parked"


# ---------------------------------------------------------------------------
# Task C acceptance: load-bearing + no pedagogy review (judge failed) != clean
# ---------------------------------------------------------------------------


def test_task_c_acceptance_given_load_bearing_and_judge_failed_expect_not_clean(tmp_path: Path):
    # Full graph run: floors load-bearing, judge records a pedagogy failure.
    # The lesson must NOT park as "clean" -- the pedagogy_unavailable revision
    # target routes it through the fix loop and escalates to human with a
    # visible blocker + llm_status in the payload.
    repo_root = tmp_path
    _write_load_bearing_floors(repo_root, floor=5)

    def judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
        return {
            "pedagogy_review": None,
            "objective_alignment_review": None,
            "answer_review": None,
            "naturalness_review": None,
            "_llm_status": {K_SURFACE_PEDAGOGY: K_LLM_STATUS_BACKEND_DOWN},
        }

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        return LoadedLesson(lesson=_ordinal_lesson(), requirements=_ordinal_requirements())

    deps = GraphDeps(loader=loader, fixer=noop_fixer, judge=judge, exporter=_RecordingExporter())
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "ordinal_numbers:taskC_acceptance"}}

    graph.invoke(_graph_input("taskC_acceptance", repo_root), config=config)
    state = graph.get_state(config)

    assert state.next == ("human_gate",)
    issues = state.values["current_issues"]
    # pedagogy_unavailable is a revision target (actionable) -> the loop ran.
    assert any(i.get("check_id") == "pedagogy_unavailable" and i.get("revision_target") for i in issues)
    # llm_status is visible in state (and would be in the interrupt payload).
    assert state.values.get("llm_status", {}).get(K_SURFACE_PEDAGOGY) == K_LLM_STATUS_BACKEND_DOWN
