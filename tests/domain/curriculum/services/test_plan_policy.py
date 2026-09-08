"""Entry point: curriculum plan policy behavior tests."""

import pytest

from lesson_builder.domain.curriculum.models import CurriculumSequence
from lesson_builder.domain.curriculum.services.plan_policy import build_plan_slots
from lesson_builder.domain.curriculum.services.plan_policy import build_sequence_report
from lesson_builder.domain.curriculum.services.plan_policy import hash_catalog_content
from lesson_builder.domain.curriculum.services.plan_policy import hash_sequence_content
from lesson_builder.domain.curriculum.services.plan_policy import normalize_catalog_kinds
from lesson_builder.domain.curriculum.services.plan_policy import require_complete_dependency_graph


def test_normalize_catalog_kinds_given_duplicate_whitespace_and_reordered_input_expect_canonical_order() -> None:
    assert normalize_catalog_kinds((" communicative ", "grammar", "grammar")) == ("grammar", "communicative")


def test_normalize_catalog_kinds_given_empty_input_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="included_kinds must contain at least one catalog kind"):
        normalize_catalog_kinds(())


def test_build_plan_slots_given_required_dependency_expect_topological_slots_and_edge_count() -> None:
    slots, edge_counts = build_plan_slots(
        [
            {
                "id": "dependent",
                "catalog_kind": "grammar",
                "title": "Dependent",
                "cefr_tags": ["A1"],
                "prerequisites": ["prerequisite"],
                "helpful_prerequisites": [],
            },
            {
                "id": "prerequisite",
                "catalog_kind": "grammar",
                "title": "Prerequisite",
                "cefr_tags": ["A1"],
                "prerequisites": [],
                "helpful_prerequisites": [],
            },
        ],
        registry=None,
        included_kinds={"grammar"},
    )

    assert [slot.catalog_id for slot in slots] == ["prerequisite", "dependent"]
    assert edge_counts == {"required": 1, "helpful": 0}


def test_build_plan_slots_given_required_dependency_in_excluded_kind_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="requires deferred catalog ID"):
        build_plan_slots(
            [
                {
                    "id": "grammar_one",
                    "catalog_kind": "grammar",
                    "cefr_tags": ["A1"],
                    "prerequisites": ["writing_one"],
                    "helpful_prerequisites": [],
                },
                {
                    "id": "writing_one",
                    "catalog_kind": "writing",
                    "cefr_tags": ["A2"],
                    "prerequisites": [],
                    "helpful_prerequisites": [],
                },
            ],
            registry=None,
            included_kinds={"grammar"},
        )


def test_hash_catalog_content_given_mapping_with_different_key_order_expect_same_hash() -> None:
    assert hash_catalog_content({"entries": [], "snapshot": "test"}) == hash_catalog_content(
        {"snapshot": "test", "entries": []}
    )


def test_require_complete_dependency_graph_given_pending_graph_expect_review_gate_error() -> None:
    with pytest.raises(ValueError, match="dependency_graph is not complete"):
        require_complete_dependency_graph(
            {
                "schema_version": 3,
                "dependency_graph": {"status": "pending"},
                "entries": [],
            }
        )


def test_build_plan_slots_given_preferred_a1_sequence_expect_preference_with_required_edges_preserved() -> None:
    sequence = CurriculumSequence.model_validate(
        {
            "schema_version": 1,
            "entries": [
                {"lesson_id": "question", "rationale": "Ask early."},
                {"lesson_id": "greeting", "rationale": "Open with an exchange."},
            ],
        }
    )
    slots, _ = build_plan_slots(
        [
            {
                "id": "greeting",
                "catalog_kind": "communicative",
                "cefr_tags": ["A1"],
                "prerequisites": [],
                "helpful_prerequisites": [],
            },
            {
                "id": "question",
                "catalog_kind": "grammar",
                "cefr_tags": ["A1"],
                "prerequisites": ["clause"],
                "helpful_prerequisites": [],
            },
            {
                "id": "clause",
                "catalog_kind": "grammar",
                "cefr_tags": ["A1"],
                "prerequisites": [],
                "helpful_prerequisites": [],
            },
        ],
        registry=None,
        included_kinds={"grammar", "communicative"},
        sequence=sequence,
    )

    assert [slot.catalog_id for slot in slots] == ["greeting", "clause", "question"]


