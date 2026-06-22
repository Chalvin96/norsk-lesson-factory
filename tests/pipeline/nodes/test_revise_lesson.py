"""Task D: ``revise_lesson`` is a whole-lesson revise author that mirrors
``author_fixer`` (structured ``Lesson`` output) but is driven by the
``ImprovementSpec`` rather than blocking issues. On a known LLM failure it
returns the original lesson unchanged so the convergence guard escalates.

Entry point: ``revise_lesson``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec
from lesson_builder.pipeline.nodes.revise_lesson import revise_lesson
from lesson_builder.schema import Lesson

ROOT = Path(__file__).resolve().parents[3]
_REAL_LESSON = json.loads((ROOT / "data" / "lessons" / "ordinal_numbers.json").read_text(encoding="utf-8"))


class _SpyAgent:
    """Single-hop agent that records the prompt and returns a canned response.

    Mirrors ``Agent.invoke`` (returns a string) for the structured-output path.
    """

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []

    def invoke(self, prompt: str, **kw: Any) -> str:
        self.calls.append(prompt)
        return self._response

    def structured(self, schema: type) -> _StructuredSpy:
        return _StructuredSpy(self, schema)


class _StructuredSpy:
    def __init__(self, agent: _SpyAgent, schema: type) -> None:
        self._agent = agent
        self._schema = schema

    def invoke(self, prompt: str, **kw: Any) -> Any:
        raw = self._agent.invoke(prompt)
        import json as _json

        return self._schema.model_validate(_json.loads(raw) if raw.strip().startswith("{") else {})


class _RaisingAgent:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def invoke(self, prompt: str, **kw: Any) -> str:
        raise self._exc

    def structured(self, schema: type) -> _RaisingStructured:
        return _RaisingStructured(self)


class _RaisingStructured:
    def __init__(self, agent: _RaisingAgent) -> None:
        self._agent = agent

    def invoke(self, prompt: str, **kw: Any) -> Any:
        raise self._agent._exc


def _spec() -> ImprovementSpec:
    return ImprovementSpec(
        operation="improve",
        target_slug="ordinal_numbers",
        confidence="high",
        interpretation_summary="make the explanation of ordinals clearer for beginners",
    )


def test_revise_lesson_given_valid_response_expect_revised_valid_lesson(monkeypatch):
    # setup: the author returns a valid revised lesson (add a goal suffix to prove revision).
    revised = {**_REAL_LESSON, "goal": _REAL_LESSON["goal"] + " (revised for clarity)"}
    response = json.dumps(revised, ensure_ascii=False)
    spy = _SpyAgent(response)
    monkeypatch.setattr("lesson_builder.pipeline.nodes.revise_lesson.author", lambda: spy)

    # execute
    result = revise_lesson(_spec(), _REAL_LESSON)

    # assert: the result is a validated Lesson dict with the revised goal.
    Lesson.model_validate(result)
    assert result["goal"].endswith("(revised for clarity)")
    assert result != _REAL_LESSON


def test_revise_lesson_given_backend_down_expect_original_lesson_unchanged(monkeypatch):
    spy = _RaisingAgent(BackendDownException("down"))
    monkeypatch.setattr("lesson_builder.pipeline.nodes.revise_lesson.author", lambda: spy)
    original = {**_REAL_LESSON, "untouched": True}

    result = revise_lesson(_spec(), original)

    assert result == original


def test_revise_lesson_prompt_references_spec_not_blocking_issues(monkeypatch):
    # the revise prompt is driven by the ImprovementSpec, not by blocking issues.
    spy = _SpyAgent(json.dumps(_REAL_LESSON, ensure_ascii=False))
    monkeypatch.setattr("lesson_builder.pipeline.nodes.revise_lesson.author", lambda: spy)

    revise_lesson(_spec(), _REAL_LESSON)

    prompt = spy.calls[0]
    assert "make the explanation of ordinals clearer for beginners" in prompt
    # it must NOT ask the author to resolve "blocking issues" (that's author_fixer's job).
    assert "Blocking issues to resolve" not in prompt
