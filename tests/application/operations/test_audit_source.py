"""Behavior tests for authored-source auditing."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.audit_source import audit_source_text
from tests.paths import K_CARDINAL_NUMBERS_FIXTURE_ROOT
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT

K_NORWEGIAN_EXAMPLE_BUT_RE = re.compile(r"(?m)^(-\s+no:\s+.*)\bbut\b")
K_NORWEGIAN_EXAMPLE_MEN_RE = re.compile(r"(?m)^(-\s+no:\s+(?:en kopp|ett glass|en banan|ett eple),)\s+men\b")


def test_audit_source_directory_given_english_but_in_norwegian_example_expect_blocking_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path, K_CARDINAL_NUMBERS_FIXTURE_ROOT)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(_introduce_norwegian_example_but(lesson_path.read_text(encoding="utf-8")), encoding="utf-8")

    audit = audit_source_directory(source)

    findings = [finding for finding in audit.findings if finding.code == "norwegian-example-english-conjunction"]
    assert len(findings) == 4
    assert all(finding.severity == "blocking" for finding in findings)
    assert all(finding.artifact == "lesson.md" for finding in findings)


def test_audit_source_directory_given_but_in_english_gloss_expect_no_language_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path, K_CARDINAL_NUMBERS_FIXTURE_ROOT)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(_replace_norwegian_example_but(lesson_path.read_text(encoding="utf-8")), encoding="utf-8")

    audit = audit_source_directory(source)

    assert not any(finding.code == "norwegian-example-english-conjunction" for finding in audit.findings)


def test_audit_source_directory_given_but_in_english_prose_expect_no_language_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path, K_CARDINAL_NUMBERS_FIXTURE_ROOT)
    lesson_path = source / "lesson.md"
    text = _replace_norwegian_example_but(lesson_path.read_text(encoding="utf-8"))
    lesson_path.write_text(text + "\nThe English explanation uses but as a connector.\n", encoding="utf-8")

    audit = audit_source_directory(source)

    assert not any(finding.code == "norwegian-example-english-conjunction" for finding in audit.findings)


def test_audit_source_directory_given_butter_substring_expect_no_language_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path, K_CARDINAL_NUMBERS_FIXTURE_ROOT)
    lesson_path = source / "lesson.md"
    text = _replace_norwegian_example_but(lesson_path.read_text(encoding="utf-8"))
    lesson_path.write_text(text + "\n::: examples\n- no: butter is not a conjunction\n:::\n", encoding="utf-8")

    audit = audit_source_directory(source)

    assert not any(finding.code == "norwegian-example-english-conjunction" for finding in audit.findings)


def test_audit_source_directory_given_valid_fixture_expect_assembled_build_target(tmp_path: Path):
    source = _copy_fixture(tmp_path)

    audit = audit_source_directory(source)

    assert audit.status == "clean"
    assert audit.exercise_handles == audit.marker_handles
    assert [item.text for item in audit.built_answers] == ["I dag jobber jeg hjemme."]
    assert audit.material_findings == []


def test_audit_source_text_given_fixture_content_expect_same_result_as_directory_audit(tmp_path: Path):
    source = _copy_fixture(tmp_path)

    directory_audit = audit_source_directory(source)
    text_audit = audit_source_text(
        (source / "exercises.yaml").read_text(encoding="utf-8"),
        lesson_text=(source / "lesson.md").read_text(encoding="utf-8"),
    )

    assert text_audit == directory_audit


def test_audit_source_directory_given_recall_blank_punctuation_collision_expect_blocking_finding(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    text = exercises_path.read_text(encoding="utf-8")
    text = text.replace("        - Snakker\n", "        - Forresten,\n", 1)
    text = text.replace('text_md: " du norsk?"', 'text_md: ", skal vi gå?"', 1)
    exercises_path.write_text(text, encoding="utf-8")

    audit = audit_source_directory(source)

    assert any(
        finding.code == "recall-rendered-punctuation" and finding.severity == "blocking" and ",," in finding.evidence
        for finding in audit.findings
    )
    assert audit.status == "blocked"


def test_audit_source_directory_given_recall_punctuation_word_join_expect_boundary_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace('text_md: " du norsk?"', 'text_md: ".Jonas"', 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "recall-rendered-boundary-missing" for finding in audit.findings)


def test_audit_source_directory_given_recall_newline_separator_expect_boundary_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace('text_md: " du norsk?"', 'text_md: "\\nJonas"', 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "recall-rendered-boundary-missing" for finding in audit.findings)


def test_audit_source_directory_given_sentence_initial_lowercase_key_expect_capitalization_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("        - Snakker\n", "        - snakker\n", 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "recall-sentence-start-lowercase" for finding in audit.findings)


def test_audit_source_directory_given_find_fix_written_repair_expect_capability_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: Tap the token, then write the correction.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_passive_corrected_description_expect_no_capability_false_positive(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: Tap the token that must be corrected.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert not any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_replace_imperative_expect_capability_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: Tap the token, then replace the incorrect word.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_negated_repair_instruction_expect_no_capability_false_positive(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: Do not restore the original sentence; tap the erroneous token.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert not any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_descriptive_repair_phrase_expect_no_capability_false_positive(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: The task helps you correct the sentence by tapping one token.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert not any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_descriptive_repair_infinitive_expect_no_capability_false_positive(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: The task allows you to correct the sentence by tapping one token.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert not any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_direct_positive_repair_requirement_expect_capability_finding(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: You must repair the sentence after tapping the erroneous token.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_need_to_correct_requirement_expect_capability_finding(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: You need to correct the sentence after tapping the erroneous token.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_repair_only_instruction_expect_capability_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
            "prompt_md: Find the error and repair only that adjective.",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_broad_speak_and_english_write_expect_surface_findings(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8") + "\n{{exercise: speak-broad}}\n{{exercise: write-english}}\n",
        encoding="utf-8",
    )
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8")
        + """
