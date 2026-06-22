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

from lesson_builder.pipeline.llm.base import extract_json_object
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec


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

    return (
        "Author one explanation section that clarifies this lesson's objective(s) in Bokmål. "
        'Return ONLY JSON: {"elements": [{"element_kind": "section", "role": "orient", '
        '"objective_ids": [], "title": "<short title>", "blocks": [{"kind": "paragraph", '
        '"spans": [{"kind": "text", "value": "<explanation>"}]}]}]}.\n\n'
        f"REQUEST: {spec.interpretation_summary}\n"
        f"OBJECTIVES: {json.dumps(lesson.get('objectives', []), ensure_ascii=False)}"
    )


__all__ = ["add_explanation"]
