"""Entry point: ``run_repl``.

Conversational terminal REPL over the chat controller. Mirrors what the LAN
FastAPI server does, but on stdin/stdout so the operator can drive the lesson
workflow from a terminal (including over ssh) without binding a network port.

Reuses ``new_session`` and ``handle_chat_message`` from ``controller`` verbatim.
The controller's intent router is the single source of truth for what each
message does; this module only formats output and manages the read/print loop.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

from lesson_builder.chat.controller import (
    _match_verb_slug,
    handle_chat_message,
    new_session,
)
from lesson_builder.chat.lesson_view import render_lesson
from lesson_builder.pipeline.graph_runner import ThreadNotFoundError, ThreadNotParkedError

K_PROMPT = "> "
K_BANNER = "lesson-data chat. Type 'help' for capabilities, 'quit' or Ctrl-D to exit."
K_EXIT_COMMANDS = ("quit", "exit")
K_HELP_COMMAND = "help"
K_LIST_COLUMNS = 3
K_FIND_PREFIXES = ("find ", "lessons matching ")
# Leading filler words that indicate a natural-language request (e.g.
# "find me a lesson on adjectives") rather than a bare keyword filter. When
# present, the message must fall through to the LLM router instead of being
# swallowed by the local substring filter.
K_FIND_FILLER_PREFIXES = ("me ", "a ", "an ", "the ", "all ", "some ", "any ")
# Longest verb first so "show lesson X" wins over "show X". The shared
# ``_match_verb_slug`` helper appends the trailing space when matching.
K_SHOW_VERBS = ("show lesson", "show")

K_CAPABILITY_EXAMPLES = (
    ("see the lessons", "what lessons exist?"),
    ("pick a lesson", "use ordinal_numbers"),
    ("improve a lesson", "add an exercise to ordinal_numbers"),
    ("run QA", "run QA on ordinal_numbers"),
    ("show the latest thread", "show the latest thread"),
    ("accept or defer", "accept the latest parked thread / defer it"),
    ("filter lessons locally", "find adjective   (or: lessons matching adjective)"),
    ("read a lesson's content", "show adjective_agreement"),
    ("see this help", "what can you do?"),
)


def run_repl(repo_root: Path) -> None:
    """Read lines from stdin, dispatch through the controller, print compact output."""
    session = new_session()
    print(K_BANNER)
    while True:
        try:
            line = input(K_PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        message = line.strip()
        if not message:
            continue
        lowered = message.lower()
        if lowered in K_EXIT_COMMANDS:
            return
        if lowered == K_HELP_COMMAND:
            _print_help()
            continue
        find_query = _match_find_shortcut(lowered)
        if find_query is not None:
            _print_find_results(repo_root, find_query)
            continue
        show_slug = _match_show_shortcut(lowered, repo_root)
        if show_slug is not None:
            _print_lesson(repo_root, show_slug)
            continue
        try:
            result = handle_chat_message(message, repo_root=repo_root, session=session)
        except (ValueError, ThreadNotFoundError, ThreadNotParkedError) as exc:
            print(f"! {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — final safety net so one turn never kills the REPL
            traceback.print_exc(file=sys.stderr)
            print(f"! unexpected error: {exc}")
            continue
        session = result.session
        print(result.assistant_text)
        print(f"[{result.action}] {_compact(result.payload)}")
        if result.action == "list_lessons" and isinstance(result.payload, dict):
            lessons = result.payload.get("lessons")
            if isinstance(lessons, list) and lessons:
                _print_columns(sorted(lessons))
        _print_verdict_line(result.action, result.payload)


def main() -> None:
    """CLI entry: resolve the repo root and start the REPL."""
    from lesson_builder.pipeline.graph_runner import K_REPO_ROOT

    run_repl(K_REPO_ROOT)


def _print_help() -> None:
    print("Capabilities (natural language works; these are examples):")
    for action, example in K_CAPABILITY_EXAMPLES:
        print(f"  {action:<24} e.g. {example}")


def _match_show_shortcut(lowered: str, repo_root: Path) -> str | None:
    """Return a known slug if ``lowered`` is a 'show <slug>' shortcut, else None.

    Only fires when the remainder resolves to an existing slug, so 'show the
    latest thread' falls through to the controller instead of being swallowed.
    """
    return _match_verb_slug(lowered, K_SHOW_VERBS, _known_slugs(repo_root))


def _print_lesson(repo_root: Path, slug: str) -> None:
    """Load and render a lesson to the terminal, without calling the LLM."""
    path = repo_root / "data" / "lessons" / f"{slug}.json"
    try:
        lesson = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"! could not read {slug}: {exc}")
        return
    for render_line in render_lesson(lesson):
        print(render_line)


def _match_find_shortcut(lowered: str) -> str | None:
    """Return the substring to filter on if ``lowered`` is a find shortcut, else None.

    Only fires when the remainder looks like a bare keyword/slug fragment (a
    single token or a hyphen/underscore phrase), so natural-language phrasing
    like ``"find me a lesson on adjectives"`` falls through to the controller's
    LLM router instead of being swallowed by the local substring filter. This
    mirrors the discipline of ``_match_show_shortcut``.
    """
    for prefix in K_FIND_PREFIXES:
        if not lowered.startswith(prefix):
            continue
        remainder = lowered[len(prefix):].strip()
        if not remainder:
            return None
        # Reject natural-language remainders: leading filler words or any
        # whitespace (a bare keyword/slug fragment has no spaces).
        for filler in K_FIND_FILLER_PREFIXES:
            if remainder.startswith(filler):
                return None
        if any(ch.isspace() for ch in remainder):
            return None
        return remainder
    return None


def _print_find_results(repo_root: Path, query: str) -> None:
    """Print lesson slugs matching ``query`` in aligned columns, without calling the LLM."""
    if not query:
        print("! find needs a substring, e.g. 'find adjective'")
        return
    slugs = _known_slugs(repo_root)
    matches = sorted(slug for slug in slugs if query in slug)
    if not matches:
        print(f"no lessons match {query!r}")
        return
    print(f"{len(matches)} lesson(s) matching {query!r}:")
    _print_columns(matches)


def _print_columns(slugs: list[str]) -> None:
    """Print ``slugs`` in aligned columns (compact, no pagination)."""
    width = max((len(slug) for slug in slugs), default=0) + 2
    for index, slug in enumerate(sorted(slugs)):
        print(slug.ljust(width), end="")
        if (index + 1) % K_LIST_COLUMNS == 0:
            print()
    if len(slugs) % K_LIST_COLUMNS != 0:
        print()


def _known_slugs(repo_root: Path) -> list[str]:
    """Read lesson slugs from the lessons dir the same way the controller does."""
    lessons_root = repo_root / "data" / "lessons"
    return sorted(path.stem for path in lessons_root.glob("*.json"))


def _print_verdict_line(action: str, payload: dict[str, Any] | None) -> None:
    """After an improvement/review turn, print a plain-language gate verdict line."""
    if action not in ("run_improvement", "run_review"):
        return
    # run_improvement has an improvement-specific pre-check: a non-parked
    # flow status (needs_triage / needs_human) reports and bails before the
    # graph is even consulted.
    if action == "run_improvement" and isinstance(payload, dict):
        flow_status = payload.get("status")
        if flow_status in ("needs_triage", "needs_human"):
            message = payload.get("message") or flow_status
            print(f"! not parked — {message}")
            return
    graph = _graph_summary_from_payload(action, payload)
    if isinstance(graph, dict):
        _print_gate_verdict_from_graph(graph)


def _graph_summary_from_payload(
    action: str, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Return the gate-result graph dict for either action.

    run_improvement nests the gate verdict under ``graph`` (the shared
    back-half summary); run_review IS the graph. Centralizes that asymmetry so
    callers don't spread it across per-action branches.
    """
    if not isinstance(payload, dict):
        return None
    if action == "run_improvement":
        graph = payload.get("graph")
        return graph if isinstance(graph, dict) else None
    return payload


def _print_gate_verdict_from_graph(graph: dict[str, Any]) -> None:
    blocking = graph.get("blocking_issues") or []
    park_status = graph.get("park_status")
    if isinstance(blocking, list) and len(blocking) > 0:
        print(f"✗ {len(blocking)} issue(s) — parked for review (accept / defer)")
    elif park_status == "parked":
        print("✓ passed QA gate")


def _compact(payload: dict[str, Any] | None) -> str:
    """Render a one-line summary of the payload as flat ``key=value`` scalars.

    Dicts and lists are skipped (they would overflow the status line), so a
    full graph payload prints just its scalar fields. The ``lessons`` count is
    the only bespoke branch because the REPL renders the full list separately.
    """
    if not payload:
        return "ok"
    if "lessons" in payload:
        return f"{len(payload['lessons'])} lessons:"
    parts = [f"{k}={v}" for k, v in payload.items() if not isinstance(v, (dict, list))]
    return " ".join(parts) if parts else "ok"


if __name__ == "__main__":
    main()
