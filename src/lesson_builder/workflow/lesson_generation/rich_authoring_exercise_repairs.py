"""Entry points: `repair_build_item` and `repair_exercise_item` normalize model-produced exercise transport.

These helpers normalize legacy model aliases and enforce the public exercise
envelope without invoking a provider or writing artifacts. Scalar identifier,
recall segment alias, and inline blank marker transformations are shared with
the application source-repair operation through
:mod:`lesson_builder.application.operations.repair_source`. The rich-authoring
workflow content module calls these entry points while normalizing generated
exercise source.
"""

from __future__ import annotations

import re
from typing import Any

from lesson_builder.application.operations.repair_source import convert_legacy_recall_segment
from lesson_builder.application.operations.repair_source import count_inline_blank_markers
from lesson_builder.application.operations.repair_source import normalize_scalar_identifier_fields
from lesson_builder.application.operations.repair_source import relocate_inline_blank_markers

K_RICH_RECALL_MIN_OPTIONS = 2


def repair_build_item(item: dict[str, Any]) -> None:
    """Apply only build-operation repairs used by transport normalization."""
    normalize_scalar_identifier_fields(item)
    _repair_build_fields(item)


def repair_exercise_item(item: dict[str, Any]) -> None:
    """Apply all deterministic operation-specific exercise repairs."""
    normalize_scalar_identifier_fields(item)
    _repair_build_fields(item)
    _repair_categorize_fields(item)
    _repair_recall_fill_aliases(item)
    _repair_recall_fill_fields(item)
    _repair_recall_fill_markers(item)
    _repair_write_fields(item)
    _repair_exercise_prompt(item)


def _repair_build_fields(item: dict[str, Any]) -> None:
    """Drop unsupported build distractors when the answer order is unambiguous."""
    if item.get("op") != "build":
        return
    tokens = item.get("tokens")
    answer_order = item.get("answer_order")
    if not isinstance(tokens, list) or not isinstance(answer_order, list):
        return
    token_ids = [
        token.get("token_id") for token in tokens if isinstance(token, dict) and isinstance(token.get("token_id"), str)
    ]
    if len(token_ids) != len(tokens) or len(set(token_ids)) != len(token_ids):
        return
    if not all(isinstance(token_id, str) for token_id in answer_order):
        return
    if len(set(answer_order)) != len(answer_order):
        return
    answer_ids = set(answer_order)
    token_id_set = set(token_ids)
    if not answer_ids or not answer_ids.issubset(token_id_set) or answer_ids == token_id_set:
        return
    # BuildPayload has no distractor representation. When the model supplied a
    # valid subset as the answer permutation, retain only those intended answer
    # tokens instead of allowing schema validation to fail on extras.
    item["tokens"] = [token for token in tokens if token.get("token_id") in answer_ids]


def _repair_categorize_fields(item: dict[str, Any]) -> None:
    """Convert the common ``categories`` alias to the public bucket shape."""
    if item.get("op") != "categorize":
        return
    categories = item.get("categories")
    if "buckets" not in item and isinstance(categories, list):
        buckets = _repair_category_aliases(categories)
        if buckets:
            item["buckets"] = buckets

    raw_buckets = item.get("buckets")
    raw_entries = item.get("items")
    if not isinstance(raw_buckets, list) or not isinstance(raw_entries, list):
        return
    aliases = _categorize_bucket_aliases(raw_buckets)
    _repair_category_entries(raw_entries, aliases)
    item.pop("categories", None)


def _repair_category_aliases(categories: list[Any]) -> list[dict[str, str]]:
    """Convert legacy category labels or mappings into public buckets."""
    buckets: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for index, category in enumerate(categories):
        if isinstance(category, str):
            label = category.strip()
            raw_id = label
        elif isinstance(category, dict):
            label_value = category.get("label", category.get("name"))
            if not isinstance(label_value, str) or not label_value.strip():
                continue
            label = label_value.strip()
            candidate_id = category.get("bucket_id", category.get("id"))
            raw_id = candidate_id if isinstance(candidate_id, str) else label
        else:
            continue
        buckets.append({"bucket_id": _categorize_bucket_id(raw_id, index, used_ids), "label": label})
    return buckets


