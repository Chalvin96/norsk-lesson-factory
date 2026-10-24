"""Approved catalog authoring invariant tests."""

import re
from pathlib import Path

import yaml

K_CATALOG_SPOKEN_PRODUCTION_PATTERN = re.compile(
    r"\b(produce|pronounce|say|speak|aloud|out loud|repeat|use a natural pitch|"
    r"put (contrastive )?prominence|keep .* together)\b",
    re.IGNORECASE,
)


def test_approved_catalog_given_pronunciation_owners_expect_no_spoken_production_outcomes() -> None:
    root = Path(__file__).resolve().parents[3]
    catalog = yaml.safe_load((root / "content/catalog/approved/catalog.yaml").read_text(encoding="utf-8"))
    violations = []

    for entry in catalog["entries"]:
        if entry["catalog_kind"] != "pronunciation":
            continue
        fields = [("learner_outcome", entry["learner_outcome"])]
        fields.extend(("teaching_points[].statement", point["statement"]) for point in entry["teaching_points"])
        violations.extend(
            (entry["id"], field, text) for field, text in fields if K_CATALOG_SPOKEN_PRODUCTION_PATTERN.search(text)
        )

    assert not violations, f"pronunciation outcomes describe spoken production: {violations!r}"
