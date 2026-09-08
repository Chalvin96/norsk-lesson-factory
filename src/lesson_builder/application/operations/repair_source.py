"""Entry points: `repair_exercise_source` repairs authored exercise YAML; shared transport helpers serve the rich-authoring workflow.

`repair_exercise_source` is called by the exercise-quality CLI, the review-edit
workflow, and the rich-authoring re-audit seam. `normalize_scalar_identifier_fields`,
`convert_legacy_recall_segment`, and `relocate_inline_blank_markers` (with
`count_inline_blank_markers` and their private scalar helpers) are the pure
transformations shared with
`workflow.lesson_generation.rich_authoring_exercise_repairs`.
"""

from __future__ import annotations

import json
import re
from typing import Any
from typing import cast

import yaml

from lesson_builder.application.operations.audit_source import audit_source_text
from lesson_builder.application.operations.convert_lesson_spans import K_SPANS_BLANK_MARKER
from lesson_builder.domain.lesson.models.source_repair import ExerciseSourceRepair
from lesson_builder.domain.lesson.models.source_repair import MechanicalRepair
from lesson_builder.formats.yaml import load_unique_yaml

K_SOURCE_REPAIR_TEXT_FIELDS = frozenset(
    {
        "prompt_md",
        "explanation_md",
        "sentence_md",
        "stem_md",
        "judge_prompt",
        "feedback",
        "text_md",
        "text",
        "why",
        "target",
    }
)
K_SOURCE_REPAIR_OPERATION_ALIASES = {
    "id": "handle",
    "operation": "op",
    "prompt": "prompt_md",
}
K_SOURCE_REPAIR_IDENTIFIER_FIELDS = frozenset(
    {
        "handle",
        "id",
        "objective",
        "objective_ref",
        "bloom",
        "blank_id",
        "token_id",
        "left_id",
        "right_id",
        "option_id",
        "item_id",
        "bucket_id",
        "answer_id",
        "error_token_id",
    }
)


def repair_exercise_source(
    exercises_yaml: str,
    *,
    lesson_text: str | None = None,
) -> ExerciseSourceRepair:
    """Apply safe transport repairs, then audit the resulting exercise source.

    This boundary deliberately does not invent answers, options, token order,
    punctuation, or lesson prose. A package that still has a structural issue
    after these repairs remains a human-review case. ``lesson_text`` is optional
    so callers can also use this function as a standalone YAML preflight; when
    supplied, marker/handle alignment is checked against the lesson copy.
    """
    raw, parsed_source = _parse_yaml_list(exercises_yaml)
    if raw is None:
        audit = audit_source_text(exercises_yaml, lesson_text=lesson_text)
        return ExerciseSourceRepair(exercises_yaml=exercises_yaml, audit=audit)

    repairs: list[MechanicalRepair] = []
    if parsed_source != exercises_yaml:
        exercises_yaml = parsed_source
        repairs.append(
            MechanicalRepair(
                code="yaml-scalar-quoting",
                location="document",
                explanation="Quoted plain text scalars containing YAML mapping-like colons.",
            )
        )
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        handle = str(item.get("handle", item.get("id", f"item-{index}")))
        _repair_aliases(item, handle, repairs)
        normalized_item = _normalize_mapping_strings(item, handle, repairs)
        if isinstance(normalized_item, dict):
            item.clear()
            item.update(normalized_item)
        _repair_recall_aliases(item, handle, repairs)
        _repair_recall_markers(item, handle, repairs)
        _repair_recall_punctuation(item, handle, repairs)
        _repair_scalar_ids(item, handle, repairs)

    repaired_yaml = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    audit = audit_source_text(repaired_yaml, lesson_text=lesson_text)
    return ExerciseSourceRepair(
        exercises_yaml=repaired_yaml,
        repairs=repairs,
        audit=audit,
    )


def normalize_scalar_identifier_fields(item: dict[str, Any]) -> set[str]:
    """Normalize scalar build/find-fix identifiers and name each changed field.

    Shared by the authored-source repair operation and the rich-authoring
    transport normalizer; callers own their own repair bookkeeping.
    """
    changed: set[str] = set()
    if item.get("op") not in {"build", "find_fix"}:
        return changed
    changed.update(_normalize_token_identifier_fields(item.get("tokens")))
    answer_order = item.get("answer_order")
    if isinstance(answer_order, list):
        normalized = [_normalize_scalar_identifier(value) for value in answer_order]
        if normalized != answer_order:
            item["answer_order"] = normalized
            changed.add("answer_order")
    if "error_token_id" in item:
        before = item["error_token_id"]
        item["error_token_id"] = _normalize_scalar_identifier(before)
        if before != item["error_token_id"]:
            changed.add("error_token_id")
    return changed


