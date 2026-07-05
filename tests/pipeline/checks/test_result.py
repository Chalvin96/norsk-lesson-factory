from lesson_builder.pipeline.checks.result import CheckResult


def test_check_result_is_blocking_given_non_advisory_blocker_expect_true():
    r = CheckResult(check_id="nynorsk_scan", severity="blocker", unit_id="ex1", message="Nynorsk form found")
    assert r.is_blocking is True
    assert r.advisory is False


def test_check_result_is_blocking_given_advisory_blocker_expect_false():
    # Uncalibrated LLM judges log blocker-severity verdicts without driving routing.
    r = CheckResult(
        check_id="pedagogy_check", severity="blocker", unit_id="", message="weak scaffolding",
        advisory=True,
    )
    assert r.is_blocking is False


def test_check_result_is_blocking_given_warning_expect_false():
    r = CheckResult(check_id="bloom_alignment", severity="warning", unit_id="o1", message="bloom drift")
    assert r.is_blocking is False


def test_check_result_given_missing_fix_hint_expect_none():
    r = CheckResult(check_id="schema_validate", severity="blocker", message="bad")
    assert r.fix_hint is None
    r2 = CheckResult(check_id="schema_validate", severity="blocker", message="bad", fix_hint="rebuild payload")
    assert r2.fix_hint == "rebuild payload"


def test_check_result_given_missing_unit_id_expect_empty_string():
    r = CheckResult(check_id="stale_check", severity="warning", message="requirements changed")
    assert r.unit_id == ""
