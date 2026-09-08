"""Entry point: `load_exercises` loads authored exercises.

This is the published per-op YAML schema. One YAML document is a list of
exercise mappings. Each exercise has common wrapper fields plus op-specific
payload fields. Fields suffixed ``_md`` are parsed as pandoc-markdown inline
(spans allowed, via :func:`convert_inlines`); all other strings are plain
scalars.

Common wrapper fields (every op)::

    handle:         stable exercise key (required; also the default Exercise.id)
    id:             curated Exercise.id (optional; defaults to handle)
    op:             choose | recall_fill | match_pairs | judge | categorize | build | find_fix | speak | write
    objective:      objective id string
    bloom:          remember | understand | apply | analyze
    prompt_md:      prompt text (inline markdown → spans)
    explanation_md: optional explanation (inline markdown → spans; omit or null → none)
    derived_from:   optional list of {section_id, block_index?, note?}; default []

Per-op YAML shapes (``_md`` fields are span-parsed; all other strings are scalars):

choose::

    stem_md:    optional stem (inline markdown → spans)
    options:    list of {id, text, correct?: bool, why?: str}
    Exactly one option has correct: true → projects to answer_id. All option ids
    are preserved verbatim (never minted). Example::

        options:
          - id: a
            text: god
            correct: true
          - id: b
            text: godt

recall_fill::

    audio_target: exact Norwegian utterance used for model audio/transcript derivation
    segments: ordered list of either:
        span  → {text_md: inline-markdown}
        blank → {blank_id: str, options: [str] (≥2), answer_index: int}
    Discriminator: presence of ``blank_id``. Example::

        segments:
          - text_md: "et "
          - blank_id: blank_1
            options: [fin, fint, fine]
            answer_index: 1
          - text_md: " hus"

match_pairs::

    left:  list of {left_id, text}
    right: list of {right_id, text}
    pairs: list of {left_id, right_id}
    Links are SEPARATE from side order and MAY be many-to-one. Example::

        left:  [{left_id: l1, text: A}, {left_id: l2, text: B}]
        right: [{right_id: r1, text: X}]
        pairs: [{left_id: l1, right_id: r1}, {left_id: l2, right_id: r1}]

judge::

    sentence_md:  sentence text (inline markdown → spans)
    is_correct:   bool
    feedback:     str | null | absent → str-or-None. Distinguished at load time.

categorize::

    buckets: list of {bucket_id, label} (≥2)
    items:   list of {item_id, text, bucket_id}

build::

    tokens:       list of {token_id, text, fixed?: bool (default false)}
    answer_order: list of token_id — a permutation; MAY differ from display order.

find_fix::

    tokens:         list of {token_id, text}
    error_token_id: str (must match a token_id)
    feedback:       str (required)

speak::

    target:         exact Norwegian target utterance for any lesson kind

write::

    response_language: no (the learner response is Norwegian)
    min_words: optional positive integer
    max_words: optional positive integer
    judge_prompt: author-written, stateless rubric instruction for the downstream LLM
    criteria: list of {id: str, instruction: str}

Hard cases handled:
  - Non-canonical local ids (``a``, ``b``, ``blank_1``, ``common_sg``) are taken
    verbatim from YAML — never minted.
  - ``[BLANK]`` in ``_md`` fields resolves to the blank text marker (via pandoc).
  - ``choose`` ``correct: true`` projects to the curated answer id.
  - ``match_pairs`` links are independent of side order and may be many-to-one.
  - ``build`` display order + ``answer_order`` are carried separately, plus ``fixed``.
  - ``find_fix`` targets the error by ``token_id``, never by text.
  - ``judge`` ``feedback`` absent/null → ``None`` vs a string.
  - ``recall_fill`` keeps an explicit Norwegian ``audio_target`` as the
    model-audio/transcript source; context labels in ``segments`` are learner
    instructions and are never guessed into audio.
  - ``speak`` keeps the exact authored target as the model-audio/transcript
    source and may support any lesson kind; learner recording and speech-to-text
    output are outside this contract.
"""

