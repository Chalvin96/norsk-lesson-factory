"""Entry point: ``validate_slug`` — the canonical lesson-slug contract.

Every filesystem path and checkpoint thread id in the pipeline is built from a
lesson slug (``content/lessons/<slug>/``, ``dist/lessons/<slug>.json``,
``tmp/<subdir>/<slug>``, ``store/scratch/...``, ``"{slug}:{run_id}"``).
Callers reach these boundaries from the CLI and scratch state, so
one contract validates a slug BEFORE any path component is constructed rather
than trusting each call site to sanitize independently.
"""

from __future__ import annotations

import re

# A slug is one safe path component: lowercase ascii alphanumerics grouped by
# single underscores (e.g. ``adjective_agreement``). This forbids the empty
# string, path separators, ``..``, leading/trailing/doubled underscores,
# whitespace, and every character that could escape a single path segment.
K_SLUG_CONTRACT = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


class InvalidSlugError(ValueError):
    """A lesson slug is empty, malformed, or would escape its path component."""


def validate_slug(slug: str) -> str:
    """Return ``slug`` unchanged when it satisfies the contract, else raise.

    Raises ``InvalidSlugError`` for anything that is not a single safe path
    component, so a malformed or traversal slug cannot reach ``Path`` joining or
    a checkpoint thread id.
    """
    if not isinstance(slug, str) or not K_SLUG_CONTRACT.fullmatch(slug):
        raise InvalidSlugError(f"invalid lesson slug {slug!r}")
    return slug
