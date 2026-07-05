from __future__ import annotations

import factory

from lesson_builder.pipeline.lesson_acceptance_log import LessonAcceptanceEntry


class LessonAcceptanceEntryFactory(factory.Factory):
    class Meta:
        model = LessonAcceptanceEntry

    slug = "past_tense"
    source_kind = "external_import"
    source_lesson_hash = "sha256:abc"
    status = "accepted"
    reviewer = "system"
    export_path = "dist/lessons/past_tense.json"
    export_hash = "sha256:def"