from __future__ import annotations

from typing import Any
from typing import cast

import panflute
from pydantic import TypeAdapter

from lesson_builder.application.operations.convert_lesson_spans import K_SPANS_BLANK_MARKER
from lesson_builder.application.operations.convert_lesson_spans import convert_inlines
from lesson_builder.domain.lesson.models.elements import Exercise
from lesson_builder.domain.lesson.models.inline import InlineSpan
from lesson_builder.domain.lesson.models.inline import TextSpan
from lesson_builder.domain.lesson.models.operations import operation_names
from lesson_builder.domain.lesson.settings import K_EXERCISES_DEFAULT_LANG
from lesson_builder.formats.markdown.pandoc import parse_markdown
from lesson_builder.formats.yaml import load_unique_yaml

K_EXERCISES_SNIPPET_LIMIT = 80
K_EXERCISES_MIN_OPTIONS = 2

Spans = list[InlineSpan]

K_EXERCISES_ADAPTER: TypeAdapter[Exercise] = TypeAdapter(Exercise)


def load_exercises(
    yaml_text: str,
    warnings: list[str] | None = None,
    default_lang: str = K_EXERCISES_DEFAULT_LANG,
) -> list[Exercise]:
    """Load ``exercises.yaml`` text into a validated ``list[Exercise]``.

    Every authored handle is validated for the whole document before any
    exercise is projected or built, so a duplicate handle is reported even
    when an earlier item is malformed. Each YAML item is then projected from
    its human-friendly per-op shape to the internal Exercise wrapper + payload
    and validated by pydantic. Every curated local id is preserved verbatim;
    ``_md`` fields are span-parsed.
    """
    raw_items = load_unique_yaml(yaml_text)
    if raw_items is None:
        return []
    if not isinstance(raw_items, list):
        raise TypeError(f"exercises.yaml must be a YAML list of exercise mappings, got {type(raw_items).__name__}.")

    if warnings is None:
        warnings = []

    _validate_exercise_handles(raw_items)

    exercises: list[Exercise] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_items):
        exercise = _build_exercise(item, default_lang, warnings, index)
        exercise_id = cast(str, cast(Any, exercise).id)
        if exercise_id in seen_ids:
            raise ValueError(f"duplicate exercise id {exercise_id!r}")
        seen_ids.add(exercise_id)
        exercises.append(exercise)
    return exercises


def _validate_exercise_handles(raw_items: list[Any]) -> None:
    """Reject empty or duplicate handles for the whole document before projection."""
    seen_handles: set[str] = set()
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        raw_handle = item.get("handle")
        if not isinstance(raw_handle, str) or not raw_handle.strip():
            raise ValueError(f"exercise at index {index}: field 'handle' must be a non-empty string.")
        if raw_handle in seen_handles:
            raise ValueError(f"duplicate exercise handle {raw_handle!r}")
        seen_handles.add(raw_handle)


# ── Per-op payload builders ──────────────────────────────────────────────


