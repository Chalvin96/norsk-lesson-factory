"""Tests for the real author fixer.

Hermetic tests use a fake LLM client through the real structured-output path.
The smoke test (LLM_SMOKE=1) exercises the actual codex backend end-to-end.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lesson_builder.pipeline.fixers import author_fixer
from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.schema import Lesson

from .fakes import FakeLlmClient, fake_agent

ROOT = Path(__file__).resolve().parents[2]
_REAL_LESSON = json.loads((ROOT / "data" / "lessons" / "ordinal_numbers.json").read_text())


def test_author_fixer_given_valid_response_expect_repaired_lesson(monkeypatch):
    # Arrange: the author returns a valid internal lesson (use a real one as the "repair").
    response = json.dumps(_REAL_LESSON, ensure_ascii=False)
    client = FakeLlmClient.responding(response)
    monkeypatch.setattr("lesson_builder.pipeline.fixers.author", lambda: fake_agent(client))

    # Act
    result = author_fixer(slug="ordinal_numbers", lesson={"key": "stale"}, issues=[], kind="fix")

    # Assert: the result is a validated Lesson dict matching the authored repair.
    assert result == Lesson.model_validate(_REAL_LESSON).model_dump(mode="json")
    assert result != {"key": "stale"}


def test_author_fixer_given_backend_down_expect_raises_first_class(monkeypatch):
    # Arrange: every backend hop fails -> known operational failure.
    client = FakeLlmClient.raising(BackendDownException("down"))
    monkeypatch.setattr("lesson_builder.pipeline.fixers.author", lambda: fake_agent(client))
    original = {"key": "ordinal_numbers", "untouched": True}

    # Act/Assert (Task C): the fixer no longer silently swallows the failure and
    # returns the original lesson. It raises first-class so ``fix_node`` can
    # record the failure kind on the AttemptRecord (distinguishing "model failed"
    # from "model chose no-op"). The node then no-ops the lesson itself.
    with pytest.raises(BackendDownException):
        author_fixer(slug="ordinal_numbers", lesson=original, issues=[], kind="fix")


@pytest.mark.skipif(not os.environ.get("LLM_SMOKE"), reason="set LLM_SMOKE=1 to run the real codex fixer e2e")
def test_author_fixer_given_real_codex_backend_expect_changed_valid_lesson():
    # A deliberately broken copy: blank the lesson goal so the author has something to repair.
    broken = {**_REAL_LESSON, "goal": ""}
    issues = [
        {
            "check_id": "structural",
            "unit_id": "lesson",
            "message": "Lesson goal is empty.",
            "fix_hint": "Write a concrete one-sentence learning goal for this lesson.",
        }
    ]

    result = author_fixer(slug="ordinal_numbers", lesson=broken, issues=issues, kind="fix")

    Lesson.model_validate(result)  # the author returned a schema-valid lesson
    assert result.get("goal"), "expected the author to fill the empty goal"
    assert result != broken