def _categorize_bucket_aliases(raw_buckets: list[Any]) -> dict[str, str]:
    """Map legacy bucket IDs and labels to canonical bucket IDs."""
    aliases: dict[str, str] = {}
    for bucket in raw_buckets:
        if not isinstance(bucket, dict):
            continue
        raw_bucket_id = bucket.get("bucket_id")
        raw_label = bucket.get("label")
        if isinstance(raw_bucket_id, str):
            aliases[raw_bucket_id] = raw_bucket_id
            if isinstance(raw_label, str):
                aliases[_categorize_alias(raw_label)] = raw_bucket_id
    return aliases


def _repair_category_entries(raw_entries: list[Any], aliases: dict[str, str]) -> None:
    """Normalize category item IDs and resolve legacy category labels."""
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        if "item_id" not in entry and isinstance(entry.get("id"), str):
            entry["item_id"] = entry.pop("id")
        if "bucket_id" in entry:
            continue
        category = entry.pop("category", None)
        if isinstance(category, str):
            resolved_bucket_id = aliases.get(category, aliases.get(_categorize_alias(category)))
            if resolved_bucket_id is not None:
                entry["bucket_id"] = resolved_bucket_id


def _categorize_bucket_id(value: str, index: int, used_ids: set[str]) -> str:
    """Create a stable schema identifier from a model-emitted category label."""
    candidate = _categorize_alias(value) or f"bucket-{index + 1}"
    base = candidate
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _categorize_alias(value: str) -> str:
    """Normalize a category label for matching without changing its display text."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _repair_write_fields(item: dict[str, Any]) -> None:
    """Keep a write task's example while removing unsupported model fields.

    This is deterministic transport repair only. A missing ``judge_prompt``
    or missing substantive ``criteria`` stay invalid so the audit boundary
    blocks the package and the workflow requests a targeted repair instead
    of accepting invented grading requirements.
    """
    if item.get("op") != "write":
        return
    _repair_write_word_limits(item)
    # The public operation uses the ISO-639-1 Norwegian code ``no``. Models
    # often emit the product's ``nb`` default instead; normalize that alias
    # before the typed operation policy validates the exercise.
    item["response_language"] = "no"
    _repair_write_criteria(item)
    notes = _extract_write_notes(item)
    if not notes:
        return
    existing = item.get("explanation_md")
    prefix = (
        f"{re.sub(r'(?:\\\\n|\\s)+', ' ', existing.strip())} " if isinstance(existing, str) and existing.strip() else ""
    )
    item["explanation_md"] = prefix + " ".join(notes)


def _repair_write_word_limits(item: dict[str, Any]) -> None:
    """Normalize legacy write word-limit aliases."""
    for alias, canonical in (("word_min", "min_words"), ("word_max", "max_words")):
        alias_value = item.pop(alias, None)
        if canonical not in item and isinstance(alias_value, int) and not isinstance(alias_value, bool):
            item[canonical] = alias_value


def _repair_write_criteria(item: dict[str, Any]) -> None:
    """Restore the public write criterion mapping shape.

    Explicitly supplied criterion strings become criterion mappings. Missing
    or empty criteria are left untouched; the loader and source audit reject
    them as blocking findings.
    """
    criteria = item.get("criteria")
    if isinstance(criteria, list) and any(isinstance(criterion, str) for criterion in criteria):
        item["criteria"] = [
            criterion if isinstance(criterion, dict) else {"id": f"criterion_{index + 1}", "instruction": criterion}
            for index, criterion in enumerate(criteria)
        ]


def _extract_write_notes(item: dict[str, Any]) -> list[str]:
    """Move optional sample and feedback text into explanation notes."""
    notes: list[str] = []
    for field, label in (("sample_answer_md", "Example answer"), ("feedback", "Feedback")):
        value = item.pop(field, None)
        if isinstance(value, str) and value.strip():
            inline_value = re.sub(r"(?:\\n|\s)+", " ", value.strip())
            notes.append(f"{label}: {inline_value}")
    return notes


def _repair_recall_fill_aliases(item: dict[str, Any]) -> None:
    """Convert legacy typed recall segments into the public segment shape."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    repaired_segments: list[Any] = []
    changed = False
    for index, segment in enumerate(item["segments"]):
        segment, segment_changed = convert_legacy_recall_segment(
            segment, blank_id=f"{item.get('handle', 'recall')}_blank_{index}"
        )
        changed = changed or segment_changed
        repaired_segments.append(segment)
    if changed:
        item["segments"] = repaired_segments


