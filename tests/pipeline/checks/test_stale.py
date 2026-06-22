import json
from pathlib import Path

from lesson_builder.pipeline.checks.validators.stale import requirements_hash, stale_check
from lesson_builder.pipeline.lesson_acceptance_log import latest_regression_baseline

ROOT = Path(__file__).resolve().parents[3]


def _requirements():
    return json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text())


def test_requirements_hash_given_same_data_expect_stable_hash():
    req = _requirements()
    assert requirements_hash(req) == requirements_hash(dict(req))


def test_stale_check_given_matching_hash_expect_no_results():
    req = _requirements()
    assert stale_check("ordinal_numbers", req, requirements_hash(req)) == []


def test_stale_check_given_different_hash_expect_warning():
    req = _requirements()
    results = stale_check("ordinal_numbers", req, "sha256:deadbeef")
    assert len(results) == 1
    assert results[0].check_id == "stale_check"
    assert results[0].severity == "warning"


def test_stale_check_given_no_recorded_hash_expect_no_results():
    req = _requirements()
    assert stale_check("ordinal_numbers", req, None) == []
    assert stale_check("ordinal_numbers", None, "sha256:deadbeef") == []


def test_stale_check_given_rebaselined_acceptance_entry_expect_no_results():
    req = _requirements()
    baseline = latest_regression_baseline(ROOT / "data" / "lesson_acceptance_log.jsonl", "ordinal_numbers")
    assert baseline is not None
    assert baseline.requirements_hash == requirements_hash(req)
    assert stale_check("ordinal_numbers", req, baseline.requirements_hash) == []
