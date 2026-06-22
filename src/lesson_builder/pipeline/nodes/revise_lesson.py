"""Entry point: ``revise_lesson``.

Whole-lesson revise author for the ``improve`` operation. Mirrors
``author_fixer`` (structured ``Lesson`` output via the shared structured-output
path) but is driven by the ``ImprovementSpec`` rather than blocking issues.
Reuses ``author_fixer``'s structure; differs only in the prompt.

On a known LLM failure (backend down, quota, or an unrepairable parse) it
returns the ORIGINAL lesson unchanged. The graph's convergence guard then sees
``lesson_hash_before == lesson_hash_after`` and escalates to a human, so a failed
revise degrades to "park for review" rather than shipping a bad revision.
"""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec
from lesson_builder.schema import Lesson


def _build_prompt(spec: ImprovementSpec, lesson: dict[str, Any]) -> str:
    return (
        "Revise this Norwegian (Bokmål) language lesson to address the improvement request below. "
        "Preserve the lesson key, objectives, and overall shape; improve clarity, pedagogy, and "
        "Bokmål quality where the request applies. Keep every required anchor form. Return the "
        "COMPLETE revised lesson.\n\n"
        f"Improvement request: {spec.interpretation_summary}\n"
        f"Target content: {spec.target_content or 'the lesson topic'}\n"
        f"Bloom focus: {spec.bloom_level or 'any'}\n\n"
        f"Current lesson JSON:\n{json.dumps(lesson, ensure_ascii=False)}"
    )


def revise_lesson(
    spec: ImprovementSpec, lesson: dict[str, Any], *, author_agent: Any | None = None
) -> dict[str, Any]:
    """Revise ``lesson`` per ``spec``; return a NEW validated ``Lesson`` dict.

    ``author_agent`` defaults to the ``author()`` profile so callers can inject
    a fake for offline tests (consistent with ``add_exercises``/
    ``add_explanation``). Returns the ORIGINAL ``lesson`` unchanged on a known
    LLM failure so the convergence guard escalates to a human. Unexpected
    exceptions propagate.
    """
    prompt = _build_prompt(spec, lesson)
    agent = author_agent or author()
    try:
        revised = agent.structured(Lesson).invoke(prompt)
    except (BackendDownException, LlmQuotaException, LlmParseException):
        return lesson
    return revised.model_dump(mode="json")


__all__ = ["revise_lesson"]
