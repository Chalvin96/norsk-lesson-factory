"""Entry points: batch generation and human approved lesson promotion.

Concrete graph, state, dependency, and node modules are imported directly by their
owners; this package exposes only the CLI-facing workflow operations.
"""

from lesson_builder.workflow.lesson_generation.batch import generate_lessons
from lesson_builder.workflow.lesson_generation.batch import recompile_lessons
from lesson_builder.workflow.lesson_generation.batch import resume_lessons
from lesson_builder.workflow.lesson_generation.promotion import promote_lesson_approval
from lesson_builder.workflow.lesson_generation.promotion import promote_lesson_batch

__all__ = [
    "generate_lessons",
    "recompile_lessons",
    "resume_lessons",
    "promote_lesson_approval",
    "promote_lesson_batch",
]
