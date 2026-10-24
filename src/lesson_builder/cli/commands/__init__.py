"""Not a check itself — registers the retained lesson-data command set."""

from __future__ import annotations

from lesson_builder.cli.commands.check_workspace import add_check_workspace_commands
from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.cli.commands.release import add_release_commands
from lesson_builder.cli.commands.workbench import add_workbench_commands


def add_commands(parent: SubparserRegistrar) -> None:
    """Register the five supported lesson-data commands in display order."""
    add_check_workspace_commands(parent)
    add_release_commands(parent)
    add_workbench_commands(parent)


__all__ = [
    # Registration surface.
    "SubparserRegistrar",
    # Retained command registrars.
    "add_commands",
    "add_check_workspace_commands",
    "add_release_commands",
    "add_workbench_commands",
]