def _build_exercise(
    raw: object,
    default_lang: str,
    warnings: list[str],
    index: int,
) -> Exercise:
    """Build one validated ``Exercise`` from a raw YAML mapping."""
    if not isinstance(raw, dict):
        raise TypeError(f"exercise at index {index}: expected a YAML mapping, got {type(raw).__name__}.")

    handle = _require_str(raw, "handle", index=index)
    if not handle.strip():
        raise ValueError(f"exercise at index {index}: field 'handle' must be a non-empty string.")
    op = _require_str(raw, "op", index=index)
    if op not in K_EXERCISES_OPS:
        raise ValueError(
            f"exercise {handle!r}: unknown op {op!r} (expected one of {', '.join(sorted(K_EXERCISES_OPS))})."
        )
    _reject_internal_fields(raw, handle)
    if op in {"speak", "write"}:
        _reject_runtime_fields(raw, handle, op)

    exercise_id = raw.get("id", handle)
    if not isinstance(exercise_id, str) or not exercise_id.strip():
        raise ValueError(f"exercise {handle!r}: field 'id' must be a non-empty string.")
    objective_id = _require_str(raw, "objective", handle=handle)
    bloom_level = _require_str(raw, "bloom", handle=handle)
    prompt = _parse_md(
        _require_str(raw, "prompt_md", handle=handle),
        default_lang,
        warnings,
    )
    explanation = _parse_optional_md(
        raw.get("explanation_md"),
        default_lang,
        warnings,
        handle=handle,
        field_name="explanation_md",
    )
    derived_from = _build_derived_from(raw.get("derived_from"), handle)

    payload = _build_payload(raw, op, default_lang, warnings, handle)

    exercise_dict: dict[str, Any] = {
        "element_kind": "exercise",
        "id": exercise_id,
        "operation": op,
        "objective_id": objective_id,
        "bloom_level": bloom_level,
        "prompt": prompt,
        "explanation": explanation,
        "derived_from": derived_from,
        "payload": payload,
    }
    return K_EXERCISES_ADAPTER.validate_python(exercise_dict)


def _build_payload(
    raw: dict[str, Any],
    op: str,
    default_lang: str,
    warnings: list[str],
    handle: str,
) -> dict[str, Any]:
    """Dispatch to the per-op payload builder."""
    if op == "choose":
        return _build_choose(raw, default_lang, warnings, handle)
    if op == "recall_fill":
        return _build_recall_fill(raw, default_lang, warnings, handle)
    if op == "match_pairs":
        return _build_match_pairs(raw, handle)
    if op == "judge":
        return _build_judge(raw, default_lang, warnings, handle)
    if op == "categorize":
        return _build_categorize(raw, handle)
    if op == "build":
        return _build_build(raw, handle)
    if op == "find_fix":
        return _build_find_fix(raw, handle)
    if op == "speak":
        return _build_speak(raw, handle)
    if op == "write":
        return _build_write(raw, handle)
    # Unreachable: op is validated in _build_exercise.
    raise ValueError(f"exercise {handle!r}: unknown op {op!r}")


def _build_choose(
    raw: dict[str, Any],
    default_lang: str,
    warnings: list[str],
    handle: str,
) -> dict[str, Any]:
    """Project ``options[*].correct: true`` → ``answer_id``; preserve every option id."""
    raw_options = _require_list(raw, "options", handle)
    answer_id: str | None = None
    seen_option_ids: set[str] = set()
    seen_option_texts: set[str] = set()
    options: list[dict[str, Any]] = []
    for opt_index, opt in enumerate(raw_options):
        if not isinstance(opt, dict):
            raise TypeError(f"exercise {handle!r}: option at index {opt_index} must be a mapping.")
        option_id = _opt_require_str(opt, "id", handle, opt_index)
        text = _opt_require_str(opt, "text", handle, opt_index)
        if option_id in seen_option_ids:
            raise ValueError(f"exercise {handle!r}: duplicate choose option id {option_id!r}.")
        normalized_text = _normalize_option_text(text)
        if normalized_text in seen_option_texts:
            raise ValueError(f"exercise {handle!r}: duplicate choose option text {text!r}.")
        seen_option_ids.add(option_id)
        seen_option_texts.add(normalized_text)
        why = opt.get("why")
        correct = opt.get("correct", False)
        if not isinstance(correct, bool):
            raise TypeError(f"exercise {handle!r}: option {opt_index} field 'correct' must be a boolean.")
        if correct:
            if answer_id is not None:
                raise ValueError(
                    f"exercise {handle!r}: multiple options have correct: true ({answer_id!r} and {option_id!r})."
                )
            answer_id = option_id
        options.append({"option_id": option_id, "text": text, "why": why})

    if answer_id is None:
        raise ValueError(f"exercise {handle!r}: no option has correct: true.")

    payload: dict[str, Any] = {"options": options, "answer_id": answer_id}
    stem_md = raw.get("stem_md")
    if stem_md is not None:
        payload["stem"] = _parse_md(
            _require_str(raw, "stem_md", handle=handle),
            default_lang,
            warnings,
        )
    return payload


