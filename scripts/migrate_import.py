"""Thin shim over ``lesson_builder.pipeline.lesson_import``.

This historical bootstrap script now points at ``regenerate_dist`` because
``data/lessons`` is the git-tracked source of truth and ``dist/`` is derived.
Keep it only as a convenience wrapper for rebuilding the serving projection.
"""

from __future__ import annotations

from pathlib import Path

from lesson_builder.pipeline.lesson_import import LessonImportError, import_lesson, regenerate_dist

__all__ = ["LessonImportError", "import_lesson", "regenerate_dist"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate dist/lessons/ from data/lessons/")
    parser.add_argument("repo_root", nargs="?", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()
    result = regenerate_dist(Path(args.repo_root))
    print(result)
