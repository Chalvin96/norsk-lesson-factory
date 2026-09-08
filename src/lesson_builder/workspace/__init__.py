"""Not a check itself — canonical workspace paths and file-system boundaries.

The package exposes path resolution only. Composed authoring checks live in
``application.operations.check_lesson_data`` so importing workspace mechanics
does not load domain validators.
"""

from lesson_builder.workspace.paths import WorkspacePaths

__all__ = ["WorkspacePaths"]
