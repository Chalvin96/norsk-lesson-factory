"""Tests for ``terminology_check``, ``terminology_audit``, and ``extract_learner_text``.

Entry point: ``terminology_check`` / ``terminology_audit``.

Covers the simplified flat ban-list linter:
- banned phrase flagged (warning)
- prose-tell density flagged (appositive-density above threshold)
- literal prose-tell phrases flagged
- clean lesson passes
- audit/gate parity (same extract_learner_text, same finding for a fixture)
- both-shape extraction (element_kind / kind) regression guard

All offline.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.checks.validators.terminology import K_TERM_CHECK_BANNED
from lesson_builder.domain.lesson.validation.checks.validators.terminology import K_TERM_CHECK_PROSE_TELL
from lesson_builder.domain.lesson.validation.checks.validators.terminology import terminology_audit
from lesson_builder.domain.lesson.validation.checks.validators.terminology import terminology_check
from lesson_builder.domain.lesson.validation.terminology import extract_learner_text

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _span(text: str) -> dict[str, str]:
    return {"kind": "text", "value": text}


def _section(section_id: str, paragraphs: list[str]) -> dict[str, Any]:
    return {
        "kind": "section",
        "id": section_id,
        "role": "orient",
        "title": "Test section",
        "blocks": [{"kind": "paragraph", "spans": [_span(p)]} for p in paragraphs],
    }


def _exercise_choose(
    ex_id: str,
    option_texts: list[str],
    whys: list[str] | None = None,
    prompt: str = "Choose the best answer.",
) -> dict[str, Any]:
    whys = whys or [""] * len(option_texts)
    options = []
    for i, (text, why) in enumerate(zip(option_texts, whys, strict=False)):
        opt: dict[str, Any] = {"option_id": chr(ord("a") + i), "text": text}
        if why:
            opt["why"] = why
        options.append(opt)
    return {
        "kind": "exercise",
        "id": ex_id,
        "operation": "choose",
        "prompt": [_span(prompt)],
        "explanation": [],
        "payload": {"options": options, "answer_id": "a"},
    }


def _lesson(elements: list[dict[str, Any]], cefr_level: str = "A2") -> dict[str, Any]:
    return {
        "key": "test_lesson",
        "concept_slug": "test_lesson",
        "cefr_level": cefr_level,
        "title": "Test Lesson",
        "goal": "Test",
        "elements": elements,
    }


def _test_bans() -> TerminologyBans:
    """A compact ban-list mirroring the production config."""
    return TerminologyBans(
        banned_phrases=["tense-carrying verb", "subjunction"],
        prose_tells=["In this lesson, you will", "finite verb, the tense-carrying verb"],
    )


# ---------------------------------------------------------------------------
# Ban-list projection contract
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Banned phrase flagged
# ---------------------------------------------------------------------------


class TestBannedPhrase:
    def test_terminology_check_given_banned_surface_expect_blocker(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section("sec_1", ["The tense-carrying verb shows the tense."]),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        banned = [r for r in results if r.check_id == K_TERM_CHECK_BANNED]
        assert len(banned) == 1
        assert "tense-carrying verb" in banned[0].message
        assert banned[0].severity == "blocker"
        assert banned[0].revision_target is False
        assert banned[0].is_blocking is True

    def test_terminology_check_given_subjunction_expect_warning(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section("sec_1", ["A subjunction introduces a subordinate clause."]),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        banned = [r for r in results if r.check_id == K_TERM_CHECK_BANNED]
        assert len(banned) == 1
        assert "subjunction" in banned[0].message

    def test_terminology_check_given_presens_label_expect_no_deterministic_warning(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section("sec_1", ["Norwegian presens corresponds to the English present tense."]),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert: presens is allowed as a Norwegian label; the LLM reviewer
        # handles English-prose preference for "present tense".
        banned = [r for r in results if r.check_id == K_TERM_CHECK_BANNED]
        assert banned == []

    def test_terminology_check_given_banned_in_option_why_expect_flagged(self):
        # setup: banned term in option 'why' should be found
        bans = _test_bans()
        lesson = _lesson(
            [
                _exercise_choose(
                    "ex_1",
                    option_texts=["Option A", "Option B"],
                    whys=["This uses the tense-carrying verb.", "This does not."],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        banned = [r for r in results if r.check_id == K_TERM_CHECK_BANNED]
        assert len(banned) == 1
        assert "tense-carrying verb" in banned[0].message


# ---------------------------------------------------------------------------
# Prose-tell density (threshold is a linter constant)
# ---------------------------------------------------------------------------


class TestProseTell:
    def test_terminology_check_given_repeated_appositive_expect_prose_tell(self):
        # setup: threshold = 2, so two ", meaning" patterns trigger the density tell
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "Snakker, meaning to speak, is common. Lese, meaning to read, is also common.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL]
        assert len(tells) >= 2
        assert tells[0].severity == "warning"

    def test_terminology_check_given_single_appositive_expect_no_density_tell(self):
        # setup: only one ", meaning" -> below threshold -> no density finding
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "Snakker, meaning to speak, is a common verb in Norwegian.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert: no density-based prose-tell (threshold = 2)
        density_tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL and "density" in r.message]
        assert density_tells == []

    def test_terminology_check_given_literal_in_this_lesson_expect_warning(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "In this lesson, you will learn about Norwegian verbs.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        tells = [
            r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL and "In this lesson, you will" in r.message
        ]
        assert len(tells) >= 1

    def test_terminology_check_given_finite_verb_tell_literal_expect_warning(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "The finite verb, the tense-carrying verb, sits in second position.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert: the literal prose-tell phrase is flagged
        tell_findings = [
            r
            for r in results
            if r.check_id == K_TERM_CHECK_PROSE_TELL and "finite verb, the tense-carrying verb" in r.message
        ]
        assert len(tell_findings) >= 1


# ---------------------------------------------------------------------------
# Clean lesson passes
# ---------------------------------------------------------------------------


class TestCleanLesson:
    def test_terminology_check_given_clean_lesson_expect_no_findings(self):
        # setup: a lesson with no banned phrases and no prose tells
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "In Norwegian, verbs change depending on the subject.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        assert results == []


# ---------------------------------------------------------------------------
# Audit/gate parity
# ---------------------------------------------------------------------------


class TestAuditGateParity:
    def test_extract_learner_text_given_internal_and_exported_shapes_expect_same_count(self):
        # setup: internal uses element_kind, exported uses kind
        internal_section = {
            "element_kind": "section",
            "id": "sec_1",
            "blocks": [{"kind": "paragraph", "spans": [_span("A finite verb shows tense.")]}],
        }
        exported_section = {
            "kind": "section",
            "id": "sec_1",
            "blocks": [{"kind": "paragraph", "spans": [_span("A finite verb shows tense.")]}],
        }
        internal_lesson = {"cefr_level": "B1", "elements": [internal_section]}
        exported_lesson = {"cefr_level": "B1", "elements": [exported_section]}
        # exercise
        internal_frags = extract_learner_text(internal_lesson)
        exported_frags = extract_learner_text(exported_lesson)
        # assert
        assert len(internal_frags) == len(exported_frags)
        assert internal_frags[0].text == exported_frags[0].text

    def test_terminology_check_and_audit_given_same_lesson_expect_same_banned_finding(self):
        # setup: the live gate (terminology_check) and the audit CLI
        # (terminology_audit) share extract_learner_text and must produce the
        # SAME finding for a fixture.
        bans = _test_bans()
        lesson = _lesson(
            [
                _section("sec_1", ["The tense-carrying verb is another label."]),
            ]
        )
        # exercise
        check_results = terminology_check(lesson, bans)
        audit_rows = terminology_audit(lesson, bans)
        # assert: both find the banned 'tense-carrying verb'
        check_banned = [r for r in check_results if r.check_id == K_TERM_CHECK_BANNED]
        audit_banned = [r for r in audit_rows if r["check_id"] == K_TERM_CHECK_BANNED]
        assert len(check_banned) == len(audit_banned)
        assert len(check_banned) == 1
        assert "tense-carrying verb" in check_banned[0].message
        assert audit_banned[0]["phrase"] == "tense-carrying verb"

    def test_terminology_check_and_audit_given_same_lesson_expect_same_prose_tell_finding(self):
        # setup
        bans = _test_bans()
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "Snakker, meaning to speak, is common. Lese, meaning to read, is common.",
                    ],
                ),
            ]
        )
        # exercise
        check_results = terminology_check(lesson, bans)
        audit_rows = terminology_audit(lesson, bans)
        # assert
        check_tells = [r for r in check_results if r.check_id == K_TERM_CHECK_PROSE_TELL]
        audit_tells = [r for r in audit_rows if r["check_id"] == K_TERM_CHECK_PROSE_TELL]
        assert len(check_tells) == len(audit_tells)
        assert len(check_tells) >= 2


# ---------------------------------------------------------------------------
# Both-shape extraction (regression guard)
# ---------------------------------------------------------------------------


class TestBothShapesExtraction:
    """Regression guard for the internal (element_kind) vs exported (kind) discriminator."""

    def test_extract_learner_text_given_element_kind_section_expect_fragments(self):
        # setup
        internal_lesson = {
            "cefr_level": "B1",
            "elements": [
                {
                    "element_kind": "section",
                    "id": "sec_internal",
                    "blocks": [{"kind": "paragraph", "spans": [_span("Internal shape text.")]}],
                }
            ],
        }
        # exercise
        fragments = extract_learner_text(internal_lesson)
        # assert
        assert len(fragments) == 1
        assert fragments[0].text == "Internal shape text."

    def test_extract_learner_text_given_kind_section_expect_fragments(self):
        # setup
        exported_lesson = {
            "cefr_level": "B1",
            "elements": [
                {
                    "kind": "section",
                    "id": "sec_exported",
                    "blocks": [{"kind": "paragraph", "spans": [_span("Exported shape text.")]}],
                }
            ],
        }
        # exercise
        fragments = extract_learner_text(exported_lesson)
        # assert
        assert len(fragments) == 1
        assert fragments[0].text == "Exported shape text."

    def test_extract_learner_text_given_element_kind_exercise_expect_fragments(self):
        # setup
        internal_lesson = {
            "cefr_level": "B1",
            "elements": [
                {
                    "element_kind": "exercise",
                    "id": "ex_1",
                    "operation": "choose",
                    "prompt": [_span("Pick one.")],
                    "explanation": [],
                    "payload": {
                        "options": [
                            {"option_id": "a", "text": "First option"},
                        ],
                        "answer_id": "a",
                    },
                }
            ],
        }
        # exercise
        fragments = extract_learner_text(internal_lesson)
        # assert
        texts = [f.text for f in fragments]
        assert "Pick one." in texts
        assert "First option" in texts


# ---------------------------------------------------------------------------
# Helper smoke-test: CheckResult construction sanity
# ---------------------------------------------------------------------------


class TestCheckResultConstruction:
    """Quick guard that the simplified validator still returns well-formed CheckResults."""

    def test_terminology_check_given_clean_lesson_expect_all_results_are_checkresult(self):
        # setup
        bans = _test_bans()
        lesson = _lesson([_section("sec_1", ["just prose"])])
        # exercise
        results = terminology_check(lesson, bans)
        # assert
        for result in results:
            assert isinstance(result, CheckResult)
            assert result.severity in ("blocker", "warning", "info")


# ---------------------------------------------------------------------------
# Gloss-template appositive + "+"-notation (new prose-tell patterns)
# ---------------------------------------------------------------------------


class TestNewProseTellPatterns:
    """Tests for the gloss-template appositive (density-gated) and the
    "+"-notation shorthand in answer-facing slots (per-occurrence)."""

    def test_terminology_check_given_repeated_appositives_expect_prose_tell_findings(self):
        # setup: two ", the ... form" appositives hit the density threshold (>= 2)
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "Use the neuter form, the form for adverbs. Take the infinitive, the base form of a verb.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, _test_bans())
        # assert: density >= 2 -> at least one prose-tell finding
        tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL]
        assert len(tells) >= 1

    def test_terminology_check_given_single_appositive_expect_no_prose_tell(self):
        # setup: exactly one ", the ... form" appositive -> density 1 < 2
        lesson = _lesson(
            [
                _section(
                    "sec_1",
                    [
                        "The infinitive, the base form of a verb, is useful.",
                    ],
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, _test_bans())
        # assert: no prose-tell finding from the appositive path (density < 2)
        tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL]
        assert tells == []

    def test_terminology_check_given_plus_notation_in_exercise_prompt_expect_prose_tell(self):
        # setup: "+"-notation in an exercise prompt (answer-facing slot)
        lesson = _lesson(
            [
                _exercise_choose(
                    "ex_1",
                    option_texts=["Option A", "Option B"],
                    prompt='bil + "the"',
                ),
            ]
        )
        # exercise
        results = terminology_check(lesson, _test_bans())
        # assert: one prose-tell finding for the "+" shorthand
        plus_tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL and "'+'-notation" in r.message]
        assert len(plus_tells) == 1

    def test_terminology_check_given_plus_notation_in_section_rule_expect_no_prose_tell(self):
        # setup: "+"-notation in a teaching rule block (legitimate pattern notation)
        rule_section = {
            "kind": "section",
            "id": "sec_1",
            "role": "model",
            "objective_ids": [],
            "title": "T",
            "blocks": [
                {
                    "kind": "rule",
                    "statement": [[_span("Basic pattern: subject + finite verb + object.")]],
                },
            ],
        }
        lesson = _lesson([rule_section])
        # exercise
        results = terminology_check(lesson, _test_bans())
        # assert: no "+" prose-tell finding (section_rule is not answer-facing)
        plus_tells = [r for r in results if r.check_id == K_TERM_CHECK_PROSE_TELL and "'+'-notation" in r.message]
        assert plus_tells == []