def convert_legacy_recall_segment(segment: object, *, blank_id: str) -> tuple[object, bool]:
    """Convert one legacy recall text or blank segment when unambiguous.

    Returns the replacement value and whether a conversion happened. The
    caller supplies the typed blank identifier so each repair path keeps its
    own generated identifier scheme.
    """
    if not isinstance(segment, dict):
        return segment, False
    if segment.get("type") == "text" and "text_md" not in segment and isinstance(segment.get("text"), str):
        return {"text_md": segment["text"]}, True
    converted_blank = _convert_legacy_recall_blank(segment, blank_id)
    return (converted_blank, True) if converted_blank is not None else (segment, False)


def count_inline_blank_markers(segments: list[Any]) -> int:
    """Count inline blank markers in recall text segments."""
    return sum(
        segment.get("text_md", "").count(K_SPANS_BLANK_MARKER)
        for segment in segments
        if isinstance(segment, dict) and isinstance(segment.get("text_md"), str)
    )


def relocate_inline_blank_markers(segments: list[Any]) -> list[Any] | None:
    """Relocate exactly matching inline blank markers into typed slots.

    Returns the relocated segment list, or ``None`` when there are no markers
    or the marker count does not match the typed blank count; the caller then
    decides between leaving the source for audit and failing the package.
    """
    blanks = [segment for segment in segments if isinstance(segment, dict) and "blank_id" in segment]
    marker_count = count_inline_blank_markers(segments)
    if marker_count == 0 or marker_count != len(blanks):
        return None
    repaired: list[Any] = []
    blank_index = 0
    for segment in segments:
        if isinstance(segment, dict) and "blank_id" in segment:
            continue
        relocated, blank_index = _relocate_text_segment(segment, blanks, blank_index)
        if relocated is None:
            repaired.append(segment)
            continue
        repaired.extend(relocated)
    return repaired


def _normalize_scalar_identifier(value: object) -> object:
    """Convert numeric and boolean identifiers to their YAML text form."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _convert_legacy_recall_blank(segment: dict[str, Any], blank_id: str) -> dict[str, Any] | None:
    """Convert one legacy recall blank with exactly one correct option."""
    if segment.get("type") != "blank" or "blank_id" in segment:
        return None
    options = segment.get("options")
    if not isinstance(options, list):
        return None
    texts = [option.get("text") if isinstance(option, dict) else option for option in options]
    correct = [
        position
        for position, option in enumerate(options)
        if isinstance(option, dict) and option.get("correct") is True
    ]
    if not all(isinstance(text, str) for text in texts) or len(correct) != 1:
        return None
    return {"blank_id": blank_id, "options": texts, "answer_index": correct[0]}


def _normalize_token_identifier_fields(value: object) -> set[str]:
    """Normalize token identifiers and name each field that changed."""
    if not isinstance(value, list):
        return set()
    changed: set[str] = set()
    for token in value:
        if not isinstance(token, dict) or "token_id" not in token:
            continue
        before = token["token_id"]
        token["token_id"] = _normalize_scalar_identifier(before)
        if before != token["token_id"]:
            changed.add("token_id")
    return changed


def _relocate_text_segment(
    segment: object,
    blanks: list[dict[str, Any]],
    blank_index: int,
) -> tuple[list[Any] | None, int]:
    """Split one marker-bearing text segment around its typed blanks."""
    text = segment.get("text_md") if isinstance(segment, dict) else None
    if not isinstance(text, str) or K_SPANS_BLANK_MARKER not in text:
        return None, blank_index
    relocated: list[Any] = []
    parts = text.split(K_SPANS_BLANK_MARKER)
    for part_index, part in enumerate(parts):
        if part:
            relocated.append({"text_md": part})
        if part_index < len(parts) - 1:
            relocated.append(blanks[blank_index])
            blank_index += 1
    return relocated, blank_index


def _parse_yaml_list(text: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Parse a YAML list with the strict loader used by source auditing.

    Duplicate mapping keys make the parse fail so the original text reaches
    ``audit_source_text`` with its ambiguity intact instead of silently
    keeping one duplicate value.
    """
    parsed_source = _quote_identifier_scalars(text)
    parsed_source = _quote_unquoted_text_scalars(parsed_source)
    try:
        raw = load_unique_yaml(parsed_source)
    except (TypeError, ValueError, yaml.YAMLError):
        return None, text
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        return None, parsed_source
    return cast(list[dict[str, Any]], raw), parsed_source


