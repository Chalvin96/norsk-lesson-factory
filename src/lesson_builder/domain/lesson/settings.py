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
)
K_LESSON_QUALITY_REVIEW_COMMON_AXES: tuple[str, ...] = (
    "observable_decision",
    "meaning_before_form",
    "bokmal_and_translation",
    "cefr_scope",
    "terminology",
    "practice_progression",
    "retrieval_or_transfer",
)
K_LESSON_QUALITY_REVIEW_CATEGORY_AXES: dict[str, tuple[str, ...]] = {
    "grammar": ("compact_generalization", "aligned_form_meaning_contrast", "misconception_resolution"),
    "phraseology": ("whole_unit_meaning", "collocational_boundary", "non_interchangeability"),
    "pronunciation": ("perception_accuracy", "sound_spelling_link", "contrast_in_context"),
    "communicative": ("speech_act_purpose", "turn_choice_consequence", "repair_and_transfer"),
    "writing": ("model_text_fitness", "organization_language_choices", "revision_guidance"),
}