- handle: speak-broad
  op: speak
  objective: obj-question-order
  bloom: apply
  prompt_md: Talk about your morning.
  target: Jeg står opp klokka sju.
- handle: write-english
  op: write
  objective: obj-question-order
  bloom: apply
  prompt_md: Write a short reply in English.
  response_language: "no"
  judge_prompt: Check the reply.
  criteria:
    - id: reply
      instruction: The reply answers the question.
""",
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    codes = {finding.code for finding in audit.findings}
    assert "speak-target-cue-broad" in codes
    assert "write-response-language-conflict" in codes


def test_audit_source_directory_given_missing_build_punctuation_expect_major_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace("text: hjemme.", "text: hjemme", 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(
        finding.code == "build-answer-punctuation-missing" and finding.severity == "major" for finding in audit.findings
    )
    assert audit.status == "blocked"


def test_audit_source_directory_given_marker_handle_drift_expect_blocking_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace(
            "{{exercise: build-fronted-time}}",
            "{{exercise: missing-build-handle}}",
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert any(finding.code == "exercise-marker-handle-mismatch" for finding in audit.findings)
    assert audit.status == "blocked"


def test_audit_source_directory_given_inline_blank_marker_expect_source_invalid(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace('text_md: " du norsk?"', 'text_md: " [BLANK] du norsk?"', 1),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert audit.status == "invalid"
    assert any(
        finding.code == "exercise-source-invalid" and "inline blank marker" in finding.evidence
        for finding in audit.findings
    )


def test_audit_source_directory_given_learner_prompt_marker_and_item_label_expect_no_false_blocker(
    tmp_path: Path,
):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8")
        .replace(
            "prompt_md: Complete the direct yes/no question.",
            'prompt_md: "Complete each [BLANK] in the question."',
            1,
        )
        .replace(
            "  segments:\n    - blank_id: finite_verb",
            '  segments:\n    - text_md: "Item 1: "\n    - blank_id: finite_verb',
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert audit.status == "clean"
    assert audit.material_findings == []


def test_audit_source_directory_given_duplicate_yaml_key_expect_source_invalid(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "- handle: identify-question\n",
            "- handle: identify-question\n  handle: overwritten-question\n",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert audit.status == "invalid"
    assert any(finding.code == "exercise-source-invalid" for finding in audit.findings)


def test_audit_source_directory_given_malformed_marker_and_yaml_expect_marker_finding_first(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(lesson_path.read_text(encoding="utf-8") + "\n{{exercise: malformed\n", encoding="utf-8")
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8").replace(
            "- handle: identify-question\n",
            "- handle: identify-question\n  handle: overwritten-question\n",
            1,
        ),
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    codes = [finding.code for finding in audit.findings]
    assert codes.index("exercise-marker-malformed") < codes.index("exercise-source-invalid")


def test_audit_source_directory_given_matching_ascii_example_wrappers_expect_blocking_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8")
        + """