def _repair_aliases(item: dict[str, Any], handle: str, repairs: list[MechanicalRepair]) -> None:
    """Normalize transport aliases without changing authored learner content."""
    for alias, canonical in K_SOURCE_REPAIR_OPERATION_ALIASES.items():
        if canonical in item or alias not in item:
            continue
        item[canonical] = item.pop(alias)
        repairs.append(
            MechanicalRepair(
                code="transport-alias",
                location=handle,
                explanation=f"Moved {alias!r} to the canonical {canonical!r} field.",
            )
        )


def _normalize_mapping_strings(
    value: object, handle: str, repairs: list[MechanicalRepair], field_name: str | None = None
) -> object:
    """Return a recursively normalized exercise mapping."""
    if isinstance(value, dict):
        return {key: _normalize_mapping_strings(child, handle, repairs, key) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize_mapping_strings(child, handle, repairs, field_name) for child in value]
    if not isinstance(value, str) or field_name not in K_SOURCE_REPAIR_TEXT_FIELDS:
        return value
    normalized = re.sub(r"`([^`\n]*)`", lambda match: match.group(1), value)
    normalized = normalized.replace("`", "")
    normalized = normalized.replace("*", "").replace("_", "")
    normalized = re.sub(r"\\n", " ", normalized)
    normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
    if field_name in {"prompt_md", "explanation_md", "sentence_md", "stem_md"}:
        normalized = normalized.strip()
    if normalized != value:
        repairs.append(
            MechanicalRepair(
                code="inline-text-normalization",
                location=handle,
                explanation=f"Removed compiler-unsafe inline formatting from {field_name}.",
            )
        )
    return normalized


def _repair_recall_aliases(item: dict[str, Any], handle: str, repairs: list[MechanicalRepair]) -> None:
    """Convert legacy text/blank segment aliases to typed recall segments."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    converted: list[Any] = []
    changed = False
    for index, segment in enumerate(item["segments"]):
        replacement, segment_changed = convert_legacy_recall_segment(segment, blank_id=f"{handle}-blank-{index + 1}")
        converted.append(replacement)
        changed = changed or segment_changed
    if changed:
        item["segments"] = converted
        repairs.append(
            MechanicalRepair(
                code="recall-segment-alias",
                location=handle,
                explanation="Converted legacy recall segment aliases into typed slots.",
            )
        )


def _repair_recall_markers(item: dict[str, Any], handle: str, repairs: list[MechanicalRepair]) -> None:
    """Relocate exactly matching legacy inline blanks into typed slots."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    relocated = relocate_inline_blank_markers(item["segments"])
    if relocated is None:
        return
    item["segments"] = relocated
    repairs.append(
        MechanicalRepair(
            code="recall-inline-blank-relocation",
            location=handle,
            explanation="Relocated matching inline blank markers into typed recall slots.",
        )
    )


def _repair_scalar_ids(item: dict[str, Any], handle: str, repairs: list[MechanicalRepair]) -> None:
    """Normalize scalar token identifiers emitted as numbers or booleans."""
    changed_fields = normalize_scalar_identifier_fields(item)
    if "token_id" in changed_fields:
        repairs.append(
            MechanicalRepair(
                code="scalar-token-id",
                location=handle,
                explanation="Normalized a scalar token identifier to text.",
            )
        )
    if "answer_order" in changed_fields:
        repairs.append(
            MechanicalRepair(
                code="scalar-answer-order-id",
                location=handle,
                explanation="Normalized scalar answer-order identifiers to text.",
            )
        )


