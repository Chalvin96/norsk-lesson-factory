"""Not a check itself — settings for the lesson authoring context."""

from __future__ import annotations

from typing import Literal

K_EXPORT_SPEAKER_ICON_BASE_URL = "https://api.dicebear.com/10.x/open-peeps/svg"
K_LESSON_SCHEMA_VERSION: Literal["4.1"] = "4.1"
K_EXERCISES_DEFAULT_LANG = "nb"
K_TRANSCRIPT_AUDIO_LANGUAGE = "nb-NO"
K_TRANSCRIPT_AUDIO_EXERCISE_OPS: tuple[str, ...] = (
    "recall_fill",
    "judge",
    "build",
    "speak",
)