def _build_recall_fill(
    raw: dict[str, Any],
    default_lang: str,
    warnings: list[str],
    handle: str,
) -> dict[str, Any]:
    """Build ordered segments, discriminating span vs blank by ``blank_id``."""
    audio_target = _require_str(raw, "audio_target", handle=handle)
    if not audio_target.strip():
        raise ValueError(f"exercise {handle!r}: audio_target must contain non-whitespace text")
    raw_segments = _require_list(raw, "segments", handle)
    _validate_recall_boundaries(raw_segments, handle)
    segments: list[dict[str, Any]] = []
    seen_blank_ids: set[str] = set()
    for seg_index, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            raise TypeError(f"exercise {handle!r}: segment at index {seg_index} must be a mapping.")
        segments.append(_build_recall_segment(seg, seg_index, seen_blank_ids, default_lang, warnings, handle))
    return {"audio_target": audio_target, "segments": segments}


def _build_recall_segment(
    segment: dict[str, Any],
    index: int,
    seen_blank_ids: set[str],
    default_lang: str,
    warnings: list[str],
    handle: str,
) -> dict[str, Any]:
    """Build one typed recall-fill span or blank segment."""
    if "blank_id" in segment:
        return _build_recall_blank(segment, index, seen_blank_ids, handle)
    text_md = _seg_require_str(segment, "text_md", handle, index)
    if K_SPANS_BLANK_MARKER in text_md:
        raise ValueError(
            f"exercise {handle!r}: recall span contains the inline blank "
            f"marker {K_SPANS_BLANK_MARKER!r}; use a typed blank segment "
            "at that position"
        )
    return {"kind": "span", "spans": _parse_recall_span(text_md, default_lang, warnings)}


def _build_recall_blank(segment: dict[str, Any], index: int, seen_blank_ids: set[str], handle: str) -> dict[str, Any]:
    """Validate and build one recall-fill blank segment."""
    blank_id = _seg_require_str(segment, "blank_id", handle, index)
    if blank_id in seen_blank_ids:
        raise ValueError(f"exercise {handle!r}: duplicate recall blank id {blank_id!r}.")
    seen_blank_ids.add(blank_id)
    options = segment.get("options")
    if not isinstance(options, list) or len(options) < K_EXERCISES_MIN_OPTIONS:
        raise ValueError(
            f"exercise {handle!r}: blank segment {blank_id!r} at index {index} needs options (list of ≥2 strings)."
        )
    if any(not isinstance(option, str) or not option.strip() for option in options):
        raise ValueError(f"exercise {handle!r}: blank segment {blank_id!r} options must be non-empty strings.")
    normalized_options = [_normalize_option_text(option) for option in options]
    if len(set(normalized_options)) != len(normalized_options):
        raise ValueError(f"exercise {handle!r}: blank segment {blank_id!r} has duplicate options.")
    answer_index = segment.get("answer_index")
    if not isinstance(answer_index, int) or isinstance(answer_index, bool):
        raise TypeError(
            f"exercise {handle!r}: blank segment {blank_id!r} at index {index} needs an integer answer_index."
        )
    if not 0 <= answer_index < len(options):
        raise ValueError(f"exercise {handle!r}: blank segment {blank_id!r} answer_index is outside the options list.")
    return {
        "kind": "blank",
        "blank_id": blank_id,
        "options": list(options),
        "answer_index": answer_index,
    }


