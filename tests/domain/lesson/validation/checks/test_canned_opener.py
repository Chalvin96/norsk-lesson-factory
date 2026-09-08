from copy import deepcopy

from lesson_builder.domain.lesson.validation.checks.validators.canned_opener import canned_opener_check


def _lesson_with_explanation(explanation_text: str) -> dict:
    return {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_open_001",
                "operation": "choose",
                "payload": {"stem": [{"kind": "text", "value": "Pick the right form."}]},
                "explanation": [{"kind": "text", "value": explanation_text}],
            }
        ],
    }


def test_canned_opener_check_given_correct_opener_expect_advisory_warning():
    # setup
    lesson = _lesson_with_explanation("Correct. Because bok is common-gender, it takes -en.")

    # execute
    results = canned_opener_check(lesson)

    # assert
    assert len(results) == 1
    result = results[0]
    assert result.check_id == "canned_opener"
    assert result.severity == "warning"
    assert result.advisory
    assert result.unit_id == "ex_open_001"
    assert not result.is_blocking


def test_canned_opener_check_given_reason_first_expect_no_results():
    # setup
    lesson = _lesson_with_explanation("Bok is neuter, so it takes the -et suffix.")

    # execute
    results = canned_opener_check(lesson)

    # assert
    assert results == []


def test_canned_opener_check_given_exercises_with_and_without_opener_expect_one_finding():
    # setup
    lesson = {
        "concept_slug": "test_slug",
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex_good",
                "explanation": [{"kind": "text", "value": "The suffix -en marks definite singular."}],
            },
            {
                "element_kind": "exercise",
                "id": "ex_bad",
                "explanation": [{"kind": "text", "value": "Good. You picked the common-gender form."}],
            },
        ],
    }

    # execute
    results = canned_opener_check(lesson)

    # assert
    bad = [r for r in results if r.unit_id == "ex_bad"]
    good = [r for r in results if r.unit_id == "ex_good"]
    assert len(bad) == 1
    assert bad[0].severity == "warning"
    assert bad[0].advisory
    assert good == []


def test_canned_opener_check_given_empty_explanation_expect_no_results():
    # setup — exercise with no explanation list
    lesson = deepcopy(_lesson_with_explanation("valid text"))
    lesson["elements"][0]["explanation"] = []

    # execute
    results = canned_opener_check(lesson)

    # assert
    assert results == []
