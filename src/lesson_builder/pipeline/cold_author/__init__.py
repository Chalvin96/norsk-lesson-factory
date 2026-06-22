"""Entry point: ``cold_author_flow``.

Cold authoring (Journey 1): produce a draft ``Lesson`` from a concept-
requirements brief and park it at the shared Lesson-QA human gate. Stages
(metadata + objectives, sections, exercises, assembly) are exported for
testability and reuse.
"""

from lesson_builder.pipeline.cold_author.assemble import assemble_lesson
from lesson_builder.pipeline.cold_author.exercises import author_exercises
from lesson_builder.pipeline.cold_author.flow import cold_author_flow
from lesson_builder.pipeline.cold_author.metadata_objectives import author_metadata_objectives
from lesson_builder.pipeline.cold_author.models import (
    AssemblyError,
    ColdAuthorResult,
    ColdMetadata,
    ColdObjective,
    StageFailure,
    StageOK,
    StageResult,
)
from lesson_builder.pipeline.cold_author.sections import author_sections

__all__ = [
    # Flow + stage entry points
    "cold_author_flow",
    "author_metadata_objectives",
    "author_sections",
    "author_exercises",
    "assemble_lesson",
    # Shared types
    "ColdAuthorResult",
    "ColdMetadata",
    "ColdObjective",
    "StageOK",
    "StageFailure",
    "StageResult",
    "AssemblyError",
]