def test_build_plan_slots_given_invalid_sequence_identity_expect_actionable_error() -> None:
    sequence = CurriculumSequence.model_validate(
        {"schema_version": 1, "entries": [{"lesson_id": " unknown ", "rationale": "Review."}]}
    )

    with pytest.raises(ValueError, match="surrounding whitespace"):
        build_plan_slots(
            [
                {
                    "id": "known",
                    "catalog_kind": "grammar",
                    "cefr_tags": ["A1"],
                    "prerequisites": [],
                    "helpful_prerequisites": [],
                }
            ],
            registry=None,
            included_kinds={"grammar"},
            sequence=sequence,
        )


def test_build_plan_slots_given_duplicate_sequence_identity_expect_actionable_error() -> None:
    sequence = CurriculumSequence.model_validate(
        {
            "schema_version": 1,
            "entries": [
                {"lesson_id": "known", "rationale": "First."},
                {"lesson_id": "known", "rationale": "Again."},
            ],
        }
    )

    with pytest.raises(ValueError, match="duplicate lesson ID"):
        build_plan_slots(
            [_sequence_catalog_entry("known")],
            registry=None,
            included_kinds={"grammar"},
            sequence=sequence,
        )


def test_build_plan_slots_given_unknown_sequence_identity_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="unknown catalog ID"):
        build_plan_slots(
            [_sequence_catalog_entry("known")],
            registry=None,
            included_kinds={"grammar"},
            sequence=_sequence_entry("missing"),
        )


def test_build_plan_slots_given_non_a1_sequence_identity_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="must be an A1"):
        build_plan_slots(
            [_sequence_catalog_entry("advanced", cefr_tags=["A2"])],
            registry=None,
            included_kinds={"grammar"},
            sequence=_sequence_entry("advanced"),
        )


def test_build_plan_slots_given_deferred_sequence_identity_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="deferred"):
        build_plan_slots(
            [_sequence_catalog_entry("writing_lesson", catalog_kind="writing")],
            registry=None,
            included_kinds={"grammar"},
            sequence=_sequence_entry("writing_lesson"),
        )


def test_build_plan_slots_given_unsupported_sequence_version_expect_actionable_error() -> None:
    with pytest.raises(ValueError, match="unsupported curriculum sequence schema_version"):
        build_plan_slots(
            [_sequence_catalog_entry("known")],
            registry=None,
            included_kinds={"grammar"},
            sequence=_sequence_entry("known", schema_version=2),
        )


def test_build_sequence_report_given_helpful_dependency_later_expect_advisory_report() -> None:
    sequence = CurriculumSequence.model_validate(
        {"schema_version": 1, "entries": [{"lesson_id": "transaction", "rationale": "Use it early."}]}
    )
    entries = [
        {
            "id": "numbers",
            "catalog_kind": "grammar",
            "cefr_tags": ["A1"],
            "prerequisites": [],
            "helpful_prerequisites": [],
        },
        {
            "id": "transaction",
            "catalog_kind": "communicative",
            "cefr_tags": ["A1"],
            "prerequisites": [],
            "helpful_prerequisites": ["numbers"],
        },
    ]

    report = build_sequence_report(entries, sequence=sequence, included_kinds={"grammar", "communicative"})

    assert report[0].status == "placed"
    assert report[0].helpful_prerequisites_later == ["numbers"]


def test_hash_sequence_content_given_absent_and_explicit_empty_input_expect_distinct_provenance() -> None:
    assert hash_sequence_content(None) != hash_sequence_content({"schema_version": 1, "entries": []})


def _sequence_entry(lesson_id: str, *, schema_version: int = 1) -> CurriculumSequence:
    return CurriculumSequence.model_validate(
        {
            "schema_version": schema_version,
            "entries": [{"lesson_id": lesson_id, "rationale": "Review this opening preference."}],
        }
    )


def _sequence_catalog_entry(
    lesson_id: str, *, catalog_kind: str = "grammar", cefr_tags: list[str] | None = None
) -> dict[str, object]:
    return {
        "id": lesson_id,
        "catalog_kind": catalog_kind,
        "cefr_tags": cefr_tags or ["A1"],
        "prerequisites": [],
        "helpful_prerequisites": [],
    }
