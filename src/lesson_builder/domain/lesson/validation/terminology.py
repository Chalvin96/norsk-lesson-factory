"""Entry point: ``extract_learner_text``.

Shared learner-facing text extraction used by BOTH the live gate
(``terminology_check``) and the audit CLI. The extraction walks the same span
tree for both call sites so the audit baseline is directly comparable to the
live findings. ``extract_learner_text`` returns location metadata (text + path
+ unit_id + context_kind), not bare strings, so findings carry actionable fix
hints.

``data/terminology/glossary.yaml`` is the authoritative machine source of truth.
The YAML registry holds concept-oriented entries with preferred/alternative
labels and forbidden phrases (SKOS/TBX style). The markdown style guide
(``knowledge/quality/terminology.md``) is rationale and usage
documentation; the CLI list/audit/ban/unban commands read and write the YAML
registry, not the markdown table.

Not a check itself — the deterministic check lives in
``checks/validators/terminology.py``.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.terminology import ContextKind
from lesson_builder.domain.lesson.models.terminology import LearnerText

# ---------------------------------------------------------------------------
# Shared learner-facing text extraction
# ---------------------------------------------------------------------------


def extract_learner_text(lesson: dict[str, Any]) -> list[LearnerText]:
    """Walk the lesson span tree and return every learner-facing text fragment.

    Returns location-tagged fragments (text + path + unit_id + context_kind) so
    findings can name the exact block/exercise/option that surfaced a term. The
    SAME function feeds the live gate and the audit CLI so the two produce
    identical findings for the same lesson.

    Scans: section prose/reading/rule/example/list blocks, table headers/cells,
    callouts, exercise prompts/explanations, option text + why, judge payloads,
    feedback, match/categorize/build tokens, and speak targets. Excludes ids,
    translation metadata, bucket ids, and objective keys.
    """
    fragments: list[LearnerText] = []
    for element in lesson.get("elements", []) or []:
        if not isinstance(element, dict):
            continue
        kind = element.get("kind") or element.get("element_kind")
        if kind == "section":
            fragments.extend(_extract_section(element))
        elif kind == "exercise":
            fragments.extend(_extract_exercise(element))
    return fragments


# ---------------------------------------------------------------------------
# Private extraction helpers
# ---------------------------------------------------------------------------


def _extract_section(section: dict[str, Any]) -> list[LearnerText]:
    section_id = str(section.get("id", ""))
    fragments: list[LearnerText] = []
    for block_index, block in enumerate(section.get("blocks", []) or []):
        if not isinstance(block, dict):
            continue
        base_path = f"elements[{section_id}].blocks[{block_index}]"
        fragments.extend(_extract_section_block(block, base_path, section_id))
    return fragments


def _extract_section_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract learner text from one section block by block kind."""
    handlers = {
        "paragraph": _extract_prose_block,
        "heading": _extract_prose_block,
        "reading": _extract_prose_block,
        "rule": _extract_rule_block,
        "example": _from_example_block,
        "examples": _from_example_block,
        "list": _extract_list_block,
        "table": _extract_table_block,
        "callout": _extract_callout_block,
        "word_list": _extract_word_list_block,
    }
    kind = block.get("kind")
    handler = handlers.get(kind) if isinstance(kind, str) else None
    return handler(block, base_path, section_id) if handler is not None else []


def _extract_prose_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract paragraph, heading, or reading spans."""
    context_kind: ContextKind = "section_reading" if block.get("kind") == "reading" else "section_prose"
    return _from_spans(block.get("spans") or [], base_path, section_id, context_kind)


def _extract_rule_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract rule statements from a typed rule block."""
    fragments: list[LearnerText] = []
    for statement_index, statement in enumerate(block.get("statement") or []):
        if isinstance(statement, list):
            fragments.extend(
                _from_spans(statement, f"{base_path}.statement[{statement_index}]", section_id, "section_rule")
            )
        elif isinstance(statement, dict):
            fragments.extend(
                _from_spans([statement], f"{base_path}.statement[{statement_index}]", section_id, "section_rule")
            )
    return fragments


