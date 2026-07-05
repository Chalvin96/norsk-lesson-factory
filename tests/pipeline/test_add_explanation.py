"""add_explanation appends an authored section without mutating the input."""

from __future__ import annotations

from lesson_builder.pipeline.improvement_steps.add_explanation import add_explanation
from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec


class _Author:
    def invoke(self, prompt, **kw):
        return (
            '{"elements": [{"element_kind": "section", "role": "orient", '
            '"objective_ids": [], "title": "Forklaring", "blocks": [{"kind": "paragraph", '
            '"spans": [{"kind": "text", "value": "fordi ..."}]}]}]}'
        )


class _BadAuthor:
    def invoke(self, prompt, **kw):
        return "not json at all"


def _spec():
    return ImprovementSpec(
        operation="add_explanation",
        target_slug="ordinal_numbers",
        count=1,
        confidence="high",
        interpretation_summary="clarify the objective",
    )


def test_add_explanation_appends_authored_section():
    lesson = {"concept_slug": "ordinal_numbers", "objectives": [{"id": "o1"}], "elements": []}

    out = add_explanation(_spec(), lesson, author_agent=_Author())

    assert any(e.get("title") == "Forklaring" for e in out["elements"])
    assert out is not lesson
    assert lesson["elements"] == []  # input untouched


def test_add_explanation_given_bad_response_returns_lesson_unchanged():
    lesson = {"concept_slug": "ordinal_numbers", "objectives": [{"id": "o1"}], "elements": []}

    out = add_explanation(_spec(), lesson, author_agent=_BadAuthor())

    assert out["elements"] == []
