"""Shared prompt fragments: the warm-coach VOICE and slot-default LANGUAGE contracts.
Injected into every content + exercise generation call so tone/language never drift
across the many tiny per-(objective x operation) calls."""

VOICE_CONTRACT_FRAGMENT = """\
Voice = "warm coach". Address the learner in second person, encouraging; name the
difficulty and normalize it ("it feels fiddly at first, and that's normal"), then teach
clearly. Warmth never replaces precision — the rule must land completely. Surfaces:
teaching prose names->normalizes->teaches; exercise prompts are short and inviting;
post-grade explanations affirm->give the rule->reassure forward. Guardrails: no baby
talk, no emoji, <=1 exclamation per block, reassurance is one clause not a paragraph,
never warm at the cost of correctness. English voice only; Norwegian examples follow the
CEFR/naturalness rules.\
"""

LANGUAGE_CONTRACT_FRAGMENT = """\
Every slot has a default language; a bare text span is its slot's default; a
`foreign_term` span (with `lang`) is the ONLY cross-language override.
Metalanguage-default (bare = English): paragraph/heading/list/rule/callout text,
`example.en`, exercise prompts/explanations, English-default table columns -> put any
Norwegian form in a `foreign_term{lang:"no"}` span. Target-default (bare = Norwegian):
`example.no`, `word_list`, `col_lang:"no"` table columns, exercise payload target content
-> do NOT tag bare Norwegian there. Paradigm tables: form columns get `col_lang:"no"` and
bare Norwegian cells; the label column gets `col_lang:"en"`.\
"""

PLAIN_TEXT_CONTRACT_FRAGMENT = """\
All span `value` fields must be plain text — no Markdown. Do NOT use *, **, _, ` or any
other Markdown formatting characters inside span values. Use span kinds (emphasis, strong,
code) to express formatting instead of Markdown syntax.\
"""


def shared_system_fragment() -> str:
    return f"{VOICE_CONTRACT_FRAGMENT}\n\n{LANGUAGE_CONTRACT_FRAGMENT}\n\n{PLAIN_TEXT_CONTRACT_FRAGMENT}"
