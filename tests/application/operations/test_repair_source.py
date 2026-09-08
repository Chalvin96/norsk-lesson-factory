"""Behavior tests for safe exercise-source repair before human review."""

from __future__ import annotations

import yaml

from lesson_builder.application.operations.repair_source import repair_exercise_source


def test_repair_exercise_source_given_matching_inline_blank_expect_typed_slot():
    """Matching legacy markers are relocated without inventing an answer."""
    source = """- handle: recall-one
  op: recall_fill
  objective: obj-1
  bloom: apply
  prompt_md: Complete the sentence.
  audio_target: 'Jeg kommer ikke.'
  segments:
    - text_md: 'Jeg **[BLANK]** kommer.'
    - blank_id: slot
      options: [ikke, også]
      answer_index: 0
"""

    result = repair_exercise_source(source, lesson_text=_lesson("recall-one"))

    assert any(repair.code == "recall-inline-blank-relocation" for repair in result.repairs)
    assert "[BLANK]" not in result.exercises_yaml
    assert result.audit.status == "clean"


def test_repair_exercise_source_given_inline_formatting_and_scalar_id_expect_normalized_source():
    """Transport-only formatting and scalar IDs are repaired deterministically."""
    source = """- handle: build-one
  op: build
  objective: obj-1
  bloom: apply
  prompt_md: 'Build the sentence: use the words.'
  tokens:
    - token_id: 1
      text: Hei
    - token_id: 2
      text: 'der.'
  answer_order: [1, 2]
"""

    result = repair_exercise_source(source, lesson_text=_lesson("build-one"))

    assert "token_id: '1'" in result.exercises_yaml
    assert "answer_order:\n  - '1'\n  - '2'" in result.exercises_yaml
    assert result.audit.status == "clean"


def test_repair_exercise_source_given_recall_space_before_punctuation_expect_clean_source():
    """Punctuation spacing is normalized without changing the keyed answer."""
    source = """- handle: recall-one
  op: recall_fill
  objective: obj-1
  bloom: apply
  prompt_md: Complete the sentence.
  audio_target: 'Jeg kommer hjem.'
  segments:
    - text_md: 'Jeg '
    - blank_id: slot
      options: [kommer, dro]
      answer_index: 0
    - text_md: ' hjem .'
"""

    result = repair_exercise_source(source, lesson_text=_lesson("recall-one"))

    assert any(repair.code == "recall-punctuation-spacing" for repair in result.repairs)
    assert "hjem ." not in result.exercises_yaml
    assert "hjem." in result.exercises_yaml
    assert result.audit.status == "clean"


def test_repair_exercise_source_given_unquoted_text_colon_expect_quoted_yaml():
    """A YAML transport colon is quoted without changing the visible prompt."""
    source = """- handle: choose-one
  op: choose
  objective: obj-1
  bloom: understand
  prompt_md: Choose the answer: use the taught form.
  options:
    - id: yes
      text: Ja.
      correct: true
    - id: no
      text: Nei.
      correct: false
"""

    result = repair_exercise_source(source, lesson_text=_lesson("choose-one"))

    assert any(repair.code == "yaml-scalar-quoting" for repair in result.repairs)
    assert "prompt_md: 'Choose the answer: use the taught form.'" in result.exercises_yaml
    assert result.audit.status == "clean"


def test_repair_exercise_source_given_folded_prompt_block_expect_inline_text():
    """Folded YAML prompt syntax becomes one compiler-safe paragraph."""
    source = """- handle: choose-one
  op: choose
  objective: obj-1
  bloom: understand
  prompt_md: >-
    Read these clauses and choose the meaning:
    Hvis hun kommer, ringer jeg.
    Hvis det regner, tar vi bussen.
  options:
    - id: condition
      text: Both clauses state conditions.
      correct: true
    - id: reason
      text: Both clauses state reasons.
      correct: false
"""

    result = repair_exercise_source(source, lesson_text=_lesson("choose-one"))

    assert result.audit.status == "clean"
    assert ">-" not in result.exercises_yaml
    parsed = yaml.safe_load(result.exercises_yaml)
    assert parsed[0]["prompt_md"] == (
        "Read these clauses and choose the meaning: Hvis hun kommer, ringer jeg. Hvis det regner, tar vi bussen."
    )


def test_repair_exercise_source_given_missing_recall_options_expect_human_finding():
    """A missing answer set is not guessed and remains a review blocker."""
    source = """- handle: recall-one
  op: recall_fill
  objective: obj-1
  bloom: apply
  prompt_md: Complete the sentence.
  segments:
    - text_md: 'Jeg '
    - blank_id: slot
      answer_index: 0
    - text_md: 'kommer.'
"""

    result = repair_exercise_source(source, lesson_text=_lesson("recall-one"))

    assert result.audit.material_findings
    assert not result.changed


def test_repair_exercise_source_given_conflicting_duplicate_keys_expect_blocked_with_original_evidence():
    """Conflicting duplicate YAML keys stay blocked instead of last-wins repair."""
    source = """- handle: choose-one
  op: choose
  objective: obj-1
  bloom: understand
  prompt_md: Choose the answer.
  options:
    - id: yes
      text: Ja.
      correct: true
      correct: false
    - id: no
      text: Nei.
      correct: false
"""

    result = repair_exercise_source(source, lesson_text=_lesson("choose-one"))

    assert result.exercises_yaml == source
    assert not result.repairs
    assert any(
        finding.code == "exercise-source-invalid" and "duplicate YAML key" in finding.evidence
        for finding in result.audit.material_findings
    )


def test_repair_exercise_source_given_duplicate_build_order_expect_human_finding():
    """An ambiguous token order is preserved for human judgment."""
    source = """- handle: build-one
  op: build
  objective: obj-1
  bloom: apply
  prompt_md: Build the sentence.
  tokens:
    - token_id: one
      text: Hei
    - token_id: two
      text: der.
  answer_order: [one, one]
"""

    result = repair_exercise_source(source, lesson_text=_lesson("build-one"))

    assert any(finding.code == "exercise-source-invalid" for finding in result.audit.material_findings)


def _lesson(handle: str) -> str:
    """Build the smallest marker-bearing lesson copy for repair tests."""
    return f"---\nslug: test\n---\n{{{{exercise: {handle}}}}}\n"
