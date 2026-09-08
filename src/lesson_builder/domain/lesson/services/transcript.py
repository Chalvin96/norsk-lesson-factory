"""Entry point: `derive_transcript_blocks` derives Norwegian audio candidates.

Pure projection over an already-compiled internal lesson document: it collects
deterministic model-audio transcript blocks from visible section content and
target exercises without touching the filesystem. The lesson-generation
workflow and the distribution exporter both call this module so projection and
synthesis agree on one visibility decision.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from lesson_builder.domain.lesson.settings import K_TRANSCRIPT_AUDIO_EXERCISE_OPS
from lesson_builder.domain.lesson.settings import K_TRANSCRIPT_AUDIO_LANGUAGE
from lesson_builder.domain.lesson.settings import K_TRANSCRIPT_NON_NORWEGIAN_AUDIO_WORDS
from lesson_builder.domain.lesson.validation.learner_visibility import is_internal_request_section_title
from lesson_builder.domain.lesson.validation.learner_visibility import visible_block_indexes

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def derive_transcript_blocks(internal_lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect deterministic audio candidates from sections and target exercises.

    Sections whose title is an internal request anchor, and authored blocks
    hidden by the shared learner-visibility boundary, never become audio
    candidates: projection and synthesis must agree on visibility.
    """
    blocks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for element in internal_lesson.get("elements", []):
        if not isinstance(element, dict):
            continue
        element_kind = element.get("element_kind")
        if element_kind == "section":
            if is_internal_request_section_title(element.get("title")):
                continue
            section_id = _slug_component(element.get("id", "section"))
            _derive_section_transcript_blocks(
                authored_blocks=element.get("blocks", []),
                section_id=str(element.get("id", "section")),
                section_slug=section_id,
                path_prefix=[],
                blocks=blocks,
                seen_ids=seen_ids,
            )
        elif element_kind == "exercise" and element.get("operation") in K_TRANSCRIPT_AUDIO_EXERCISE_OPS:
            text = _exercise_audio_text(element)
            if text:
                _append_transcript_block(
                    blocks,
                    seen_ids,
                    _candidate_id(
                        f"t-exercise-{_slug_component(element.get('id', 'exercise'))}-target",
                        text,
                        seen_ids,
                    ),
                    text,
                    origin="exercise_target",
                    source={"exercise_id": str(element.get("id", "exercise"))},
                )
    if not blocks:
        raise ValueError("lesson package must contain at least one audio candidate")
    return blocks


def _derive_section_transcript_blocks(
    *,
    authored_blocks: object,
    section_id: str,
    section_slug: str,
    path_prefix: list[int],
    blocks: list[dict[str, Any]],
    seen_ids: set[str],
) -> None:
    """Recurse through visible section blocks while preserving source locators.

    The shared learner-visibility boundary decides which authored blocks can
    become audio candidates; original block indexes are kept so binding
    locators match the projection exactly.
    """
    if not isinstance(authored_blocks, list):
        return
    for block_index in visible_block_indexes(authored_blocks):
        block = authored_blocks[block_index]
        if not isinstance(block, dict):
            continue
        block_path = path_prefix + [block_index]
        source = _source_for_block(section_id, block_path, block_index)
        kind = block.get("kind")
        if kind == "examples":
            _derive_examples_block(block, section_slug, source, blocks, seen_ids)
        elif kind == "example":
            _derive_example_block(block, section_slug, source, blocks, seen_ids)
        elif kind == "reading":
            _derive_reading_block(block, section_slug, source, blocks, seen_ids)
        if kind == "callout":
            _derive_section_transcript_blocks(
                authored_blocks=block.get("blocks", []),
                section_id=section_id,
                section_slug=section_slug,
                path_prefix=block_path,
                blocks=blocks,
                seen_ids=seen_ids,
            )


def _source_for_block(section_id: str, block_path: list[int], block_index: int) -> dict[str, Any]:
    """Build the authored source locator used by audio bindings."""
    source: dict[str, Any] = {"section_id": section_id}
    if len(block_path) == 1:
        source["block_index"] = block_index
    else:
        source["block_path"] = block_path
    return source


