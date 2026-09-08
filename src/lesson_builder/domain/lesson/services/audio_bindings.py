"""Entry point: `audio_binding_key` derives the stable audio source locator.

The public packet projection (``domain/lesson/validation/export_serializer.py``) and application audio
synthesis (``application/operations/synthesize_audio.py``) share this one contract for naming the
lesson source position an audio ID attaches to.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def audio_binding_key(source: Mapping[str, Any]) -> str:
    """Return the stable source locator key used to attach an audio ID."""
    exercise_id = source.get("exercise_id")
    if exercise_id is not None:
        return _build_exercise_binding_key(exercise_id)
    section_id = source.get("section_id")
    if not isinstance(section_id, str) or not section_id:
        raise ValueError("audio source requires a section_id")
    return _build_section_binding_key(source, section_id)


def _build_exercise_binding_key(exercise_id: object) -> str:
    """Return the binding key for one exercise audio source."""
    if not isinstance(exercise_id, str) or not exercise_id:
        raise ValueError("audio source exercise_id must be a non-empty string")
    return f"exercise:{exercise_id}"


def _build_section_binding_key(source: Mapping[str, Any], section_id: str) -> str:
    """Return the binding key for one section audio source."""
    block_path = source.get("block_path")
    if block_path is not None:
        key = _build_path_binding_key(section_id, block_path)
    else:
        block_index = source.get("block_index")
        key = f"section:{section_id}:block:{_require_nonnegative_index(block_index, 'audio source requires a non-negative block_index')}"
    item_index = source.get("item_index")
    if item_index is not None:
        key += f":item:{_require_nonnegative_index(item_index, 'audio source item_index must be non-negative')}"
    return key


def _build_path_binding_key(section_id: str, block_path: object) -> str:
    """Return a section path binding after validating every path index."""
    if (
        not isinstance(block_path, list)
        or not block_path
        or any(not isinstance(index, int) or index < 0 for index in block_path)
    ):
        raise ValueError("audio source block_path must contain non-negative indexes")
    return f"section:{section_id}:path:{'.'.join(str(index) for index in block_path)}"


def _require_nonnegative_index(value: object, error: str) -> int:
    """Return one non-negative integer index with the caller's error text."""
    if not isinstance(value, int) or value < 0:
        raise ValueError(error)
    return value


__all__ = ["audio_binding_key"]