def _validate_recall_boundaries(raw_segments: list[Any], handle: str) -> None:
    """Reject word joins caused by YAML trimming an unquoted boundary space."""
    for index, (left, right) in enumerate(zip(raw_segments, raw_segments[1:], strict=False)):
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        if "blank_id" in left:
            left_options = left.get("options")
            right_text = right.get("text_md")
            if (
                isinstance(left_options, list)
                and isinstance(right_text, str)
                and any(
                    isinstance(option, str)
                    and option
                    and option[-1].isalnum()
                    and right_text
                    and right_text[0].isalnum()
                    for option in left_options
                )
            ):
                _raise_recall_boundary_error(handle, index)
        elif "blank_id" in right:
            left_text = left.get("text_md")
            right_options = right.get("options")
            if (
                isinstance(left_text, str)
                and isinstance(right_options, list)
                and any(
                    isinstance(option, str) and option and option[0].isalnum() and left_text and left_text[-1].isalnum()
                    for option in right_options
                )
            ):
                _raise_recall_boundary_error(handle, index)


def _raise_recall_boundary_error(handle: str, index: int) -> None:
    """Raise the actionable error shared by both recall boundary directions."""
    raise ValueError(
        f"exercise {handle!r}: recall_fill span/blank boundary at segment "
        f"{index} joins alphanumeric text without whitespace; quote the span "
        "with an explicit leading or trailing space"
    )


