"""Entry point: ``add_explanation``.

Author one or more explanation sections for the ``add_explanation`` (and, for
now, ``improve``) improvement operations. An explanation is a ``Section`` with
role ``orient`` (no objective-cardinality constraint) carrying paragraph blocks.

The author agent produces the section content; the back-half validates it. On a
malformed/empty agent response the lesson is returned unchanged so the flow
still parks for a human rather than crashing.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec
from lesson_builder.pipeline.llm.base import extract_json_object


def add_explanation(spec: ImprovementSpec, lesson: dict[str, Any], *, author_agent: Any) -> dict[str, Any]:
    """Append authored explanation section(s) to ``lesson``; return a new dict."""
    prompt = _build_prompt(spec, lesson)
    raw = author_agent.invoke(prompt)
    updated = deepcopy(lesson)
    try:
        generated = extract_json_object(raw)
    except ValueError:
        return updated
    sections = generated.get("elements") or generated.get("sections") or []
    for section in sections:
        section.setdefault("id", f"improve_explanation_{uuid.uuid4().hex[:8]}")
        updated["elements"].append(section)
    return updated


def _build_prompt(spec: ImprovementSpec, lesson: dict[str, Any]) -> str:
    import json

    # Lazy import: cold_author/__init__ eagerly loads cold_author.exercises,
    # which imports node modules — a top-level import risks an init cycle.
    from lesson_builder.pipeline.cold_author.naturalness_guide import K_NATURALNESS_GUIDE

    return (
        "Author one explanation section that clarifies this lesson's objective(s) in Bokmål. "
        "Teach the pattern, do not just list rules: give the rule, a short why, and a concrete "
        "worked example. Gloss any grammar term on first use. Write TO the learner, natural and "
        "level-appropriate; no meta-talk about the course or CEFR levels. Keep it to a tight "
        "paragraph or two. Span values are plain text only (no markdown characters).\n\n"
        'Return ONLY JSON: {"elements": [{"element_kind": "section", "role": "orient", '
        '"objective_ids": [], "title": "<short title>", "blocks": [{"kind": "paragraph", '
        '"spans": [{"kind": "text", "value": "<explanation>"}]}]}]}.\n\n'
        f"REQUEST: {spec.interpretation_summary}\n"
        f"OBJECTIVES: {json.dumps(lesson.get('objectives', []), ensure_ascii=False)}\n"
        f"{K_NATURALNESS_GUIDE}"
    )


__all__ = ["add_explanation"]