def _extract_list_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract list-item span text."""
    fragments: list[LearnerText] = []
    for item_index, item in enumerate(block.get("items") or []):
        if isinstance(item, list):
            fragments.extend(_from_spans(item, f"{base_path}.items[{item_index}]", section_id, "section_list_item"))
    return fragments


def _extract_table_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract table headers and cells."""
    fragments: list[LearnerText] = []
    for header_index, header in enumerate(block.get("headers") or []):
        fragments.extend(_from_spans(header, f"{base_path}.headers[{header_index}]", section_id, "table_header"))
    for row_index, row in enumerate(block.get("rows") or []):
        for cell_index, cell in enumerate(row):
            fragments.extend(
                _from_spans(cell, f"{base_path}.rows[{row_index}][{cell_index}]", section_id, "table_cell")
            )
    return fragments


def _extract_callout_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract paragraph and heading children from a callout."""
    fragments: list[LearnerText] = []
    for inner_index, inner in enumerate(block.get("blocks") or []):
        if isinstance(inner, dict) and inner.get("kind") in ("paragraph", "heading"):
            fragments.extend(
                _from_spans(
                    inner.get("spans") or [],
                    f"{base_path}.callout[{inner_index}]",
                    section_id,
                    "callout",
                )
            )
    return fragments


def _extract_word_list_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    """Extract term and form text from a word list."""
    fragments: list[LearnerText] = []
    for item_index, item in enumerate(block.get("items") or []):
        if not isinstance(item, dict):
            continue
        for field in ("term", "form"):
            value = item.get(field)
            if isinstance(value, str):
                fragments.append(
                    LearnerText(
                        text=value,
                        path=f"{base_path}.items[{item_index}].{field}",
                        unit_id=section_id,
                        context_kind="section_prose",
                    )
                )
    return fragments


def _from_example_block(block: dict[str, Any], base_path: str, section_id: str) -> list[LearnerText]:
    fragments: list[LearnerText] = []
    if "no" in block:
        fragments.extend(_from_spans(block["no"], f"{base_path}.no", section_id, "section_example"))
    if "en" in block:
        fragments.extend(_from_spans(block["en"], f"{base_path}.en", section_id, "section_example"))
    for item_index, item in enumerate(block.get("items") or []):
        if isinstance(item, dict):
            if "no" in item:
                fragments.extend(
                    _from_spans(item["no"], f"{base_path}.items[{item_index}].no", section_id, "section_example")
                )
            if "en" in item:
                fragments.extend(
                    _from_spans(item["en"], f"{base_path}.items[{item_index}].en", section_id, "section_example")
                )
    return fragments


def _extract_exercise(exercise: dict[str, Any]) -> list[LearnerText]:
    ex_id = str(exercise.get("id", ""))
    base_path = f"elements[{ex_id}]"
    fragments = _from_spans(exercise.get("prompt") or [], f"{base_path}.prompt", ex_id, "exercise_prompt")
    fragments.extend(
        _from_spans(exercise.get("explanation") or [], f"{base_path}.explanation", ex_id, "exercise_explanation")
    )
    payload = exercise.get("payload") or {}
    operation = exercise.get("operation")
    handlers = {
        "choose": _extract_choose_exercise,
        "judge": _extract_judge_exercise,
        "recall_fill": _extract_recall_fill_exercise,
        "match_pairs": _extract_match_pairs_exercise,
        "categorize": _extract_categorize_exercise,
        "build": _extract_token_exercise,
        "find_fix": _extract_find_fix_exercise,
        "speak": _extract_speak_exercise,
    }
    handler = handlers.get(operation) if isinstance(operation, str) else None
    if handler is not None:
        fragments.extend(handler(payload, base_path, ex_id))
    return fragments


def _extract_choose_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract option text, rationales, and the choose stem."""
    fragments: list[LearnerText] = []
    option_fields: tuple[tuple[str, ContextKind], ...] = (("text", "option_text"), ("why", "option_why"))
    for option_index, option in enumerate(payload.get("options") or []):
        if not isinstance(option, dict):
            continue
        for field, context_kind in option_fields:
            value = option.get(field)
            if isinstance(value, str):
                fragments.append(
                    LearnerText(
                        text=value,
                        path=f"{base_path}.payload.options[{option_index}].{field}",
                        unit_id=ex_id,
                        context_kind=context_kind,
                    )
                )
    stem = payload.get("stem")
    if isinstance(stem, list):
        fragments.extend(_from_spans(stem, f"{base_path}.payload.stem", ex_id, "exercise_prompt"))
    return fragments


