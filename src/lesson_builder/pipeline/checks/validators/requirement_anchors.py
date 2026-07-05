"""Entry point: ``requirement_anchors_check``.

Deterministic check: ensures required anchor forms from the concept
requirements appear somewhere in the lesson text. No-ops if no requirements
are supplied.
"""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult


def requirement_anchors_check(
    lesson: dict[str, Any],
    requirements: dict[str, Any] | None = None,
) -> list[CheckResult]:
    """Ensure required anchor forms from the concept requirements appear in the lesson text.

    Callers pass the already-resolved requirements entry rather than having the
    check reach into repo paths on its own.
    """
    if not requirements:
        return []

    anchors = requirements.get("required_anchor_forms") or []
    if not anchors:
        return []

    elements = lesson.get("elements", [])
    all_text = json.dumps(elements, ensure_ascii=False).lower()
    missing = [anchor for anchor in anchors if str(anchor).lower() not in all_text]
    if not missing:
        return []

    return [
        CheckResult(
            check_id="requirement_anchors",
            severity="blocker",
            message=f"missing requirement anchors: {missing}",
            fix_hint="Add the missing required anchor forms from the concept requirements.",
        )
    ]
