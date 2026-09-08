"""Entry point: `validate_exercise_envelope` checks the exercise transport boundary."""

from __future__ import annotations


def validate_exercise_envelope(raw: object) -> None:
    """Reject prose, duplicate handles, or internal provenance in parsed YAML."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("exercise author must return a non-empty YAML list")
    handles: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("exercise author YAML items must be mappings")
        handle = item.get("handle")
        if not isinstance(handle, str) or not handle:
            raise ValueError("every exercise must have a stable handle")
        if handle in handles:
            raise ValueError(f"duplicate exercise handle {handle!r}")
        handles.append(handle)
        if not isinstance(item.get("op"), str) or not isinstance(item.get("prompt_md"), str):
            raise TypeError(f"exercise {handle!r} is missing op or prompt_md")
        if "derived_from" in item:
            raise ValueError(
                f"exercise {handle!r} must not carry internal provenance (derived_from); "
                "the exercise-author handoff is self-contained"
            )


__all__ = ["validate_exercise_envelope"]
