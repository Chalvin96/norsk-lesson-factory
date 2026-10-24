from lesson_builder.domain.lesson.models.operations import eligible_operations


def test_eligible_operations_given_remember_expect_cross_kind_operations():
    assert set(eligible_operations(["remember"])) == {
        "recall_fill",
        "match_pairs",
    }


def test_eligible_operations_given_multiple_targets_expect_union_with_response_operations():
    ops = set(eligible_operations(["remember", "apply"]))
    assert {"match_pairs", "build", "write"}.issubset(ops)


def test_eligible_operations_given_unknown_bloom_expect_empty_result():
    assert eligible_operations([]) == []


def test_eligible_operations_given_bloom_only_policy_expect_write_at_apply():
    assert "write" in eligible_operations(["apply"])
    assert "write" not in eligible_operations(["remember"])
