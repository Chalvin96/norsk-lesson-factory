"""Tests for the catalog-package human-gate router and thread-id helper."""

from __future__ import annotations

from lesson_builder.workflow.lesson_generation.state import K_LESSON_GENERATION_NODE_HUMAN_GATE
from lesson_builder.workflow.lesson_generation.state import K_ROUTE_END
from lesson_builder.workflow.lesson_generation.state import K_ROUTE_FINALIZE
from lesson_builder.workflow.lesson_generation.state import route_after_human
from lesson_builder.workflow.lesson_generation.state import thread_id_for


def test_route_after_human_given_accept_decision_expect_finalize():
    state = {"human_decision": {"status": "accept"}}

    assert route_after_human(state) == K_ROUTE_FINALIZE  # type: ignore[arg-type]


def test_route_after_human_given_defer_decision_expect_end():
    state = {"human_decision": {"status": "defer"}}

    assert route_after_human(state) == K_ROUTE_END  # type: ignore[arg-type]


def test_route_after_human_given_reject_decision_expect_end():
    state = {"human_decision": {"status": "reject"}}

    assert route_after_human(state) == K_ROUTE_END  # type: ignore[arg-type]


def test_route_after_human_given_missing_decision_expect_repark():
    state = {"human_decision": None}

    assert route_after_human(state) == K_LESSON_GENERATION_NODE_HUMAN_GATE  # type: ignore[arg-type]


def test_route_after_human_given_unknown_status_expect_repark():
    state = {"human_decision": {"status": "bogus"}}

    assert route_after_human(state) == K_LESSON_GENERATION_NODE_HUMAN_GATE  # type: ignore[arg-type]


def test_thread_id_for_given_plain_run_id_expect_prefixed():
    assert thread_id_for("abc123") == "catalog_generation:abc123"


def test_thread_id_for_given_already_prefixed_run_id_expect_idempotent():
    assert thread_id_for("catalog_generation:abc123") == "catalog_generation:abc123"
