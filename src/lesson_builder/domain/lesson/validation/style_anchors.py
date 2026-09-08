"""Not a check itself — shared naturalness/register style anchors.

Single source of truth for the register, terminology, and pronunciation facts
that both the author side (``cold_author/naturalness_guide.py``) and the reviewer
side (``judges.py`` naturalness prefix) depend on. Kept neutral so it can be
hosted in an imperative author guide and an evaluative reviewer rubric without
drifting apart. Leaf module: imports nothing from ``pipeline``.
"""

from __future__ import annotations

# Register / terminology / pronunciation / word-order standard. Stated as the
# norm a lesson should meet; the author writes to it, the reviewer scores against
# it. Phrased verbatim-stable so both prompts cite the same examples.
K_STYLE_ANCHORS_REGISTER_POLICY = (
    "The terminology style guide is authoritative: accept pinned and Also OK glossary terms "
    "when their level guidance fits. Standard textbook grammar terms are acceptable even "
    "at A1/A2 when the lesson needs the concept and the term is introduced, scaffolded with a "
    'plain explanation, or already established in the lesson (e.g. "infinitive", "bare '
    'infinitive", "modal verb", "present tense"). Pair the first technical label with a learner '
    "action or plain explanation, and use only established labels. Assume more terminology at "
    "higher levels. Pronunciation guidance must give an "
    "English approximation plus a mouth-position note (e.g. lips rounded, tongue position), not "
    'a bare phonetic label like "front rounded vowel" on its own. Word order: prefer the '
    'neutral default "heller ikke" (not "ikke ... heller").'
)

__all__ = ["K_STYLE_ANCHORS_REGISTER_POLICY"]
