"""Not a check itself — command-family registration for the lesson-data CLI."""

from __future__ import annotations

from lesson_builder.cli.commands.catalog_design import add_catalog_design_commands
from lesson_builder.cli.commands.check_workspace import add_check_workspace_commands
from lesson_builder.cli.commands.curriculum import add_curriculum_commands
from lesson_builder.cli.commands.doctor import add_doctor_commands
from lesson_builder.cli.commands.exercise_quality import add_exercise_commands
from lesson_builder.cli.commands.lesson_generation import add_lesson_generation_commands
from lesson_builder.cli.commands.lesson_validation import add_lesson_validation_commands
from lesson_builder.cli.commands.preview import add_preview_commands
from lesson_builder.cli.commands.publication import add_publication_commands
from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.cli.commands.release import add_release_commands
from lesson_builder.cli.commands.terminology import add_terminology_commands


def add_commands(parent: SubparserRegistrar) -> None:
    """Register every lesson-data command family in CLI display order."""
    add_lesson_validation_commands(parent)
    add_terminology_commands(parent)
    add_catalog_design_commands(parent)
    add_lesson_generation_commands(parent)
    add_curriculum_commands(parent)
    add_exercise_commands(parent)
    add_release_commands(parent)
    add_check_workspace_commands(parent)
    add_publication_commands(parent)
    add_doctor_commands(parent)
    add_preview_commands(parent)


__all__ = [
    # Registration surface.
    "SubparserRegistrar",
    # Command-family registrars.
    "add_commands",
    "add_catalog_design_commands",
    "add_check_workspace_commands",
    "add_curriculum_commands",
    "add_exercise_commands",
    "add_lesson_generation_commands",
    "add_lesson_validation_commands",
    "add_publication_commands",
    "add_preview_commands",
    "add_release_commands",
    "add_terminology_commands",
]
