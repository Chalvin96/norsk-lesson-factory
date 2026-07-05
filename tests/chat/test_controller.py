"""Entry point: ``handle_chat_message``.

Behavioral tests for the LAN chat controller. These tests inject fake operator
decisions so no live LLM calls are made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.chat.controller import (
    ChatOperatorDecision,
    ChatSessionState,
    handle_chat_message,
)


class _FakeStructuredAgent:
    def __init__(self, decision: ChatOperatorDecision) -> None:
        self._decision = decision

    def invoke(self, prompt: str) -> ChatOperatorDecision:
        assert "Allowed actions" in prompt
        return self._decision


class _FakeOperatorAgent:
    def __init__(self, decision: ChatOperatorDecision) -> None:
        self._decision = decision

    def structured(self, schema: type) -> _FakeStructuredAgent:
        del schema
        return _FakeStructuredAgent(self._decision)


def test_handle_chat_message_given_run_improvement_decision_expect_runs_flow_and_updates_session(
    monkeypatch, tmp_path: Path
):
    # setup
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    decision = ChatOperatorDecision(
        action="run_improvement",
        assistant_message="Adding an exercise now.",
        slug="ordinal_numbers",
        request_text="add one exercise",
        add_exercise=True,
        count=1,
    )
    operator_agent = _FakeOperatorAgent(decision)
    session = ChatSessionState(session_id="s1")

    class _FakeFlowResult:
        status = "parked_for_review"
        graph = {"run_id": "r123", "thread_id": "ordinal_numbers:r123", "park_status": "parked"}

        def model_dump(self, mode: str = "json") -> dict:
            del mode
            return {
                "status": self.status,
                "graph": self.graph,
                "draft_path": "tmp/improvement_drafts/ordinal_numbers__r123.json",
            }

    monkeypatch.setattr(
        "lesson_builder.chat.controller.run_improvement_flow",
        lambda *args, **kwargs: _FakeFlowResult(),
    )

    # execute
    result = handle_chat_message(
        "add one exercise",
        repo_root=tmp_path,
        session=session,
        operator_agent=operator_agent,
    )

    # assert
    assert result.action == "run_improvement"
    assert result.payload["graph"]["run_id"] == "r123"
    assert result.session.active_slug == "ordinal_numbers"
    assert result.session.last_run_id == "r123"


class _ExplodingOperatorAgent:
    """Fails if the LLM is consulted — proves a deterministic path skipped it."""

    def structured(self, schema: type) -> object:
        del schema
        raise AssertionError("operator LLM should not be called for a deterministic select")


def test_handle_chat_message_given_work_on_known_slug_expect_set_active_without_llm(
    tmp_path: Path,
):
    # setup: "work on <slug>" must select deterministically, never call the LLM
    # and never trigger a billable run_improvement.
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    session = ChatSessionState(session_id="s3")

    # execute
    result = handle_chat_message(
        "work on ordinal_numbers",
        repo_root=tmp_path,
        session=session,
        operator_agent=_ExplodingOperatorAgent(),
    )

    # assert
    assert result.action == "set_active_lesson"
    assert result.session.active_slug == "ordinal_numbers"


def test_handle_chat_message_given_work_on_plus_change_request_expect_uses_llm(
    monkeypatch, tmp_path: Path
):
    # setup: a select verb followed by a real change must NOT short-circuit; it
    # falls through to the LLM, which routes it to run_improvement.
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    decision = ChatOperatorDecision(
        action="run_improvement",
        assistant_message="Adding an exercise.",
        slug="ordinal_numbers",
        request_text="add an exercise",
        add_exercise=True,
    )
    monkeypatch.setattr(
        "lesson_builder.chat.controller.run_improvement_flow",
        lambda *args, **kwargs: _StubFlowResult(),
    )
    session = ChatSessionState(session_id="s4")

    # execute
    result = handle_chat_message(
        "work on adding an exercise to ordinal_numbers",
        repo_root=tmp_path,
        session=session,
        operator_agent=_FakeOperatorAgent(decision),
    )

    # assert
    assert result.action == "run_improvement"


class _StubFlowResult:
    status = "parked_for_review"
    graph = {"run_id": "r9", "park_status": "parked"}

    def model_dump(self, mode: str = "json") -> dict:
        del mode
        return {"status": self.status, "graph": self.graph, "draft_path": "tmp/x.json"}


def test_handle_chat_message_given_list_lessons_decision_expect_returns_known_slugs(
    tmp_path: Path,
):
    # setup
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    _write_repo_with_lesson(tmp_path, "adjective_agreement")
    decision = ChatOperatorDecision(
        action="list_lessons",
        assistant_message="Here are the lessons.",
    )
    operator_agent = _FakeOperatorAgent(decision)
    session = ChatSessionState(session_id="s2")

    # execute
    result = handle_chat_message(
        "what lessons exist?",
        repo_root=tmp_path,
        session=session,
        operator_agent=operator_agent,
    )

    # assert
    assert result.action == "list_lessons"
    assert result.payload == {"lessons": ["adjective_agreement", "ordinal_numbers"]}


def test_handle_chat_message_given_show_thread_unknown_slug_expect_value_error(
    tmp_path: Path,
):
    # setup: an LLM-controlled show_thread decision carrying a slug that is not
    # in the known lessons list must be rejected before reaching graph_runner
    # (defense-in-depth alongside graph_runner's path-traversal guard).
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    decision = ChatOperatorDecision(
        action="show_thread",
        assistant_message="Showing the latest thread.",
        slug="../../etc/passwd",
        run_id="r1",
    )
    operator_agent = _FakeOperatorAgent(decision)
    session = ChatSessionState(session_id="s5")

    # execute + assert: raises before graph_runner.show_thread is invoked
    with pytest.raises(ValueError, match="unknown lesson slug"):
        handle_chat_message(
            "show the latest thread",
            repo_root=tmp_path,
            session=session,
            operator_agent=operator_agent,
        )


def test_handle_chat_message_given_resume_thread_unknown_slug_expect_value_error(
    tmp_path: Path,
):
    # setup: same guard applies to resume_thread — an unknown slug from the
    # LLM must not reach resume_thread (which could otherwise re-enter a graph
    # with attacker-influenced state).
    _write_repo_with_lesson(tmp_path, "ordinal_numbers")
    decision = ChatOperatorDecision(
        action="resume_thread",
        assistant_message="Accepting the latest thread.",
        slug="definitely_not_a_lesson",
        run_id="r1",
        decision="accept",
    )
    operator_agent = _FakeOperatorAgent(decision)
    session = ChatSessionState(session_id="s6")

    # execute + assert
    with pytest.raises(ValueError, match="unknown lesson slug"):
        handle_chat_message(
            "accept the latest parked thread",
            repo_root=tmp_path,
            session=session,
            operator_agent=operator_agent,
        )


def _write_repo_with_lesson(repo_root: Path, slug: str) -> None:
    lessons_root = repo_root / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    (lessons_root / f"{slug}.json").write_text("{}", encoding="utf-8")
