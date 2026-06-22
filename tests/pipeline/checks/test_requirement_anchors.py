import json
from pathlib import Path

import pytest

from lesson_builder.pipeline.checks.validators.requirement_anchors import requirement_anchors_check

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())


def _requirements():
    return json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text())


def test_requirement_anchors_check_given_real_lesson_with_real_requirements_expect_no_results():
    assert requirement_anchors_check(_lesson(), _requirements()) == []


def test_requirement_anchors_check_given_missing_requirement_expect_blocker():
    results = requirement_anchors_check(_lesson(), {"required_anchor_forms": ["uoppnåelig_form"]})
    assert len(results) == 1
    assert results[0].check_id == "requirement_anchors"
    assert results[0].is_blocking
    assert "uoppnåelig_form" in results[0].message


def test_requirement_anchors_check_given_no_requirements_expect_no_results():
    assert requirement_anchors_check(_lesson(), None) == []
    assert requirement_anchors_check(_lesson(), {}) == []
    assert requirement_anchors_check(_lesson(), {"required_anchor_forms": []}) == []


@pytest.mark.parametrize(
    "slug",
    ["collocations", "formal_vs_informal_register", "preterite_vs_present_perfect"],
)
def test_requirement_anchors_check_given_golden_anchor_lessons_expect_no_results(slug: str):
    lesson = json.loads((ROOT / "data/lessons" / f"{slug}.json").read_text())
    requirements = json.loads((ROOT / "data/concept_requirements" / f"{slug}.json").read_text())

    assert requirement_anchors_check(lesson, requirements) == []
