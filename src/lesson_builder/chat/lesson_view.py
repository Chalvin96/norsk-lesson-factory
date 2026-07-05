"""Pure renderer: ``render_lesson(lesson) -> list[str]``.

Flattens the span-structured lesson JSON into compact, terminal-readable lines
for the chat REPL's ``show <slug>`` shortcut. Kept pure (dict in, lines out) so
the same renderer can back a future visual editor. Element ids are surfaced so
the operator can target a follow-up edit (e.g. "change ex_choose_common").
"""

from __future__ import annotations

from typing import Any

K_PREVIEW_WIDTH = 96
K_TEXT_BLOCK_KINDS = ("paragraph", "example", "callout", "rule")


def render_lesson(lesson: dict[str, Any]) -> list[str]:
    """Render a lesson dict to compact display lines."""
    key = lesson.get("key") or lesson.get("concept_slug") or "?"
    title = lesson.get("title") or key
    cefr = lesson.get("cefr_level") or "?"
    lines = [f"{key} — {title} ({cefr})"]
    if lesson.get("goal"):
        lines.append(f"Goal: {lesson['goal']}")
    objectives = lesson.get("objectives") or []
    if objectives:
        lines.append(f"Objectives ({len(objectives)}):")
        for obj in objectives:
            lines.append(f"  · {obj.get('id')}: {obj.get('statement')}")
    elements = lesson.get("elements") or []
    lines.append(f"{len(elements)} elements:")
    for element in elements:
        lines.extend(_render_element(element))
    return lines


def _render_element(element: dict[str, Any]) -> list[str]:
    eid = element.get("id", "?")
    if element.get("element_kind") == "exercise":
        return _render_exercise(element, eid)
    return _render_section(element, eid)


def _render_section(element: dict[str, Any], eid: str) -> list[str]:
    role = element.get("role") or "section"
    title = element.get("title") or ""
    head = f"  [{eid}]  section · {role}"
    if title:
        head += f'  "{title}"'
    lines = [head]
    preview = _first_block_text(element.get("blocks") or [])
    if preview:
        lines.append(f"      {_truncate(preview)}")
    return lines


def _render_exercise(element: dict[str, Any], eid: str) -> list[str]:
    operation = element.get("operation") or "?"
    bloom = element.get("bloom_level") or "?"
    lines = [f"  [{eid}]  exercise · {operation}  ({bloom})"]
    prompt = _spans_to_text(element.get("prompt"))
    if prompt:
        lines.append(f"      Q: {_truncate(prompt)}")
    answer = _answer_preview(operation, element.get("payload") or {})
    if answer:
        lines.append(f"      ✓ {_truncate(answer)}")
    return lines


def _answer_preview(operation: str, payload: dict[str, Any]) -> str:
    """Best-effort correct-answer preview, dispatched by exercise operation."""
    if operation == "choose":
        answer_id = payload.get("answer_id")
        for option in payload.get("options") or []:
            if option.get("option_id") == answer_id:
                return option.get("text") or ""
        return ""
    if operation == "recall_fill":
        return _recall_fill_answer(payload.get("segments") or [])
    if operation == "build":
        return _build_answer(payload)
    if operation == "judge":
        verdict = "correct" if payload.get("is_correct") else "incorrect"
        sentence = _spans_to_text(payload.get("sentence"))
        return f"{verdict}: {sentence}" if sentence else verdict
    if operation == "find_fix":
        return payload.get("feedback") or ""
    if operation == "categorize":
        return f"{len(payload.get('items') or [])} items → {len(payload.get('buckets') or [])} buckets"
    if operation == "match_pairs":
        return f"{len(payload.get('pairs') or [])} pairs"
    return ""


def _recall_fill_answer(segments: list[dict[str, Any]]) -> str:
    """Reconstruct the filled sentence, inserting [answer] for each blank."""
    parts: list[str] = []
    for segment in segments:
        if segment.get("kind") == "blank":
            options = segment.get("options") or []
            index = segment.get("answer_index")
            filled = options[index] if isinstance(index, int) and 0 <= index < len(options) else "?"
            parts.append(f"[{filled}]")
        else:
            parts.append("".join(s.get("value", "") for s in segment.get("spans") or []))
    return "".join(parts).strip()


def _build_answer(payload: dict[str, Any]) -> str:
    """Join build tokens in their answer order into the target sentence."""
    by_id = {t.get("token_id"): t.get("text", "") for t in payload.get("tokens") or []}
    return " ".join(by_id.get(tid, "") for tid in payload.get("answer_order") or []).strip()


def _first_block_text(blocks: list[dict[str, Any]]) -> str:
    for block in blocks:
        if block.get("kind") in K_TEXT_BLOCK_KINDS:
            text = _spans_to_text(block.get("spans"))
            if text:
                return text
    return ""


def _spans_to_text(spans: Any) -> str:
    if not isinstance(spans, list):
        return ""
    return "".join(
        span.get("value", "") for span in spans if isinstance(span, dict)
    ).strip()


def _truncate(text: str, width: int = K_PREVIEW_WIDTH) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"