def _build_match_pairs(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build independent left/right/pays lists; links are separate from side order."""
    raw_left = _require_list(raw, "left", handle)
    raw_right = _require_list(raw, "right", handle)
    raw_pairs = _require_list(raw, "pairs", handle)
    left = [
        {
            "left_id": _sub_require_str(item, "left_id", handle, "left", i),
            "text": _sub_require_str(item, "text", handle, "left", i),
        }
        for i, item in enumerate(raw_left)
    ]
    right = [
        {
            "right_id": _sub_require_str(item, "right_id", handle, "right", i),
            "text": _sub_require_str(item, "text", handle, "right", i),
        }
        for i, item in enumerate(raw_right)
    ]
    pairs = [
        {
            "left_id": _sub_require_str(item, "left_id", handle, "pairs", i),
            "right_id": _sub_require_str(item, "right_id", handle, "pairs", i),
        }
        for i, item in enumerate(raw_pairs)
    ]
    _require_unique_ids(left, "left_id", handle, "left item")
    _require_unique_ids(right, "right_id", handle, "right item")
    _require_unique_texts(left, "text", handle, "left item")
    _require_unique_texts(right, "text", handle, "right item")
    pair_ids = [(pair["left_id"], pair["right_id"]) for pair in pairs]
    if len(set(pair_ids)) != len(pair_ids):
        raise ValueError(f"exercise {handle!r}: duplicate match pair.")
    return {"left": left, "right": right, "pairs": pairs}


def _build_judge(
    raw: dict[str, Any],
    default_lang: str,
    warnings: list[str],
    handle: str,
) -> dict[str, Any]:
    """Build judge payload; feedback absent/null → None, string → string."""
    sentence_md = _require_str(raw, "sentence_md", handle=handle)
    is_correct = raw.get("is_correct")
    if not isinstance(is_correct, bool):
        raise TypeError(f"exercise {handle!r}: 'is_correct' must be a boolean.")
    feedback = raw.get("feedback")
    if feedback is not None and not isinstance(feedback, str):
        raise ValueError(f"exercise {handle!r}: 'feedback' must be a string or null.")
    return {
        "sentence": _parse_md(sentence_md, default_lang, warnings),
        "is_correct": is_correct,
        "feedback": feedback,
    }


def _build_categorize(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build buckets + items, preserving all ids verbatim."""
    raw_buckets = _require_list(raw, "buckets", handle)
    raw_items = _require_list(raw, "items", handle)
    buckets = [
        {
            "bucket_id": _sub_require_str(b, "bucket_id", handle, "buckets", i),
            "label": _sub_require_str(b, "label", handle, "buckets", i),
        }
        for i, b in enumerate(raw_buckets)
    ]
    items = [
        {
            "item_id": _sub_require_str(item, "item_id", handle, "items", i),
            "text": _sub_require_str(item, "text", handle, "items", i),
            "bucket_id": _sub_require_str(item, "bucket_id", handle, "items", i),
        }
        for i, item in enumerate(raw_items)
    ]
    _require_unique_ids(buckets, "bucket_id", handle, "category bucket")
    _require_unique_ids(items, "item_id", handle, "category item")
    _require_unique_texts(buckets, "label", handle, "category bucket")
    _require_unique_texts(items, "text", handle, "category item")
    return {"buckets": buckets, "items": items}


def _build_build(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build tokens (display order + fixed flag) + answer_order separately."""
    raw_tokens = _require_list(raw, "tokens", handle)
    tokens: list[dict[str, Any]] = []
    for index, token in enumerate(raw_tokens):
        token_id = _sub_require_str(token, "token_id", handle, "tokens", index)
        text = _sub_require_str(token, "text", handle, "tokens", index)
        fixed = token.get("fixed", False) if isinstance(token, dict) else False
        if not isinstance(fixed, bool):
            raise TypeError(f"exercise {handle!r}: tokens entry {index} field 'fixed' must be a boolean.")
        tokens.append({"token_id": token_id, "text": text, "fixed": fixed})
    _require_unique_ids(tokens, "token_id", handle, "build token")
    answer_order = _require_list(raw, "answer_order", handle)
    if any(not isinstance(token_id, str) for token_id in answer_order):
        raise ValueError(f"exercise {handle!r}: answer_order must contain string token IDs.")
    return {"tokens": tokens, "answer_order": list(answer_order)}


def _build_find_fix(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build find_fix payload; error targeted by token_id, never by text."""
    raw_tokens = _require_list(raw, "tokens", handle)
    tokens = [
        {
            "token_id": _sub_require_str(t, "token_id", handle, "tokens", i),
            "text": _sub_require_str(t, "text", handle, "tokens", i),
        }
        for i, t in enumerate(raw_tokens)
    ]
    _require_unique_ids(tokens, "token_id", handle, "find_fix token")
    error_token_id = _require_str(raw, "error_token_id", handle=handle)
    feedback = _require_str(raw, "feedback", handle=handle)
    return {
        "tokens": tokens,
        "error_token_id": error_token_id,
        "feedback": feedback,
    }


def _build_speak(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build a target-bearing speak payload without runtime audio controls."""
    target = _require_str(raw, "target", handle=handle).strip()
    if not target:
        raise ValueError(f"exercise {handle!r}: field 'target' must contain non-whitespace text")
    return {"target": target}


def _build_write(raw: dict[str, Any], handle: str) -> dict[str, Any]:
    """Build the app-facing writing task and its immutable judge rubric."""
    response_language = raw.get("response_language", "no")
    if response_language != "no":
        raise ValueError(f"exercise {handle!r}: write response_language must be 'no'")
    judge_prompt = _require_str(raw, "judge_prompt", handle=handle).strip()
    if not judge_prompt:
        raise ValueError(f"exercise {handle!r}: judge_prompt must not be blank")
    criteria = _build_write_criteria(raw, handle)
    payload: dict[str, Any] = {
        "response_language": "no",
        "judge_prompt": judge_prompt,
        "criteria": criteria,
    }
    payload.update(_write_word_bounds(raw, handle))
    return payload


def _build_write_criteria(raw: dict[str, Any], handle: str) -> list[dict[str, str]]:
    """Validate and normalize the immutable writing rubric criteria."""
    criteria: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, criterion in enumerate(_require_list(raw, "criteria", handle)):
        if not isinstance(criterion, dict):
            raise TypeError(f"exercise {handle!r}: criteria entry {index} must be a mapping")
        criterion_id = _sub_require_str(criterion, "id", handle, "criteria", index)
        instruction = _sub_require_str(criterion, "instruction", handle, "criteria", index).strip()
        if criterion_id in seen_ids:
            raise ValueError(f"exercise {handle!r}: criteria ids must be unique: {criterion_id!r}")
        if not instruction:
            raise ValueError(f"exercise {handle!r}: criteria instruction {criterion_id!r} must not be blank")
        seen_ids.add(criterion_id)
        criteria.append({"id": criterion_id, "instruction": instruction})
    return criteria


def _write_word_bounds(raw: dict[str, Any], handle: str) -> dict[str, int]:
    """Validate optional positive word bounds and their ordering."""
    bounds: dict[str, int] = {}
    for key in ("min_words", "max_words"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"exercise {handle!r}: {key} must be a positive integer")
        bounds[key] = value
    if (
        bounds.get("min_words") is not None
        and bounds.get("max_words") is not None
        and bounds["min_words"] > bounds["max_words"]
    ):
        raise ValueError(f"exercise {handle!r}: min_words must not exceed max_words")
    return bounds


# ── Inline markdown parsing ──────────────────────────────────────────────


def _parse_md(text: str, default_lang: str, warnings: list[str]) -> Spans:
    """Parse an inline-markdown string into spans via pandoc + ``convert_inlines``.

    Adjacent-text merge and NFC normalization come from ``convert_inlines``.
    ``[BLANK]`` resolves to the blank text marker. Raises if the text produces
    zero or more than one block (inline fields must be single-paragraph).
    """
    if not text.strip():
        return []
    doc = parse_markdown(text)
    blocks = list(doc.content)
    if not blocks:
        return []
    if len(blocks) > 1 or not isinstance(blocks[0], (panflute.Para, panflute.Plain)):
        snippet = text[:K_EXERCISES_SNIPPET_LIMIT]
        raise ValueError(
            f"_md field must be single-paragraph inline text, got {len(blocks)} block(s). Snippet: {snippet}"
        )
    return convert_inlines(blocks[0].content, warnings, default_lang)


def _parse_recall_span(text: str, default_lang: str, warnings: list[str]) -> Spans:
    """Parse a recall span while preserving quoted boundary whitespace."""
    spans = _parse_md(text, default_lang, warnings)
    if text[:1] == " ":
        spans.insert(0, TextSpan(kind="text", value=" "))
    if text[-1:] == " ":
        spans.append(TextSpan(kind="text", value=" "))
    return spans


def _parse_optional_md(
    text: object,
    default_lang: str,
    warnings: list[str],
    *,
    handle: str,
    field_name: str,
) -> Spans | None:
    """Parse an optional ``_md`` field; returns ``None`` for absent/null/empty."""
    if text is None:
        return None
    if not isinstance(text, str):
        raise TypeError(
            f"exercise {handle!r}: field {field_name!r} must be a string or null, got {type(text).__name__}."
        )
    if not text.strip():
        return None
    return _parse_md(text, default_lang, warnings)


def _require_unique_ids(items: list[dict[str, Any]], key: str, handle: str, label: str) -> None:
    """Reject duplicate local identifiers before the typed schema can hide them."""
    values = [item[key] for item in items]
    if len(set(values)) != len(values):
        raise ValueError(f"exercise {handle!r}: duplicate {label} {key} values.")


def _require_unique_texts(items: list[dict[str, Any]], key: str, handle: str, label: str) -> None:
    """Reject duplicate visible choices that would make a closed task ambiguous."""
    values = [item[key] for item in items]
    normalized = [_normalize_option_text(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"exercise {handle!r}: duplicate {label} visible text.")


def _normalize_option_text(value: str) -> str:
    """Normalize only whitespace and case for duplicate-answer detection."""
    return " ".join(value.split()).casefold()


# ── derived_from ─────────────────────────────────────────────────────────


def _build_derived_from(raw: object, handle: str) -> list[dict[str, Any]]:
    """Build the ``derived_from`` provenance list from YAML (default empty)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"exercise {handle!r}: 'derived_from' must be a list or null.")
    result: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"exercise {handle!r}: derived_from entry {i} must be a mapping.")
        section_id = item.get("section_id")
        if not isinstance(section_id, str):
            raise TypeError(f"exercise {handle!r}: derived_from entry {i} needs a string section_id.")
        entry: dict[str, Any] = {"section_id": section_id}
        if "block_index" in item:
            entry["block_index"] = item["block_index"]
        if "note" in item:
            entry["note"] = item["note"]
        result.append(entry)
    return result


# ── Field helpers ────────────────────────────────────────────────────────

K_EXERCISES_OPS: frozenset[str] = frozenset(operation_names())
K_EXERCISES_INTERNAL_FIELDS: frozenset[str] = frozenset({"stage", "build_stage"})
K_SPEAK_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "handle",
        "id",
        "op",
        "objective",
        "bloom",
        "prompt_md",
        "explanation_md",
        "derived_from",
        "target",
    }
)
K_WRITE_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "handle",
        "id",
        "op",
        "objective",
        "bloom",
        "prompt_md",
        "explanation_md",
        "derived_from",
        "response_language",
        "min_words",
        "max_words",
        "judge_prompt",
        "criteria",
    }
)