def _repair_recall_fill_fields(item: dict[str, Any]) -> None:
    """Move one model-flattened recall blank into the typed segment shape."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    raw_options = item.get("options")
    answer_index = item.get("answer_index")
    options = _extract_flattened_options(raw_options, answer_index)
    repaired = 0
    segments = item["segments"]
    for index, segment in enumerate(segments):
        repaired += _repair_recall_segment_fields(segments, index, segment, options, answer_index)
    if repaired and options is not None:
        item.pop("options", None)
        item.pop("answer_index", None)


def _extract_flattened_options(raw_options: object, answer_index: object) -> list[str] | None:
    """Extract a valid option list from a flattened recall blank."""
    if not isinstance(raw_options, list) or not isinstance(answer_index, int):
        return None
    options = [
        option["text"] for option in raw_options if isinstance(option, dict) and isinstance(option.get("text"), str)
    ]
    return options if len(options) >= K_RICH_RECALL_MIN_OPTIONS else None


def _repair_recall_segment_fields(
    segments: list[Any],
    index: int,
    segment: object,
    options: list[str] | None,
    answer_index: object,
) -> int:
    """Apply flattened options and spacing repairs to one typed blank."""
    repaired = 0
    if options is not None and isinstance(segment, dict) and "blank_id" in segment and "options" not in segment:
        segment["options"] = options
        segment["answer_index"] = answer_index
        repaired += 1
    if not isinstance(segment, dict) or "blank_id" not in segment:
        return repaired
    if index > 0:
        repaired += _add_recall_boundary_space(segments[index - 1], trailing=True)
    if index + 1 < len(segments):
        repaired += _add_recall_boundary_space(segments[index + 1], trailing=False)
    return repaired


def _add_recall_boundary_space(segment: object, *, trailing: bool) -> int:
    """Add a missing space before or after a typed recall blank."""
    if not isinstance(segment, dict) or not isinstance(segment.get("text_md"), str) or not segment["text_md"]:
        return 0
    text = segment["text_md"]
    if trailing:
        if text[-1].isspace():
            return 0
        segment["text_md"] = text + " "
        return 1
    if text[0].isspace():
        return 0
    segment["text_md"] = " " + text
    return 1


def _repair_recall_fill_markers(item: dict[str, Any]) -> None:
    """Place legacy inline blank markers at their typed segment positions."""
    if item.get("op") != "recall_fill" or not isinstance(item.get("segments"), list):
        return
    segments = item["segments"]
    marker_count = count_inline_blank_markers(segments)
    if marker_count == 0:
        return
    blank_count = sum(1 for segment in segments if isinstance(segment, dict) and "blank_id" in segment)
    if marker_count != blank_count:
        raise ValueError(
            f"exercise {item.get('handle', 'recall_fill')!r}: found "
            f"{marker_count} inline blank markers but {blank_count} blank segments"
        )
    item["segments"] = relocate_inline_blank_markers(segments)


# complexipy: ignore -- legacy complexity retained during a behavior-preserving move.
def _repair_exercise_prompt(item: dict[str, Any]) -> None:
    """Restore mechanical answer slots that a conversion model collapsed."""
    prompt = item.get("prompt_md")
    if not isinstance(prompt, str) or item.get("op") != "choose":
        return
    repaired = prompt
    if re.search(r"(?i)\b(?:complete|choose)\b", repaired):
        repaired = re.sub(r"(?<=\S) {2,}(?=\S)", " _____ ", repaired, count=1)
        if repaired.rstrip().endswith(" ?") and "_____" not in repaired:
            repaired = repaired.rstrip()[:-2] + " _____?"
    malformed = re.search(
        r"After `(?P<finite>[^`,]+),\s*use the\s+[^`]*\s+without\s+å`[.]?",
        repaired,
        flags=re.IGNORECASE,
    )
    if malformed:
        correct_options = [
            option for option in item.get("options", []) if isinstance(option, dict) and option.get("correct") is True
        ]
        if correct_options:
            option_text = str(correct_options[0].get("text", ""))
            label = re.sub(r"`", "", option_text).split("—", 1)[0].strip()
            if label:
                repaired = f"After `{malformed.group('finite').strip()}`, use the `{label}` without `å`."
    item["prompt_md"] = repaired


__all__ = [
    "repair_build_item",
    "repair_exercise_item",
]
