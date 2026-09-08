"""Entry point: ``render_bug_check``.

Deterministic render-bug check: flags ``foreign_term`` spans that will render
incorrectly in a Bokmal lesson. Two defect classes:

- ``foreign_term`` tagged ``lang="no"`` whose value is a common English function
  word ("the", "a", "an", "of") — an English word mis-labelled as Norwegian.
  This is an unambiguous defect: ``blocker``.
- ``foreign_term`` with ``lang`` other than ``"no"`` — ``lang="en"`` is used
  legitimately for inline English contrast terms and glosses (e.g. "Subject
  form", "there is"), so this is only ``advisory`` (warning): it surfaces the
  span for review without blocking, since it can also indicate an English UI
  label wrongly styled as a foreign term.

Recursively walks the entire element tree so foreign_term spans in section prose,
table cells, callouts, exercise prompts/explanations, and payloads are all
covered. Anchors ``unit_id`` to the containing element id.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.validation.checks.defect_rules import classify_foreign_term
from lesson_builder.domain.lesson.validation.checks.defect_rules import find_foreign_term_spans

K_RENDER_BUG_LANG = "render_bug_lang"
K_RENDER_BUG_ENGLISH_FUNC_WORD = "render_bug_english_func_word"


def render_bug_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag foreign_term spans that will render incorrectly in a Bokmal lesson."""
    results: list[CheckResult] = []
    for element in _elements(data):
        el_id = str(element.get("id", ""))
        for span in find_foreign_term_spans(element):
            defect = classify_foreign_term(span)
            if defect is None:
                continue
            lang = str(span.get("lang", ""))
            value = str(span.get("value", ""))
            if defect == "advisory":
                results.append(
                    CheckResult(
                        check_id=K_RENDER_BUG_LANG,
                        severity="warning",
                        advisory=True,
                        unit_id=el_id,
                        message=(
                            f"foreign_term '{value}' has lang='{lang}', expected 'no'. "
                            f"Legitimate for inline English contrast terms; flag for review "
                            f"in case it is an English UI label styled as a foreign term."
                        ),
                        fix_hint=(
                            "If this is a Bokmal term, set lang='no'. If it is a plain "
                            "English label (e.g. a table header), use a text span instead "
                            "of foreign_term."
                        ),
                    )
                )
            else:  # "blocker"
                results.append(
                    CheckResult(
                        check_id=K_RENDER_BUG_ENGLISH_FUNC_WORD,
                        severity="blocker",
                        unit_id=el_id,
                        message=(f"foreign_term '{value}' tagged lang='no' but is a common English function word"),
                        fix_hint=(
                            f"'{value}' is an English function word; re-tag as lang='en' "
                            f"or replace with the Norwegian equivalent."
                        ),
                    )
                )
    return results


def _elements(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict)]