def _reject_runtime_fields(raw: dict[str, Any], handle: str, operation: str) -> None:
    """Reject runtime-oriented fields from authored speak/write envelopes."""
    allowed = K_SPEAK_ALLOWED_FIELDS if operation == "speak" else K_WRITE_ALLOWED_FIELDS
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"exercise {handle!r}: {operation} does not support field(s): {', '.join(unknown)}")


def _reject_internal_fields(raw: dict[str, Any], handle: str) -> None:
    """Reject factory diagnostics that are not part of authored exercise YAML."""
    leaked = sorted(set(raw) & K_EXERCISES_INTERNAL_FIELDS)
    if leaked:
        raise ValueError(
            f"exercise {handle!r}: internal field(s) are not supported in exercises.yaml: " + ", ".join(leaked)
        )


def _require_str(
    raw: dict[str, Any],
    key: str,
    *,
    handle: str | None = None,
    index: int | None = None,
) -> str:
    """Require a string field from a YAML mapping with a contextual error."""
    value = raw.get(key)
    if not isinstance(value, str):
        if handle is not None:
            ctx = f"exercise {handle!r}"
        elif index is not None:
            ctx = f"exercise at index {index}"
        else:
            ctx = "exercise"
        raise TypeError(f"{ctx}: field {key!r} must be a string, got {type(value).__name__}.")
    return value


