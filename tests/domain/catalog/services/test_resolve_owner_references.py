"""Behavior tests for historical-to-active owner reference resolution."""

from lesson_builder.domain.catalog.services.resolve_owner_references import build_owner_resolution


def test_build_owner_resolution_given_merged_and_deleted_ids_expect_safe_mapping() -> None:
    resolution = build_owner_resolution(
        {
            "entries": [
                {
                    "id": "survivor",
                    "source_owners": ["old_owner"],
                    "merged_from": ["merged_owner"],
                }
            ],
            "summary": {"deleted_owner_ids": ["deleted_owner"]},
        }
    )

    assert resolution.resolve("old_owner") == "survivor"
    assert resolution.resolve("legacy__merged_owner") == "survivor"
    assert resolution.resolve("deleted_owner") is None
    assert resolution.resolve("unknown_owner") is None
