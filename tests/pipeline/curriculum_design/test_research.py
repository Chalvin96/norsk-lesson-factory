"""Tests for the curriculum_design research stage (Phase 1).

Entry points: ``researcher``, ``resolve_citations``, ``unresolved``.

Offline: injects a fake ``ResearchBackend`` returning canned ``ResearchNote``
lists, and a fake ``fetch`` for the citation resolver (200 + quote present /
404 / quote-absent cases). No live web is exercised.
"""

from __future__ import annotations

import pytest

from lesson_builder.pipeline.curriculum_design.models import ResearchNote
from lesson_builder.pipeline.curriculum_design.research import (
    researcher,
    resolve_citations,
    unresolved,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def _note(
    claim: str = "a claim",
    url: str = "https://example.com/a",
    quote: str = "quoted text",
    resolved: bool = False,
) -> ResearchNote:
    return ResearchNote(claim=claim, url=url, quote=quote, resolved=resolved)


def test_researcher_given_canned_backend_expect_notes_returned():
    # setup: a fake backend returning two canned notes.
    canned = [
        _note(claim="past tense formation", url="https://a.example", quote="past tense"),
        _note(claim="definite articles", url="https://b.example", quote="den, det"),
    ]

    def backend(query: str) -> list[ResearchNote]:
        assert query == "norwegian grammar basics"
        return canned

    # execute
    result = researcher("norwegian grammar basics", backend=backend)

    # assert
    assert result == canned


def test_researcher_given_no_backend_wired_expect_hard_fail():
    # setup + execute + assert: no backend wired -> hard-fail, NEVER silent fallback.
    with pytest.raises(RuntimeError, match="no research backend wired"):
        researcher("any seed")


def test_resolve_citations_given_200_and_quote_present_expect_resolved():
    # setup: fetch returns 200 with the quote in the page text.
    notes = [_note(claim="c1", url="https://a.example", quote="the past tense")]

    def fetch(url: str) -> _FakeResponse:
        assert url == "https://a.example"
        return _FakeResponse(200, "page body the past tense appears here")

    # execute
    resolved = resolve_citations(notes, fetch=fetch)

    # assert
    assert resolved[0].resolved is True


def test_resolve_citations_given_404_expect_unresolved():
    # setup: fetch returns 404.
    notes = [_note(url="https://gone.example", quote="something")]

    def fetch(url: str) -> _FakeResponse:
        return _FakeResponse(404, "not found")

    # execute
    resolved = resolve_citations(notes, fetch=fetch)

    # assert
    assert resolved[0].resolved is False


def test_resolve_citations_given_200_but_quote_absent_expect_unresolved():
    # setup: 200 OK but the quoted text is NOT in the page body.
    notes = [_note(url="https://a.example", quote="the exact quoted snippet")]

    def fetch(url: str) -> _FakeResponse:
        return _FakeResponse(200, "page body that does not contain the quote")

    # execute
    resolved = resolve_citations(notes, fetch=fetch)

    # assert: a resolvable-but-wrong URL must not pass.
    assert resolved[0].resolved is False


def test_resolve_citations_given_fetch_raises_expect_unresolved():
    # setup: fetch raises (network error, DNS failure, etc.).
    notes = [_note(url="https://broken.example", quote="x")]

    def fetch(url: str) -> _FakeResponse:
        raise ConnectionError("DNS resolution failed")

    # execute
    resolved = resolve_citations(notes, fetch=fetch)

    # assert
    assert resolved[0].resolved is False


def test_resolve_citations_given_mixed_notes_expect_per_note_resolution():
    # setup: three notes — corroborated, 404, quote-absent.
    notes = [
        _note(claim="ok", url="https://ok.example", quote="present"),
        _note(claim="gone", url="https://gone.example", quote="missing"),
        _note(claim="wrong", url="https://wrong.example", quote="not in page"),
    ]

    def fetch(url: str) -> _FakeResponse:
        if url == "https://ok.example":
            return _FakeResponse(200, "text with present in it")
        if url == "https://gone.example":
            return _FakeResponse(404, "not found")
        return _FakeResponse(200, "page body without the quote")

    # execute
    resolved = resolve_citations(notes, fetch=fetch)

    # assert
    assert [n.resolved for n in resolved] == [True, False, False]


def test_unresolved_given_mixed_notes_expect_only_unresolved_returned():
    # setup
    notes = [
        _note(claim="ok", resolved=True),
        _note(claim="bad", resolved=False),
    ]

    # execute
    result = unresolved(notes)

    # assert
    assert len(result) == 1
    assert result[0].claim == "bad"
