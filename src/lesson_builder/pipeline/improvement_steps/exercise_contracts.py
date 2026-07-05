"""Shared author-side exercise contract fragments.

Single source of truth for the per-operation flat-field spec, the explanation
quality bar, and the inline-span plaintext rule. Both the cold-author path
(``cold_author/exercises.py``) and the improvement-flow path
(``nodes/add_exercises.py``) compose their author prompts from these constants
so the two paths can never drift apart silently. Leaf module: it imports nothing
from ``pipeline`` to stay safe for either caller to import.
"""

from __future__ import annotations

# Per-operation flat content the author must return. Python constructs the typed
# payload and every id/structural flag from these flat fields.
PER_OP_FLAT_FIELDS = (
    "Each exercise returns FLAT per-operation content (Python constructs the typed "
    "payload and every id/structural flag). The flat fields per operation are:\n"
    "- judge: sentence_no (Norwegian sentence), is_correct (bool), feedback "
    "(string or null; required when is_correct is false).\n"
    "- choose: options (list of >= 2 unique strings), correct_index (int).\n"
    '- recall_fill: sentence (with "___" blank markers, one per blank), blanks '
    "(list of {options: [>=2 strings], answer: one of options}).\n"
    "- match_pairs: pairs (list of >= 2 {left, right}; all texts unique per side).\n"
    "- categorize: buckets (>= 2 unique labels), items (list of {text, bucket}).\n"
    "- build: sentence (>= 2 words), optional fixed_words.\n"
    "- find_fix: sentence_with_error, error_word (occurs exactly once), feedback."
)

# Quality bar for explanation_text. The key requirement: for operations that
# offer competing options, the explanation must teach the contrast, not just
# bless the correct answer.
EXPLANATION_QUALITY = (
    "explanation_text is the post-answer teaching moment. Do NOT open with an "
    "acknowledgment word (Correct, Good, Right, Yes, Nice, Riktig, Bra) — begin directly "
    "with the reason, e.g. 'Bok is common-gender singular, so it takes -en.'. For any "
    "operation that offers the learner competing options (choose, recall_fill), the "
    "explanation MUST do two things: (1) say why the correct answer fits, and (2) contrast "
    "the wrong options — name each distractor and the meaning/form it would signal instead, "
    "so the learner learns the distinction and not just the answer. For judge/find_fix, "
    "explain what makes the sentence right or wrong, naming the specific token at fault. "
    "Keep it to one or two tight sentences; teach the contrast, do not pad."
)

# Inline span values carry no markup.
SPAN_PLAINTEXT_RULE = "Inline span values are plain text only (no markdown characters: no * ` _)."

__all__ = ["PER_OP_FLAT_FIELDS", "EXPLANATION_QUALITY", "SPAN_PLAINTEXT_RULE"]