def _require_list(raw: dict[str, Any], key: str, handle: str) -> list[Any]:
    """Require a list field from a YAML mapping with a contextual error."""
    value = raw.get(key)
    if not isinstance(value, list):
        raise TypeError(f"exercise {handle!r}: field {key!r} must be a list, got {type(value).__name__}.")
    return value


def _opt_require_str(opt: dict[str, Any], key: str, handle: str, opt_index: int) -> str:
    """Require a string from a choose-option mapping."""
    value = opt.get(key)
    if not isinstance(value, str):
        raise TypeError(f"exercise {handle!r}: option {opt_index} field {key!r} must be a string.")
    return value


def _seg_require_str(seg: dict[str, Any], key: str, handle: str, seg_index: int) -> str:
    """Require a string from a recall_fill-segment mapping."""
    value = seg.get(key)
    if not isinstance(value, str):
        raise TypeError(f"exercise {handle!r}: segment {seg_index} field {key!r} must be a string.")
    return value


def _sub_require_str(
    item: object,
    key: str,
    handle: str,
    list_name: str,
    index: int,
) -> str:
    """Require a string from a sub-item in a named list (left/right/pairs/etc.)."""
    if not isinstance(item, dict):
        raise TypeError(f"exercise {handle!r}: {list_name} entry {index} must be a mapping.")
    value = item.get(key)
    if not isinstance(value, str):
        raise TypeError(f"exercise {handle!r}: {list_name} entry {index} field {key!r} must be a string.")
    return value