def _derive_examples_block(
    block: dict[str, Any],
    section_slug: str,
    source: dict[str, Any],
    blocks: list[dict[str, Any]],
    seen_ids: set[str],
) -> None:
    """Derive candidates from a multi-example teaching block."""
    for item_index, item in enumerate(block.get("items", [])):
        if not isinstance(item, dict):
            continue
        text = _flatten_spans(item.get("no"), norwegian_only=True)
        if not text or not _is_audio_safe_example(item):
            continue
        item_source = {**source, "item_index": item_index}
        _append_transcript_block(
            blocks,
            seen_ids,
            _candidate_id(f"t-lesson-{section_slug}-example", text, seen_ids),
            text,
            origin="lesson_example",
            source=item_source,
        )


def _derive_example_block(
    block: dict[str, Any],
    section_slug: str,
    source: dict[str, Any],
    blocks: list[dict[str, Any]],
    seen_ids: set[str],
) -> None:
    """Derive a candidate from a single teaching example."""
    text = _flatten_spans(block.get("no"), norwegian_only=True)
    if text and _is_audio_safe_example(block):
        _append_transcript_block(
            blocks,
            seen_ids,
            _candidate_id(f"t-lesson-{section_slug}-example-single", text, seen_ids),
            text,
            origin="lesson_example",
            source=source,
        )


