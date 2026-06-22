"""Entry point: ``researcher`` / ``resolve_citations`` / ``unresolved``.

The research stage of the curriculum_design loop (Phase 1). Mirrors the
pipeline's DI discipline: a ``ResearchBackend`` Protocol is the single seam
between the loop and any live web search; tests inject a fake backend so the
suite stays offline. At runtime a real web backend is wired in, but if NONE is
wired ``researcher`` HARD-FAILS — it never silently falls back to model memory
(that would reintroduce the wrong-paradigm risk the plan calls out).

``resolve_citations`` is the citation-truth layer: each cited URL is fetched
and the quoted text asserted present in the page body. An uncorroborated note
is marked ``resolved=False`` and droppable by the caller so a
resolvable-but-wrong URL cannot pass.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from lesson_builder.pipeline.curriculum_design.models import ResearchNote

K_RESEARCH_FETCH_TIMEOUT = 20.0


@runtime_checkable
class ResearchBackend(Protocol):
    """A callable research backend: ``query`` -> cited ``ResearchNote`` list."""

    def __call__(self, query: str) -> list[ResearchNote]: ...


# A fetch callable: URL -> httpx.Response (or any object with status_code + text).
FetchFn = Callable[[str], Any]


def researcher(
    query: str,
    *,
    backend: ResearchBackend | None = None,
) -> list[ResearchNote]:
    """Run research for ``query`` via ``backend``.

    If ``backend`` is ``None`` the default real web backend is resolved; if none
    is wired this HARD-FAILS with a clear message — NEVER silently falls back to
    model memory. Tests inject a fake ``backend`` so they never hit this path.
    """
    resolved_backend = backend if backend is not None else _default_research_backend()
    if resolved_backend is None:
        raise RuntimeError(
            "no research backend wired: pass backend=<callable(query)->list[ResearchNote]> "
            "explicitly; curriculum_design never silently falls back to model memory"
        )
    notes = resolved_backend(query)
    return list(notes)


def resolve_citations(
    notes: Sequence[ResearchNote],
    *,
    fetch: FetchFn | None = None,
) -> list[ResearchNote]:
    """Fetch each note's URL; mark ``resolved`` only if 200 AND ``quote`` is in the page text.

    ``fetch`` defaults to ``httpx.get`` (inject for tests). Network errors,
    non-200 responses, or a missing quote all mark the note ``resolved=False``
    so the caller can drop it (blocks promotion). A note that was already
    resolved stays resolved only if it re-corroborates.
    """
    fetcher = fetch if fetch is not None else _default_fetch
    resolved_notes: list[ResearchNote] = []
    for note in notes:
        resolved_notes.append(_resolve_one(note, fetcher))
    return resolved_notes


def unresolved(notes: Sequence[ResearchNote]) -> list[ResearchNote]:
    """Return the subset of ``notes`` whose citation did not corroborate."""
    return [note for note in notes if not note.resolved]


def _resolve_one(note: ResearchNote, fetcher: FetchFn) -> ResearchNote:
    try:
        response = fetcher(note.url)
    except Exception:
        return note.model_copy(update={"resolved": False})
    status_code = getattr(response, "status_code", 0)
    text = getattr(response, "text", "") or ""
    corroborated = status_code == 200 and note.quote in text
    return note.model_copy(update={"resolved": corroborated})


def _default_fetch(url: str) -> httpx.Response:
    return httpx.get(url, timeout=K_RESEARCH_FETCH_TIMEOUT, follow_redirects=True)


def _default_research_backend() -> ResearchBackend | None:
    """Resolve the real web research backend, or ``None`` if none is wired.

    At runtime this would construct a live WebSearch/WebFetch adapter; in
    offline/test contexts it returns ``None`` so ``researcher`` hard-fails
    rather than silently falling back to model memory.
    """
    return None


__all__ = [
    "FetchFn",
    "ResearchBackend",
    "resolve_citations",
    "researcher",
    "unresolved",
]
