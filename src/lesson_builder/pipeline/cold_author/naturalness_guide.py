"""Naturalness style-guide for cold-author prompts. Not a check itself — a
prevention-side constant (``K_NATURALNESS_GUIDE``) appended to the section and
exercise authoring prompts so the author writes natural, level-appropriate
Norwegian from the start, reducing the naturalness reviewer's defect rate.

Entry point: ``K_NATURALNESS_GUIDE`` (a string appended to authoring prompts).
"""

from __future__ import annotations

from lesson_builder.pipeline.style_anchors import REGISTER_POLICY

K_NATURALNESS_GUIDE = f"""\

NATURALNESS STYLE-GUIDE (follow while authoring):
- Register, terminology, pronunciation, and word order: {REGISTER_POLICY} Use natural \
adverb/particle placement throughout.
- Messages/e-mails: use a natural greeting and sign-off with a line break; use \
periods as the simple fallback punctuation. Keep the register conversational and \
idiomatic, not stilted.
- Collocations: choose the natural Norwegian collocation a native speaker would \
use; avoid calques from English."""


__all__ = ["K_NATURALNESS_GUIDE"]
