"""Entry point: round-trip authored YAML scalar edit behavior tests."""

import yaml

from lesson_builder.formats.workbench_curriculum import insert_yaml_sequence_item
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_scalars
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_sequence_items


def test_yaml_rewrite_given_comments_quotes_and_multiline_values_expect_unrelated_bytes_retained() -> None:
    source = "# heading\r\nrun_id: 'unchanged' # keep\r\nslots:\r\n- title: 'Old title' # title note\r\n  learner_outcome: A long\r\n    outcome here.\r\n  sequence_index: 0 # position\r\n"
    changed = rewrite_yaml_scalars(source, {("slots", 0, "sequence_index"): 1})
    assert changed == source.replace("sequence_index: 0", "sequence_index: 1")
    edited = rewrite_yaml_scalars(
        changed, {("slots", 0, "title"): "Title: user's choice", ("slots", 0, "learner_outcome"): "Say a phrase."}
    )
    assert yaml.safe_load(edited)["slots"][0] == {
        "title": "Title: user's choice",
        "learner_outcome": "Say a phrase.",
        "sequence_index": 1,
    }
    assert "# title note\r\n" in edited and "run_id: 'unchanged' # keep\r\n" in edited


def test_yaml_rewrite_given_long_outcome_expect_valid_slot_indentation() -> None:
    outcome = " ".join(["outcome"] * 100)
    result = rewrite_yaml_scalars(
        "slots:\n- learner_outcome: Old outcome\n  sequence_index: 0\n", {("slots", 0, "learner_outcome"): outcome}
    )
    assert yaml.safe_load(result)["slots"][0]["learner_outcome"] == outcome


def test_yaml_rewrite_given_block_scalar_outcome_expect_valid_replacement_and_following_field_retained() -> None:
    source = "slots:\n- learner_outcome: |\n    Say a phrase.\n    Ask a question.\n  sequence_index: 0 # keep\n"
    result = rewrite_yaml_scalars(source, {("slots", 0, "learner_outcome"): "Say something.\nThen listen."})
    assert yaml.safe_load(result)["slots"][0]["learner_outcome"] == "Say something.\nThen listen."
    assert result.endswith("  sequence_index: 0 # keep\n")


def test_yaml_insertion_given_wrapped_strings_nulls_and_next_field_expect_only_new_entry_lines() -> None:
    source = "# heading\nentries:\n- id: first\n  title: A very long title which was\n    deliberately wrapped\n  owner_scope: null # retain spelling\n- id: second\n  owner_scope: null\nnext_field: unchanged\n"
    added = {"id": "new", "title": "Title: with colon", "owner_scope": None}
    middle = insert_yaml_sequence_item(source, "entries", added, 1)
    assert middle == source.replace(
        "- id: second", "- id: new\n  title: 'Title: with colon'\n  owner_scope:\n- id: second"
    )
    end = insert_yaml_sequence_item(source, "entries", added, 2)
    assert end == source.replace("next_field:", "- id: new\n  title: 'Title: with colon'\n  owner_scope:\nnext_field:")
    assert yaml.safe_load(end)["entries"][-1] == added


def test_yaml_insertion_given_no_final_newline_expect_separate_new_entry_and_existing_bytes_retained() -> None:
    source = "entries:\n- id: first"
    result = insert_yaml_sequence_item(source, "entries", {"id": "second"}, 1)
    assert result == source + "\n- id: second\n"
    assert yaml.safe_load(result)["entries"] == [{"id": "first"}, {"id": "second"}]


def test_yaml_sequence_rewrite_given_reordered_updated_and_new_entries_expect_authored_order_and_retained_comments() -> (
    None
):
    source = (
        "# heading\nentries:\n"
        "- id: first\n  title: 'First' # keep first comment\n  learner_outcome: First outcome\n"
        "- id: second\n  title: Second\n  learner_outcome: Second outcome\n"
        "next_field: unchanged\n"
    )
    items = [
        {"id": "second", "title": "Second edited", "learner_outcome": "Second outcome"},
        {"id": "new", "title": "New", "learner_outcome": "New outcome"},
        {"id": "first", "title": "First", "learner_outcome": "First outcome"},
    ]

    result = rewrite_yaml_sequence_items(source, "entries", items, "id")

    assert yaml.safe_load(result)["entries"] == items
    assert "title: 'First' # keep first comment\n" in result
    assert result.index("id: second") < result.index("id: new") < result.index("id: first")
    assert result.endswith("next_field: unchanged\n")


def test_yaml_sequence_rewrite_given_no_entries_expect_empty_list_and_unrelated_source_retained() -> None:
    source = "# heading\nslots: # preserve note\n- catalog_id: first\n  title: First\nsummary:\n  count: 1\n"

    result = rewrite_yaml_sequence_items(source, "slots", [], "catalog_id")

    assert yaml.safe_load(result) == {"slots": [], "summary": {"count": 1}}
    assert "slots: [] # preserve note\n" in result
    assert result.endswith("summary:\n  count: 1\n")


def test_yaml_sequence_rewrite_given_empty_flow_sequence_and_new_entry_expect_valid_block_sequence() -> None:
    source = "slots: [] # preserve note\nsummary:\n  count: 1\n"
    item = {"catalog_id": "first", "title": "First"}

    result = rewrite_yaml_sequence_items(source, "slots", [item], "catalog_id")

    assert yaml.safe_load(result) == {"slots": [item], "summary": {"count": 1}}
    assert "slots: # preserve note\n" in result
