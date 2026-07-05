"""Tests for the improvement flow: triage, feasibility, draft authoring, and graph handoff."""

from __future__ import annotations

import json
from pathlib import Path

import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.improvement_flow import run_improvement_flow
from lesson_builder.pipeline.improvement_steps.triage import TriageClassification
from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_qa_graph import noop_fixer


class _FakeTriageAgent:
    """Offline triage agent returning a canned routing verdict."""

    def __init__(self, route: str, matched_slug: str | None = None) -> None:
        self._classification = TriageClassification(
            route=route, matched_slug=matched_slug, rationale="test triage"
        )

    def structured(self, schema: object) -> _FakeTriageAgent:
        return self

    def invoke(self, prompt: str) -> TriageClassification:
        return self._classification

K_TEST_ROOT = Path(__file__).resolve().parents[2]
_REAL_LESSON = json.loads(
    (K_TEST_ROOT / "data" / "lessons" / "ordinal_numbers.json").read_text(encoding="utf-8")
)


class _FakeAuthor:
    """Offline author: returns flat exercise specs and an explanation section.

    Carries both ``exercises`` (read by add_exercises, now in the flat-contract
    shape construct_payload expects) and ``elements`` (read by add_explanation)
    so one fake serves every operation without a live backend. For the ``improve``
    (whole-lesson revise) path it returns a complete internal ``Lesson`` so the
    structured-output validator accepts the revision.
    """

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, prompt: str, **kw) -> str:
        self.calls += 1
        # The revise prompt asks for the COMPLETE revised lesson; return one.
        if "Return the COMPLETE revised lesson" in prompt:
            return json.dumps(_REAL_LESSON, ensure_ascii=False)
        return (
            '{"exercises": [{"operation": "judge", "prompt_text": "Is this correct?", '
            '"explanation_text": "practice", "flat": {"sentence_no": "Eksempel.", '
            '"is_correct": true, "feedback": "ok"}}], '
            '"elements": [{"element_kind": "section", "role": "orient", "objective_ids": [], '
            '"title": "Forklaring", "blocks": [{"kind": "paragraph", '
            '"spans": [{"kind": "text", "value": "Dette forklarer temaet."}]}]}]}'
        )

    def structured(self, schema: type):
        from lesson_builder.pipeline.llm.base import extract_json_object

        agent = self

        class _Structured:
            def invoke(self_inner, prompt: str, **kw) -> object:
                raw = agent.invoke(prompt)
                return schema.model_validate(extract_json_object(raw))

        return _Structured()


def _write_repo_lesson(tmp_path: Path, slug: str) -> Path:
    repo_root = tmp_path
    lesson_payload = json.loads((K_TEST_ROOT / "data" / "lessons" / f"{slug}.json").read_text(encoding="utf-8"))
    requirements_payload = json.loads(
        (K_TEST_ROOT / "data" / "concept_requirements" / f"{slug}.json").read_text(encoding="utf-8")
    )
    (repo_root / "data" / "lessons").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "concept_requirements").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "lessons" / f"{slug}.json").write_text(
        json.dumps(lesson_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_root / "data" / "concept_requirements" / f"{slug}.json").write_text(
        json.dumps(requirements_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_root / "store").mkdir(parents=True, exist_ok=True)
    return repo_root


def test_run_improvement_flow_given_add_exercises_request_expect_parked_for_review(
    tmp_path: Path,
    monkeypatch,
):
    repo_root = _write_repo_lesson(tmp_path, "ordinal_numbers")
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")

    result = run_improvement_flow(
        "add 2 exercises",
        repo_root=repo_root,
        slug="ordinal_numbers",
        add_exercise=True,
        count=2,
        run_id="imp1",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    assert result.status == "parked_for_review"
    assert result.graph is not None
    assert result.graph["next"] == ["human_gate"]
    assert result.draft_path is not None
    assert (repo_root / result.draft_path).exists()


def test_run_improvement_flow_given_unknown_target_expect_needs_triage(tmp_path: Path, monkeypatch):
    repo_root = _write_repo_lesson(tmp_path, "ordinal_numbers")
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")

    result = run_improvement_flow(
        "add exercises on quantum physics phenomena",
        repo_root=repo_root,
        run_id="imp2",
        triage_agent=_FakeTriageAgent("new_topic"),
    )

    assert result.status == "needs_triage"
    assert result.triage is not None
    assert result.triage.proposed_action == "new_topic"


def test_run_improvement_flow_given_improve_request_expect_parked_for_review(tmp_path: Path, monkeypatch):
    repo_root = _write_repo_lesson(tmp_path, "ordinal_numbers")
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")

    result = run_improvement_flow(
        "improve the ordinal numbers lesson",
        repo_root=repo_root,
        run_id="imp3",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    # improve is now authored via the whole-lesson revise author (Task D), so it
    # parks for review instead of dead-ending at needs_human.
    assert result.status == "parked_for_review"
    assert result.graph["next"] == ["human_gate"]
    assert (repo_root / result.draft_path).exists()


def test_show_thread_after_improvement_given_duplicated_slug_run_id_expect_returns_draft(
    tmp_path: Path, monkeypatch
):
    # Regression: the chat operator LLM can echo the full thread_id it saw in its
    # prompt back as the run_id (e.g. "ordinal_numbers:abc123"), which made
    # thread_id_for produce a duplicated-slug id that never matched the
    # checkpointer. thread_id_for now strips a leading slug: prefix so the parked
    # improvement draft stays discoverable.
    repo_root = _write_repo_lesson(tmp_path, "ordinal_numbers")
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")

    result = run_improvement_flow(
        "add 2 exercises",
        repo_root=repo_root,
        slug="ordinal_numbers",
        add_exercise=True,
        count=2,
        run_id="show1",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )
    assert result.status == "parked_for_review"

    # Simulate the LLM-provided run_id that carries the slug prefix.
    duplicated_slug_run_id = f"ordinal_numbers:{result.graph['run_id']}"
    from lesson_builder.pipeline.graph_runner import show_thread

    thread = show_thread("ordinal_numbers", duplicated_slug_run_id, repo_root=repo_root)

    assert thread["thread_id"] == f"ordinal_numbers:{result.graph['run_id']}"
    assert thread["park_status"] == "parked"
    assert thread["next"] == ["human_gate"]
