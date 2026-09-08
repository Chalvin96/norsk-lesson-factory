"""Behavior tests for the lesson validation result contract."""

from lesson_builder.domain.lesson.models.checks import CheckResult


def test_check_result_is_blocking_given_non_advisory_blocker_expect_true():
    result = CheckResult(check_id="nynorsk_scan", severity="blocker", unit_id="ex1", message="Nynorsk form found")
    assert result.is_blocking is True
    assert result.advisory is False


def test_check_result_is_blocking_given_advisory_blocker_expect_false():
    # Advisory LLM judges log blocker-severity verdicts without driving routing.
    result = CheckResult(
        check_id="pedagogy_check",
        severity="blocker",
        unit_id="",
        message="weak scaffolding",
        advisory=True,
    )
    assert result.is_blocking is False


def test_check_result_is_blocking_given_warning_expect_false():
    result = CheckResult(check_id="bloom_alignment", severity="warning", unit_id="o1", message="bloom drift")
    assert result.is_blocking is False


def test_check_result_given_missing_fix_hint_expect_none():
    result = CheckResult(check_id="schema_validate", severity="blocker", message="bad")
    assert result.fix_hint is None
    result_with_hint = CheckResult(
        check_id="schema_validate", severity="blocker", message="bad", fix_hint="rebuild payload"
    )
    assert result_with_hint.fix_hint == "rebuild payload"


def test_check_result_given_missing_unit_id_expect_empty_string():
    result = CheckResult(check_id="stale_check", severity="warning", message="requirements changed")
    assert result.unit_id == ""