::: examples
- no: 'Hei.'
- en: 'Hello.'
- no: O'Leary kommer.
- en: O'Leary is coming.
- no: «Hei.»
- en: «Hello.»
:::
""",
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    wrappers = [finding for finding in audit.findings if finding.code == "typed-example-outer-quote-wrapper"]
    assert len(wrappers) == 1
    assert wrappers[0].severity == "blocking"
    assert audit.status == "blocked"


def test_audit_source_directory_given_internal_or_typographic_quotes_expect_no_wrapper_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8")
        + """
::: examples
- no: Han sa «hei».
- en: He said «hello».
- no: O'Leary kommer.
- en: O'Leary is coming.
:::
""",
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    assert not any(finding.code == "typed-example-outer-quote-wrapper" for finding in audit.findings)


def _copy_fixture(tmp_path: Path, fixture_root: Path = K_CATALOG_PACKAGE_FIXTURE_ROOT) -> Path:
    """Copy the fixture's authored source into a disposable test directory."""
    destination = tmp_path / "source"
    destination.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(fixture_root / name, destination / name)
    return destination


def _replace_norwegian_example_but(text: str) -> str:
    """Normalize any Norwegian example conjunction for negative cases."""
    return K_NORWEGIAN_EXAMPLE_BUT_RE.sub(r"\1men", text)


def _introduce_norwegian_example_but(text: str) -> str:
    """Seed the known Norwegian example defect for the positive audit case."""
    return K_NORWEGIAN_EXAMPLE_MEN_RE.sub(r"\1 but", text)


def test_audit_source_directory_given_visible_exact_speak_target_expect_no_speak_surface_finding(tmp_path: Path):
    source = _copy_fixture(tmp_path)
    lesson_path = source / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8") + "\n{{exercise: speak-exact}}\n",
        encoding="utf-8",
    )
    exercises_path = source / "exercises.yaml"
    exercises_path.write_text(
        exercises_path.read_text(encoding="utf-8")
        + """
- handle: speak-exact
  op: speak
  objective: obj-question-order
  bloom: apply
  prompt_md: In the morning, say exactly: Jeg star opp klokka sju.
  target: Jeg star opp klokka sju.
""",
        encoding="utf-8",
    )

    audit = audit_source_directory(source)

    codes = {finding.code for finding in audit.findings}
    assert "speak-target-cue-exact-missing" not in codes
    assert "speak-target-not-visible" not in codes


def test_audit_source_directory_given_find_fix_repair_and_explanation_requests_expect_capability_finding(
    tmp_path: Path,
):
    prompts = (
        "Find and fix the incorrect token.",
        "Find the token, then explain why the correction is needed and provide the corrected sentence.",
        "Fix the sentence.",
        "Change the token.",
        "Edit the answer.",
    )
    for index, prompt in enumerate(prompts):
        case_root = tmp_path / str(index)
        case_root.mkdir()
        source = _copy_fixture(case_root)
        exercises_path = source / "exercises.yaml"
        exercises_path.write_text(
            exercises_path.read_text(encoding="utf-8").replace(
                "prompt_md: Tap the token that makes the modal-verb phrase incorrect.",
                "prompt_md: " + prompt,
                1,
            ),
            encoding="utf-8",
        )

        audit = audit_source_directory(source)

        assert any(finding.code == "find-fix-attempt-capability" for finding in audit.findings)


def test_audit_source_directory_given_hidden_or_non_exact_speak_target_expect_surface_findings(tmp_path: Path):
    cases = (
        ("Read this sentence.", "Jeg star opp klokka sju.", "speak-target-cue-exact-missing"),
        ("Say exactly: Something else.", "Jeg star opp klokka sju.", "speak-target-not-visible"),
    )
    for index, (prompt, target, expected_code) in enumerate(cases):
        case_root = tmp_path / ("speak-" + str(index))
        case_root.mkdir()
        source = _copy_fixture(case_root)
        (source / "lesson.md").write_text(
            (source / "lesson.md").read_text(encoding="utf-8") + "\n{{exercise: speak-case}}\n",
            encoding="utf-8",
        )
        (source / "exercises.yaml").write_text(
            (source / "exercises.yaml").read_text(encoding="utf-8")
            + f"""
- handle: speak-case
  op: speak
  objective: obj-question-order
  bloom: apply
  prompt_md: "{prompt}"
  target: "{target}"
""",
            encoding="utf-8",
        )

        audit = audit_source_directory(source)

        assert expected_code in {finding.code for finding in audit.findings}