def _derive_reading_block(
    block: dict[str, Any],
    section_slug: str,
    source: dict[str, Any],
    blocks: list[dict[str, Any]],
    seen_ids: set[str],
) -> None:
    """Derive a candidate from one reading turn and retain speaker metadata."""
    text = _flatten_spans(block.get("spans"), norwegian_only=True)
    if not text:
        return
    reading_source = dict(source)
    for key in ("dialogue_id", "speaker_id", "speaker_name", "character_id", "voice_profile"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            reading_source[key] = value.strip()
    _append_transcript_block(
        blocks,
        seen_ids,
        _candidate_id(f"t-lesson-{section_slug}-reading", text, seen_ids),
        text,
        origin="lesson_reading",
        source=reading_source,
    )


def _exercise_audio_text(exercise: dict[str, Any]) -> str:
    """Extract one canonical Norwegian target sentence from an exercise."""
    operation = exercise.get("operation")
    payload = exercise.get("payload")
    if not isinstance(payload, dict):
        return ""
    if operation == "judge":
        return _judge_audio_text(exercise, payload)
    if operation == "build":
        return _build_audio_text(exercise, payload)
    if operation == "recall_fill":
        return _recall_fill_audio_text(exercise, payload)
    if operation == "speak":
        return _speak_audio_text(exercise, payload)
    return ""


def _judge_audio_text(exercise: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return the target for a correct judge exercise."""
    if payload.get("is_correct") is not True:
        return ""
    text = _flatten_spans(payload.get("sentence"), norwegian_only=True)
    if not text:
        raise ValueError(f"exercise {exercise.get('id', 'judge')!r} has no valid judge target")
    return text


def _build_audio_text(exercise: dict[str, Any], payload: dict[str, Any]) -> str:
    """Reconstruct the target sentence from a build exercise's answer order."""
    tokens = {
        token.get("token_id"): str(token.get("text", ""))
        for token in payload.get("tokens", [])
        if isinstance(token, dict) and isinstance(token.get("token_id"), str)
    }
    answer_order = payload.get("answer_order")
    if not isinstance(answer_order, list) or not answer_order:
        raise ValueError(f"exercise {exercise.get('id', 'build')!r} has no build answer order")
    missing = [token_id for token_id in answer_order if token_id not in tokens]
    if missing:
        raise ValueError(
            f"exercise {exercise.get('id', 'build')!r} build answer_order references unknown token(s): {missing!r}"
        )
    text = _join_tokens(tokens[token_id] for token_id in answer_order)
    if not text:
        raise ValueError(f"exercise {exercise.get('id', 'build')!r} has an empty build target")
    return text


def _recall_fill_audio_text(exercise: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return and validate the authored audio target for recall-fill."""
    target = payload.get("audio_target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"exercise {exercise.get('id', 'recall_fill')!r} has an empty audio_target")
    if _known_non_norwegian_words(target):
        raise ValueError(f"exercise {exercise.get('id', 'recall_fill')!r} audio_target must contain Norwegian only")
    return target.strip()


def _speak_audio_text(exercise: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return and validate the authored target for speak."""
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"exercise {exercise.get('id', 'speak')!r} has an empty speak target")
    return target.strip()


def _append_transcript_block(
    blocks: list[dict[str, Any]],
    seen_ids: set[str],
    block_id: str,
    text: str,
    *,
    origin: str,
    source: dict[str, Any],
) -> None:
    """Append one Norwegian transcript block and omit foreign candidates."""
    safe_text = _validate_norwegian_audio_text(text.strip())
    if safe_text is None:
        return
    if block_id in seen_ids:
        raise ValueError(f"derived transcript ids must be unique: {block_id!r}")
    seen_ids.add(block_id)
    blocks.append(
        {
            "id": block_id,
            "text": safe_text,
            "origin": origin,
            "source": source,
            "audio": {
                "role": "model",
                "language": K_TRANSCRIPT_AUDIO_LANGUAGE,
            },
        }
    )


def _candidate_id(prefix: str, text: str, seen_ids: set[str]) -> str:
    """Create a content-stable candidate ID, suffixing duplicate text."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:12]
    base = f"{prefix}-{digest}"
    candidate_id = base
    duplicate_index = 2
    while candidate_id in seen_ids:
        candidate_id = f"{base}-{duplicate_index}"
        duplicate_index += 1
    return candidate_id


def _is_audio_safe_example(item: dict[str, Any]) -> bool:
    """Skip examples explicitly labelled as incorrect in their translation."""
    structured_audio = item.get("audio")
    if isinstance(structured_audio, bool):
        return structured_audio
    if isinstance(structured_audio, dict) and structured_audio.get("enabled") is False:
        return False
    norwegian = _flatten_spans(item.get("no")).lstrip("* `_\t'\"")
    if norwegian.lower().startswith(("✗", "incorrect:", "wrong:", "not normally:", "not:")):
        return False
    english = _flatten_spans(item.get("en")).lstrip("* `_\t'\"")
    return not english.lower().startswith(("✗", "incorrect:", "wrong:", "intended meaning:", "not standard:", "not:"))


def _flatten_spans(spans: object, *, strip: bool = True, norwegian_only: bool = False) -> str:
    """Flatten inline spans, optionally rejecting explicitly foreign content."""
    if not isinstance(spans, list):
        return ""
    pieces: list[str] = []
    for span in spans:
        if isinstance(span, dict):
            if norwegian_only:
                language = span.get("lang")
                if language is not None and language != "no":
                    return ""
            value = span.get("value")
            if isinstance(value, str):
                pieces.append(value)
            else:
                pieces.append(
                    _flatten_spans(
                        span.get("children"),
                        strip=strip,
                        norwegian_only=norwegian_only,
                    )
                )
        elif isinstance(span, str):
            pieces.append(span)
    text = "".join(pieces)
    return text.strip() if strip else text


def _validate_norwegian_audio_text(text: str) -> str | None:
    """Return text only when it has no known English contamination."""
    foreign_words = _known_non_norwegian_words(text)
    if foreign_words:
        return None
    return text


def _known_non_norwegian_words(text: str) -> list[str]:
    """Return sorted known English words in one prospective audio fragment."""
    words = re.findall(r"[A-Za-zÆØÅæøå]+", text.lower())
    return sorted({word for word in words if word in K_TRANSCRIPT_NON_NORWEGIAN_AUDIO_WORDS})


def _join_tokens(tokens: Iterable[Any]) -> str:
    """Join token text into readable Norwegian, tightening sentence punctuation."""
    text = " ".join(str(token).strip() for token in tokens if str(token).strip()).strip()
    for punctuation in ",.!?;:":
        text = text.replace(f" {punctuation}", punctuation)
    return text


def _slug_component(value: object) -> str:
    """Normalize a source identifier for a deterministic transcript ID."""
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return normalized or "item"


__all__ = ["derive_transcript_blocks"]