def _repair_recall_punctuation(item: dict[str, Any], handle: str, repairs: list[MechanicalRepair]) -> None:
    """Remove mechanically invalid whitespace before sentence punctuation."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    changed = 0
    for segment in item["segments"]:
        if not isinstance(segment, dict):
            continue
        changed += _repair_segment_punctuation(segment)
    if changed:
        repairs.append(
            MechanicalRepair(
                code="recall-punctuation-spacing",
                location=handle,
                explanation="Removed whitespace before punctuation in a keyed recall target.",
            )
        )


def _repair_segment_punctuation(segment: dict[str, Any]) -> int:
    """Normalize punctuation spacing in one recall segment."""
    changed = 0
    text_values: list[tuple[Any, Any]] = []
    if isinstance(segment.get("text_md"), str):
        text_values.append((segment, "text_md"))
    options = segment.get("options")
    if isinstance(options, list):
        text_values.extend((options, index) for index, option in enumerate(options) if isinstance(option, str))
    for container, key in text_values:
        before = container[key]
        after = re.sub(r"\s+([,.;:!?])", r"\1", before)
        if after != before:
            container[key] = after
            changed += 1
    return changed


def _quote_unquoted_text_scalars(text: str) -> str:
    """Quote plain YAML text fields containing a mapping-like colon.

    A configured model can wrap prose onto indented continuation lines without
    using a YAML block scalar. PyYAML then interprets a later ``:`` as a new
    mapping. Fold only continuation lines belonging to known text fields and
    quote the resulting scalar; nested exercise mappings remain untouched.
    """
    text_fields = K_SOURCE_REPAIR_TEXT_FIELDS
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _text_scalar_match(line, text_fields)
        if match is None:
            output.append(line)
            index += 1
            continue
        value = match.group(3)
        if value[:1] in {"'", '"', "[", "{"}:
            output.append(line)
            index += 1
            continue
        replacement, consumed = _fold_text_scalar(lines, index, match)
        if replacement is None:
            output.extend(lines[index : index + consumed])
            index += consumed
            continue
        output.append(replacement)
        index += consumed
    return "".join(output)


def _text_scalar_match(line: str, text_fields: frozenset[str]) -> re.Match[str] | None:
    """Match a known plain-text YAML field on one source line."""
    match = re.match(r"^(\s*(?:-\s+)?)([A-Za-z_][A-Za-z0-9_-]*):\s+(.+?)(\r?\n)?$", line)
    if match is None or match.group(2) not in text_fields:
        return None
    return match


def _fold_text_scalar(
    lines: list[str],
    index: int,
    match: re.Match[str],
) -> tuple[str | None, int]:
    """Fold a text field's indented continuation lines and quote it when needed."""
    value = match.group(3)
    prefix = match.group(1)
    base_indent = len(prefix) - (2 if prefix.rstrip().endswith("-") else 0)
    is_block_scalar = re.fullmatch(r"[|>][0-9]?[+-]?", value) is not None
    parts, consumed = _collect_folded_parts(lines, index, base_indent, is_block_scalar, value)
    if is_block_scalar:
        # The source exercise loader accepts one inline paragraph for these
        # fields. Fold both ``|`` and ``>`` blocks to one plain string while
        # preserving all authored words; this is transport normalization,
        # not a semantic rewrite.
        parts = [part for part in parts[1:] if part]
    folded = " ".join(parts)
    if not is_block_scalar and ": " not in folded:
        return None, consumed
    newline = match.group(4) or "\n"
    return f"{match.group(1)}{match.group(2)}: {json.dumps(folded, ensure_ascii=False)}{newline}", consumed


def _collect_folded_parts(
    lines: list[str],
    index: int,
    base_indent: int,
    is_block_scalar: bool,
    value: str,
) -> tuple[list[str], int]:
    """Collect one text scalar and its indented continuation lines."""
    parts = [value]
    consumed = 1
    while index + consumed < len(lines):
        continuation = lines[index + consumed]
        if not continuation.strip():
            if is_block_scalar:
                parts.append("")
                consumed += 1
                continue
            break
        indent = len(continuation) - len(continuation.lstrip(" "))
        if indent <= base_indent or (not is_block_scalar and continuation.lstrip().startswith("-")):
            break
        parts.append(continuation.strip())
        consumed += 1
    return parts, consumed


def _quote_identifier_scalars(text: str) -> str:
    """Quote identifier scalars so YAML 1.1 cannot coerce ``yes``/``no``."""
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.match(
            r"^(\s*(?:-\s+)?)([A-Za-z_][A-Za-z0-9_-]*):\s+([^#\n]+?)(\s*(?:#.*)?\r?\n)?$",
            line,
        )
        if match is None or match.group(2) not in K_SOURCE_REPAIR_IDENTIFIER_FIELDS:
            output.append(line)
            continue
        value = match.group(3).strip()
        if value[:1] in {"'", '"', "[", "{"}:
            output.append(line)
            continue
        if value.casefold() not in {"yes", "no", "true", "false", "null"} and not re.fullmatch(
            r"[-+]?\d+(?:\.\d+)?", value
        ):
            output.append(line)
            continue
        suffix = match.group(4) or "\n"
        output.append(f"{match.group(1)}{match.group(2)}: {json.dumps(value, ensure_ascii=False)}{suffix}")
    return "".join(output)


__all__ = [
    # Entry point for source repair callers.
    "repair_exercise_source",
    # Shared pure transport transformations imported by the workflow layer.
    "convert_legacy_recall_segment",
    "count_inline_blank_markers",
    "normalize_scalar_identifier_fields",
    "relocate_inline_blank_markers",
]
