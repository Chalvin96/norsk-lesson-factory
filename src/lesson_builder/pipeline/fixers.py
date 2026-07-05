"""Real author fixer: repairs a lesson via the codex ``author`` agent profile.

Wired as a ``GraphDeps.fixer`` so the lesson-QA fix/regenerate loop can author
real repairs end-to-end. The graph default stays ``noop_fixer`` (library/test
safe); the CLI opts into this for live runs (``graph run --fixer codex``).

The fixer is a pure function of its inputs plus one LLM call: it builds a prompt
from the full lesson + the blocking issues (message + fix_hint + unit_id), asks
the author to return the COMPLETE corrected lesson, and validates the response
against the internal ``Lesson`` schema via the shared structured-output path
(JSON-instruction + extract_json + Pydantic, with a bounded re-ask).

Task C: on a known LLM failure (backend down, quota, or an unrepairable parse)
the fixer RAISES the original exception instead of silently returning the
unchanged lesson. ``fix_node`` catches it, records the failure kind on the
AttemptRecord (distinguishing "model failed" from "model chose no-op"), and
no-ops the lesson so the convergence guard escalates to a human. A failed
author call is now a first-class signal, not a silent no-op that reads as
"model chose to change nothing."
"""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.pipeline.agents import author
from lesson_builder.schema import Lesson


def _format_issues(issues: list[dict[str, Any]]) -> str:
    if not issues:
        return "(no specific blocking issues were supplied)"
    lines: list[str] = []
    for issue in issues:
        unit = issue.get("unit_id") or "lesson"
        check = issue.get("check_id", "?")
        message = issue.get("message", "")
        line = f"- [{check}] ({unit}) {message}"
        hint = issue.get("fix_hint")
        if hint:
            line += f"\n    fix hint: {hint}"
        lines.append(line)
    return "\n".join(lines)


def _build_prompt(*, slug: str, lesson: dict[str, Any], issues: list[dict[str, Any]], kind: str) -> str:
    verb = "Regenerate" if kind == "regenerate" else "Repair"
    return (
        f"{verb} this Norwegian-language lesson so that every blocking issue listed below is "
        "resolved. Preserve everything that is already correct and pedagogically sound; change "
        "ONLY what the issues require. Keep the same lesson key, objectives, and overall shape "
        "unless an issue demands otherwise. Return the COMPLETE corrected lesson.\n\n"
        f"Lesson slug: {slug}\n\n"
        f"Blocking issues to resolve:\n{_format_issues(issues)}\n\n"
        f"Current lesson JSON:\n{json.dumps(lesson, ensure_ascii=False)}"
    )


def author_fixer(
    *,
    slug: str,
    lesson: dict[str, Any],
    issues: list[dict[str, Any]],
    kind: str,
) -> dict[str, Any]:
    """Author a repaired lesson aggregate addressing the blocking ``issues``.

    Returns a NEW lesson dict, validated against ``Lesson``. On a known LLM
    failure (backend down / quota / unrepairable parse) the exception PROPAGATES
    so ``fix_node`` can record the failure kind on the AttemptRecord (Task C);
    the node then no-ops the lesson and the convergence guard escalates to a
    human. Unexpected exceptions also propagate (they are bugs, not operational
    failures).
    """
    prompt = _build_prompt(slug=slug, lesson=lesson, issues=issues, kind=kind)
    # Task C: do not swallow _LLM_FAILURES here -- let them propagate so the
    # graph records a first-class failure kind on the AttemptRecord instead of
    # silently reading as a no-op.
    repaired = author().structured(Lesson).invoke(prompt)
    return repaired.model_dump(mode="json")
