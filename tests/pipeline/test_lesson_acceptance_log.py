from pathlib import Path

import pytest

from lesson_builder.pipeline.lesson_acceptance_log import (
    append_entry,
    latest_regression_baseline,
    mark_unverified,
    read_entries,
)

from .factories import LessonAcceptanceEntryFactory

ROOT = Path(__file__).resolve().parents[2]


def test_lesson_acceptance_log_given_appended_entry_expect_roundtrip_read(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"

    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build())
    entries = read_entries(acceptance_log)

    assert len(entries) == 1
    assert entries[0].slug == "past_tense"
    assert entries[0].status == "accepted"


def test_regression_baseline_given_imported_unverified_status_expect_excluded(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"

    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="imported_unverified"))
    first_match = latest_regression_baseline(acceptance_log, "past_tense")
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted", export_hash="sha256:ghi"))
    second_match = latest_regression_baseline(acceptance_log, "past_tense")

    assert first_match is None
    assert second_match is not None
    assert second_match.status == "accepted"


def test_mark_unverified_given_prior_accepted_entry_expect_baseline_demoted(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))

    mark_unverified(acceptance_log, "past_tense", reviewer="qa-bot", reason="dist import unverified")

    assert latest_regression_baseline(acceptance_log, "past_tense") is None
    entries = read_entries(acceptance_log)
    assert len(entries) == 2
    assert entries[-1].status == "imported_unverified"


def test_latest_regression_baseline_given_deferred_after_accepted_expect_accepted_still_baseline(
    tmp_path: Path,
):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="deferred"))

    baseline = latest_regression_baseline(acceptance_log, "past_tense")

    assert baseline is not None
    assert baseline.status == "accepted"


def test_mark_unverified_given_unknown_slug_expect_value_error(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"

    with pytest.raises(ValueError):
        mark_unverified(acceptance_log, "no_such_slug", reviewer="qa-bot", reason="missing baseline")


def test_mark_unverified_given_reason_expect_recorded_on_entry(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))

    mark_unverified(acceptance_log, "past_tense", reviewer="qa-bot", reason="needs human review")

    entries = read_entries(acceptance_log)
    assert entries[-1].blocking_issues == ["needs human review"]
    assert entries[-1].reviewer == "qa-bot"


def test_mark_unverified_given_prior_provenance_hashes_expect_carried_forward(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(
        acceptance_log,
        LessonAcceptanceEntryFactory.build(status="accepted", requirements_hash="sha256:reqs"),
    )

    mark_unverified(acceptance_log, "past_tense", reviewer="qa-bot", reason="needs human review")

    entries = read_entries(acceptance_log)
    assert entries[-1].requirements_hash == "sha256:reqs"


def test_read_entries_given_malformed_trailing_line_expect_valid_lines_still_read(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))
    with acceptance_log.open("a", encoding="utf-8") as fh:
        fh.write('{"slug": "past_tense", "status": "accep')  # torn trailing line, no fsync

    entries = read_entries(acceptance_log)

    assert len(entries) == 1
    assert entries[0].status == "accepted"


def test_read_entries_given_malformed_non_trailing_line_expect_raises(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))
    with acceptance_log.open("a", encoding="utf-8") as fh:
        fh.write('{"slug": "past_tense", "status": "accep\n')  # malformed, NOT trailing
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted"))

    with pytest.raises(ValueError):
        read_entries(acceptance_log)


def test_latest_regression_baseline_given_demote_then_reaccept_expect_newest_accepted(tmp_path: Path):
    acceptance_log = tmp_path / "acceptance.jsonl"
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted", export_hash="sha256:1"))
    mark_unverified(acceptance_log, "past_tense", reviewer="qa-bot", reason="needs re-review")
    append_entry(acceptance_log, LessonAcceptanceEntryFactory.build(status="accepted", export_hash="sha256:2"))

    baseline = latest_regression_baseline(acceptance_log, "past_tense")

    assert baseline is not None
    assert baseline.export_hash == "sha256:2"


def test_committed_lesson_acceptance_log_given_all_entries_expect_all_are_baselines():
    entries = read_entries(ROOT / "data" / "lesson_acceptance_log.jsonl")

    assert len(entries) == 104
    assert all(e.usable_as_regression_baseline() for e in entries)
    statuses = {e.status for e in entries}
    assert statuses == {"accepted", "curated"}
