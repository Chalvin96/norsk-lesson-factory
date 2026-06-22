from lesson_builder.schema.selection import (
    MIN_EXERCISES_PER_OBJECTIVE,
    eligible_operations,
)


def test_min_exercises_default():
    assert MIN_EXERCISES_PER_OBJECTIVE >= 1


def test_remember_eligible_ops():
    assert set(eligible_operations(["remember"])) == {"recall_fill", "match_pairs"}


def test_multiple_targets_union():
    ops = set(eligible_operations(["remember", "apply"]))
    assert "match_pairs" in ops and "build" in ops


def test_unknown_bloom_ignored():
    assert eligible_operations([]) == []
