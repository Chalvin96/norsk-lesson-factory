"""Naturalness style-guide for cold-author prompts. Not a check itself — a
prevention-side constant (``K_NATURALNESS_GUIDE``) appended to the section and
exercise authoring prompts so the author writes natural, level-appropriate
Norwegian from the start, reducing the naturalness reviewer's defect rate.

Entry point: ``K_NATURALNESS_GUIDE`` (a string appended to authoring prompts).
"""

from __future__ import annotations

K_NATURALNESS_GUIDE = """\

NATURALNESS STYLE-GUIDE (follow while authoring):
- Word order: prefer the neutral default "heller ikke" (not "ikke ... heller") \
for "neither / not ... either". Use natural adverb/particle placement throughout.
- CEFR register: the terminology style guide wins. Use pinned or Also OK glossary \
terms; do not replace standard textbook terms just because they are technical. \
At A1/A2, standard terms are fine when the lesson needs them and they are \
introduced, scaffolded with a plain explanation, or already established in the \
lesson (e.g. "infinitive", "bare infinitive", "modal verb", "present tense"). \
Avoid unexplained label-dumps and invented labels; keep the learner action or \
plain explanation close to the first technical label. At higher levels you may \
assume more terminology.
- Pronunciation: when describing a sound, always give an English approximation \
and a mouth-position note (e.g. lips rounded, tongue position), not just a \
phonetic label like "front rounded vowel" on its own.
- Messages/e-mails: use a natural greeting and sign-off with a line break; use \
periods as the simple fallback punctuation. Keep the register conversational and \
idiomatic, not stilted.
- Collocations: choose the natural Norwegian collocation a native speaker would \
use; avoid calques from English."""


__all__ = ["K_NATURALNESS_GUIDE"]
