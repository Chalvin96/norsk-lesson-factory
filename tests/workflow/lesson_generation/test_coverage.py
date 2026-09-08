"""Tests for plan-owned coverage units and deterministic evidence checks."""

from __future__ import annotations

from lesson_builder.workflow.lesson_generation.coverage import evaluate_required_coverage
from lesson_builder.workflow.lesson_generation.coverage import parse_plan_coverage


def _lesson() -> str:
    return """---
type: Lesson
slug: sample
title: Sample
cefr_level: A2
goal: Teach the rule.
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand, apply]
objectives:
  - id: obj-lesson
    statement: Teach the rule.
    bloom_targets: [understand, apply]
---

## The model {#sec-model role=model objectives=obj-lesson}

The rule is explained here.

::: examples
- no: Hvis jeg kan komme, får jeg en time.
- en: If I can come, I get an appointment.
:::
"""


def _exercises() -> str:
    return "- handle: choose-rule" + chr(10)


def test_parse_plan_coverage_given_frontmatter_units_expect_stable_contract():
    plan = """---
objectives:
  - id: obj-lesson
    statement: Teach the rule.
coverage:
  - id: cov-rule
    objective: obj-lesson
    claim: Explain the rule.
    scope: required
    evidence: [explanation, controlled_practice]
---
# Plan
"""

    contract = parse_plan_coverage(plan)

    assert contract.configured is True
    assert contract.units[0].id == "cov-rule"
    assert contract.units[0].objective == "obj-lesson"


def _coverage_plan(*evidence: str) -> str:
    return f"""---
objectives:
  - id: obj-lesson
    statement: Teach the rule.
coverage:
  - id: cov-rule
    objective: obj-lesson
    claim: Explain the rule.
    scope: required
    evidence: [{", ".join(evidence)}]
---
# Plan
"""


def test_evaluate_required_coverage_given_unconfigured_plan_expect_unconfigured():
    result = evaluate_required_coverage(
        plan_text="---\nobjectives:\n  - id: obj-lesson\n---\n# Plan\n",
        lesson_md=_lesson(),
        exercises_yaml=_exercises(),
    )

    assert result["status"] == "unconfigured"
    assert result["findings"] == ["coverage_unconfigured: plan declares no coverage contract"]
    assert result["units_checked"] == 0


def test_evaluate_required_coverage_given_linked_evidence_expect_pass():
    result = evaluate_required_coverage(
        plan_text=_coverage_plan("explanation", "example"),
        lesson_md=_lesson(),
        exercises_yaml="- handle: choose-rule\n  objective: obj-lesson\n",
    )

    assert result["status"] == "pass"
    assert result["findings"] == []
    assert result["units_checked"] == 1


def test_evaluate_required_coverage_given_objective_without_exercise_expect_needs_human():
    result = evaluate_required_coverage(
        plan_text=_coverage_plan("explanation", "controlled_practice"),
        lesson_md=_lesson(),
        exercises_yaml="- handle: other-rule\n  objective: obj-other\n",
    )

    assert result["status"] == "needs_human"
    assert "objective_without_exercise:obj-lesson" in result["findings"]
    assert "cov-rule:missing_evidence:controlled_practice" in result["findings"]


def test_evaluate_required_coverage_given_missing_contrast_section_expect_needs_human():
    result = evaluate_required_coverage(
        plan_text=_coverage_plan("contrast_example"),
        lesson_md=_lesson(),
        exercises_yaml="- handle: choose-rule\n  objective: obj-lesson\n",
    )

    assert result["status"] == "needs_human"
    assert result["findings"] == ["cov-rule:missing_evidence:contrast_example"]