def _extract_judge_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract the judged sentence and corrective feedback."""
    fragments = _from_spans(payload.get("sentence") or [], f"{base_path}.payload.sentence", ex_id, "judge_payload")
    feedback = payload.get("feedback")
    if isinstance(feedback, str):
        fragments.append(
            LearnerText(
                text=feedback,
                path=f"{base_path}.payload.feedback",
                unit_id=ex_id,
                context_kind="feedback",
            )
        )
    return fragments


def _extract_recall_fill_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract visible spans from recall-fill segments."""
    fragments: list[LearnerText] = []
    for segment_index, segment in enumerate(payload.get("segments") or []):
        if isinstance(segment, dict) and segment.get("kind") == "span":
            fragments.extend(
                _from_spans(
                    segment.get("spans") or [],
                    f"{base_path}.payload.segments[{segment_index}].spans",
                    ex_id,
                    "exercise_prompt",
                )
            )
    return fragments


def _extract_match_pairs_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract visible text from both matching sides."""
    return _extract_item_texts(payload, ("left", "right"), base_path, ex_id, "match_text")


def _extract_categorize_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract visible text from categorize items."""
    return _extract_item_texts(payload, ("items",), base_path, ex_id, "categorize_text")


def _extract_token_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract visible token text from a build exercise."""
    return _extract_item_texts(payload, ("tokens",), base_path, ex_id, "build_token")


def _extract_find_fix_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract find-fix tokens and corrective feedback."""
    fragments = _extract_item_texts(payload, ("tokens",), base_path, ex_id, "build_token")
    feedback = payload.get("feedback")
    if isinstance(feedback, str):
        fragments.append(
            LearnerText(
                text=feedback,
                path=f"{base_path}.payload.feedback",
                unit_id=ex_id,
                context_kind="feedback",
            )
        )
    return fragments


def _extract_speak_exercise(payload: dict[str, Any], base_path: str, ex_id: str) -> list[LearnerText]:
    """Extract a non-empty speak target."""
    target = payload.get("target")
    if isinstance(target, str) and target.strip():
        return [
            LearnerText(
                text=target,
                path=f"{base_path}.payload.target",
                unit_id=ex_id,
                context_kind="speak_target",
            )
        ]
    return []


def _extract_item_texts(
    payload: dict[str, Any], fields: tuple[str, ...], base_path: str, ex_id: str, context_kind: ContextKind
) -> list[LearnerText]:
    """Extract ``text`` values from one or more exercise item collections."""
    fragments: list[LearnerText] = []
    for field in fields:
        for item_index, item in enumerate(payload.get(field) or []):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                fragments.append(
                    LearnerText(
                        text=item["text"],
                        path=f"{base_path}.payload.{field}[{item_index}].text",
                        unit_id=ex_id,
                        context_kind=context_kind,
                    )
                )
    return fragments


def _from_spans(spans: object, path: str, unit_id: str, context_kind: ContextKind) -> list[LearnerText]:
    fragments: list[LearnerText] = []
    if not isinstance(spans, list):
        return fragments
    text = _spans_to_text(spans)
    if text:
        fragments.append(LearnerText(text=text, path=path, unit_id=unit_id, context_kind=context_kind))
    return fragments


def _spans_to_text(spans: list[Any]) -> str:
    """Flatten a portable-text span list to plain text, preserving order.

    Recurses into ``sentence`` container spans so their children's text is
    included. ``annotated`` is treated as a leaf (its ``value`` is used).
    """
    parts: list[str] = []
    for span in spans:
        if isinstance(span, dict) and isinstance(span.get("value"), str):
            parts.append(span["value"])
        elif isinstance(span, dict) and span.get("kind") == "sentence":
            parts.append(_spans_to_text(span.get("children") or []))
        elif isinstance(span, str):
            parts.append(span)
    return " ".join(parts)


__all__ = [
    "extract_learner_text",
]
