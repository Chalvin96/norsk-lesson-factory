"""Entry point: behavioral tests for typed checkpoint splitting."""

import pytest
import yaml

from lesson_builder.workflow.lesson_generation.checkpoints import assert_checkpoint_metadata_absent
from lesson_builder.workflow.lesson_generation.checkpoints import split_checkpoint_directives
from lesson_builder.workflow.lesson_generation.checkpoints import validate_routed_checkpoint_requests


def test_split_checkpoint_directives_given_typed_intents_expect_markers_and_frozen_order():
    draft = """Teaching one.

{{checkpoint
handle: first-check
objective_ref: obj-one
bloom: understand
evidence: Learner distinguishes the two taught meanings.
}}

Teaching two.

{{checkpoint
handle: second-check
objective_ref: obj-one
bloom: apply
evidence: Learner constructs the taught form for a new situation.
}}
"""

    result = split_checkpoint_directives(draft)

    assert result.prose_md == (
        "Teaching one.\n\n{{exercise: first-check}}\n\nTeaching two.\n\n{{exercise: second-check}}\n"
    )
    assert [item["handle"] for item in yaml.safe_load(result.intents_yaml)] == [
        "first-check",
        "second-check",
    ]
    assert "checkpoint" not in result.prose_md.lower()


def test_split_checkpoint_directives_given_legacy_prose_token_expect_rejection_before_normalization():
    draft = "Checkpoint choose-item [objective obj-one, bloom apply]: Learner constructs one request.\n"

    with pytest.raises(ValueError, match="prose-shaped checkpoint token"):
        split_checkpoint_directives(draft)


def test_split_checkpoint_directives_given_malformed_directive_expect_rejection():
    draft = "{{checkpoint\nhandle: choose-item\nobjective_ref: obj-one\n}}"

    with pytest.raises(ValueError, match="malformed checkpoint directive"):
        split_checkpoint_directives(draft)


def test_validate_routed_checkpoint_requests_given_only_route_added_expect_acceptance():
    intents = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence: Learner constructs one complete request.
"""
    requests = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence_route: sentence_construction
  evidence: Learner constructs one complete request.
"""

    validate_routed_checkpoint_requests(requests, intents)


def test_validate_routed_checkpoint_requests_given_changed_evidence_expect_rejection():
    intents = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence: Learner constructs one complete request.
"""
    requests = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence_route: sentence_construction
  evidence: Learner merely recognizes one request.
"""

    with pytest.raises(ValueError, match="changed authored checkpoint intent"):
        validate_routed_checkpoint_requests(requests, intents)


def test_validate_routed_checkpoint_requests_given_extra_field_expect_rejection():
    intents = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence: Learner constructs one complete request.
"""
    requests = """
- handle: choose-item
  objective_ref: obj-one
  bloom: apply
  evidence_route: sentence_construction
  evidence: Learner constructs one complete request.
  instructions: Internal activity prose must not cross this boundary.
"""

    with pytest.raises(ValueError, match="may add only evidence_route"):
        validate_routed_checkpoint_requests(requests, intents)


def test_assert_checkpoint_metadata_absent_given_internal_directive_expect_rejection():
    with pytest.raises(ValueError, match="internal checkpoint metadata"):
        assert_checkpoint_metadata_absent("{{checkpoint\nhandle: choose-item\n}}")
