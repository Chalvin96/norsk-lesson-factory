"""Entry point: parser-backed rendered comparison behavior tests."""

import pytest

from lesson_builder.formats.workbench_proposal import render_proposal_text


def test_proposal_text_given_reading_fence_and_attributes_expect_language_scoped_body_and_translation() -> None:
    regions = render_proposal_text(
        '::: {.reading speaker_name="Mina" translation="Hello there."}\n**Hei** [der]{lang=nb}!\n:::\n'
    )
    assert [(region.text, region.lang) for region in regions] == [
        ("Mina", "en"),
        ("Hei der!", "nb"),
        ("Hello there.", "en"),
    ]


def test_proposal_text_given_yaml_item_expect_authored_text_without_schema_keys() -> None:
    regions = render_proposal_text(
        "- handle: greeting\n  operation: translate\n  prompt: Say **hello**.\n  explanation: Use a greeting.\n",
        kind="exercise",
    )
    assert [region.text for region in regions] == ["Say hello.", "Use a greeting."]


def test_proposal_text_given_nested_callout_and_example_expect_readable_content_without_fences() -> None:
    regions = render_proposal_text("::: {.callout}\nBe **polite**.\n\n::: {.example}\nno: Hei!\nen: Hello!\n:::\n:::\n")
    text = " ".join(region.text for region in regions)
    assert "Be polite." in text
    assert "Hei!" in text
    assert ":::" not in text
    assert ".callout" not in text


def test_proposal_text_given_exercise_dialogue_and_matching_sides_expect_authored_languages_without_provenance() -> (
    None
):
    regions = render_proposal_text(
        "op: match_pairs\nprompt_md: Match lines.\nstimulus:\n- kind: dialogue\n  turns:\n  - speaker: Mina\n    text_md: Hei!\nleft:\n- left_id: hello\n  text: God dag.\nright:\n- right_id: greeting\n  text: A greeting.\nderived_from:\n- section_id: introduction\n",
        kind="exercise",
    )
    assert [(item.text, item.lang) for item in regions] == [
        ("Match lines.", "en"),
        ("Mina", "en"),
        ("Hei!", "nb"),
        ("God dag.", "nb"),
        ("A greeting.", "en"),
    ]


@pytest.mark.parametrize(
    ("source", "changed", "language"),
    [
        ("op: recall_fill\nsegments:\n- kind: blank\n  options: [Hei, old]\n  answer_index: 0\n", "new", "nb"),
        ("op: categorize\nbuckets:\n- bucket_id: b\n  label: old\n", "new", "en"),
        ("op: categorize\nitems:\n- item_id: i\n  text: old\n", "new", "nb"),
        ("op: judge\nsentence_md: old\n", "new", "nb"),
        ("op: judge\nfeedback: old\n", "new", "en"),
        ("op: choose\nstem_md: old\n", "new", "en"),
        ("op: write\njudge_prompt: old\n", "new", "en"),
        ("op: write\ncriteria:\n- id: c\n  instruction: old\n", "new", "en"),
    ],
)
def test_proposal_text_given_operation_specific_text_edit_expect_visible_changed_text_in_authored_language(
    source: str, changed: str, language: str
) -> None:
    before = render_proposal_text(source, kind="exercise")
    after = render_proposal_text(source.replace("old", changed), kind="exercise")
    assert before != after
    assert (changed, language) in [(item.text, item.lang) for item in after]


def test_proposal_text_given_reordered_yaml_fields_expect_identical_rendered_order() -> None:
    before = "op: write\nprompt_md: Reply.\nstimulus:\n- kind: dialogue\n  turns:\n  - speaker: Mina\n    text_md: Hei!\njudge_prompt: Check the reply.\ncriteria:\n- id: greeting\n  instruction: Include a greeting.\n"
    after = "criteria:\n- instruction: Include a greeting.\n  id: greeting\njudge_prompt: Check the reply.\nop: write\nstimulus:\n- turns:\n  - text_md: Hei!\n    speaker: Mina\n  kind: dialogue\nprompt_md: Reply.\n"
    assert render_proposal_text(before, kind="exercise") == render_proposal_text(after, kind="exercise")


def test_proposal_text_given_audio_target_with_paired_blanks_expect_literal_underscores() -> None:
    regions = render_proposal_text('audio_target: "___ jeg kommer hjem. ___ jeg drar."\n', kind="exercise")
    assert [(region.text, region.lang) for region in regions] == [("___ jeg kommer hjem. ___ jeg drar.", "nb")]
