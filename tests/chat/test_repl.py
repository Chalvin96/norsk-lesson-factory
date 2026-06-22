"""Entry point: ``run_repl``.

Behavioral tests for the conversational REPL over the chat controller. The
controller's ``handle_chat_message`` is monkeypatched with scripted results so
no live LLM calls are made; the tests assert that the REPL prints the assistant
text, a compact one-line payload summary, and survives a ``ValueError`` turn
without crashing the loop.
"""

from __future__ import annotations

from pathlib import Path

import lesson_builder.chat.repl as repl_module
from lesson_builder.chat.controller import ChatSessionState, ChatTurnResult
from lesson_builder.chat.repl import run_repl


def _turn(
    *,
    assistant_text: str,
    action: str,
    payload: dict | None,
    session: ChatSessionState,
) -> ChatTurnResult:
    return ChatTurnResult(
        session=session,
        assistant_text=assistant_text,
        action=action,  # type: ignore[arg-type]
        payload=payload,
    )


def _make_input(monkeypatch, lines: list[str]) -> None:
    """Replace ``builtins.input`` with a generator that yields ``lines`` then EOFs."""
    iterator = iter(lines)

    def _fake_input(_prompt: object = "") -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr("builtins.input", _fake_input)


def test_run_repl_given_assistant_text_expect_prints_text_and_compact_payload(
    monkeypatch, tmp_path: Path, capsys
):
    # setup
    session = ChatSessionState(session_id="s1")
    expected = _turn(
        assistant_text="Here are the lessons.",
        action="list_lessons",
        payload={"lessons": ["ordinal_numbers", "adjective_agreement"]},
        session=session,
    )
    calls: list[str] = []

    def _fake_handle(message, *, repo_root, session):  # noqa: ANN001
        calls.append(message)
        return expected

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["what lessons exist?", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert calls == ["what lessons exist?"]
    assert "Here are the lessons." in out
    assert "[list_lessons]" in out
    assert "ordinal_numbers" in out
    assert "2 lessons:" in out


def test_run_repl_given_value_error_turn_expect_survives_and_keeps_looping(
    monkeypatch, tmp_path: Path, capsys
):
    # setup
    session = ChatSessionState(session_id="s2")
    state = {"calls": 0}

    def _fake_handle(message, *, repo_root, session):  # noqa: ANN001
        state["calls"] += 1
        if state["calls"] == 1:
            raise ValueError("unknown lesson slug 'nope'")
        return _turn(
            assistant_text="Recovered: lesson selected.",
            action="set_active_lesson",
            payload={"active_slug": "ordinal_numbers"},
            session=session,
        )

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["use nope", "use ordinal_numbers", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "! unknown lesson slug 'nope'" in out
    assert "Recovered: lesson selected." in out
    assert "[set_active_lesson] active_slug=ordinal_numbers" in out
    assert state["calls"] == 2


def test_run_repl_given_help_command_expect_prints_creator_friendly_capabilities_without_calling_controller(
    monkeypatch, tmp_path: Path, capsys
):
    # setup
    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for the help command")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s3"))
    _make_input(monkeypatch, ["help", "quit"])

    # execute
    run_repl(tmp_path)

    # assert: help text uses creator-friendly phrases as leading words, not
    # internal action names, but still keeps the example lines.
    out = capsys.readouterr().out
    assert "Capabilities" in out
    assert "what lessons exist?" in out  # example line preserved
    assert "add an exercise to ordinal_numbers" in out  # example line preserved
    assert "find adjective" in out  # find shortcut documented


def test_run_repl_given_empty_line_then_eof_expect_no_controller_call(
    monkeypatch, tmp_path: Path, capsys
):
    # setup
    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for empty/EOF input")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s4"))
    _make_input(monkeypatch, ["", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "lesson-data chat" in out


def test_run_repl_given_lesson_summary_payload_expect_compact_summary(
    monkeypatch, tmp_path: Path, capsys
):
    # setup
    session = ChatSessionState(session_id="s5")
    expected = _turn(
        assistant_text="Parked for review.",
        action="run_review",
        payload={
            "slug": "ordinal_numbers",
            "run_id": "r1",
            "lesson": {
                "title": "Ordinal numbers",
                "cefr_level": "A1",
                "elements": [{"id": "e1"}, {"id": "e2"}],
            },
        },
        session=session,
    )
    monkeypatch.setattr(
        repl_module,
        "handle_chat_message",
        lambda message, *, repo_root, session: expected,
    )
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["run QA on ordinal_numbers", "quit"])

    # execute
    run_repl(tmp_path)

    # assert: the flat ``key=value`` fallback prints the scalar fields and
    # skips the nested ``lesson`` dict (no ``lesson_summary``/``elements``
    # curation now — the lesson dict is excluded by the generic filter).
    out = capsys.readouterr().out
    assert "[run_review]" in out
    assert "slug=ordinal_numbers" in out
    assert "run_id=r1" in out


def test_run_repl_given_thread_not_found_error_expect_caught_and_loop_survives(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: a non-ValueError domain exception (ThreadNotFoundError) must be
    # caught so the loop survives, instead of killing the whole REPL process.
    session = ChatSessionState(session_id="s6")
    state = {"calls": 0}

    def _fake_handle(message, *, repo_root, session):  # noqa: ANN001
        state["calls"] += 1
        if state["calls"] == 1:
            raise repl_module.ThreadNotFoundError("no such thread 'slug:badid'")
        return _turn(
            assistant_text="Recovered after domain error.",
            action="set_active_lesson",
            payload={"active_slug": "ordinal_numbers"},
            session=session,
        )

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["show the latest thread", "use ordinal_numbers", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "! no such thread 'slug:badid'" in out
    assert "Recovered after domain error." in out
    assert "[set_active_lesson] active_slug=ordinal_numbers" in out
    assert state["calls"] == 2


def test_run_repl_given_unexpected_exception_expect_safety_net_and_loop_survives(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: a generic unexpected exception is caught by the final safety net
    # and printed as "! unexpected error: ...", keeping the loop alive.
    session = ChatSessionState(session_id="s7")
    state = {"calls": 0}

    def _fake_handle(message, *, repo_root, session):  # noqa: ANN001
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("kaboom")
        return _turn(
            assistant_text="Recovered after unexpected error.",
            action="set_active_lesson",
            payload={"active_slug": "ordinal_numbers"},
            session=session,
        )

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["do something", "use ordinal_numbers", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "! unexpected error: kaboom" in out
    assert "Recovered after unexpected error." in out
    assert state["calls"] == 2


def test_run_repl_given_find_shortcut_expect_filters_slugs_without_calling_controller(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: seed a fake lessons dir with several slugs; the find shortcut must
    # filter locally and never call handle_chat_message.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    for slug in ("adjective_agreement", "adjective_comparison", "ordinal_numbers"):
        (lessons_root / f"{slug}.json").write_text("{}", encoding="utf-8")

    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for the find shortcut")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s8"))
    _make_input(monkeypatch, ["find adjective", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "2 lesson(s) matching 'adjective'" in out
    assert "adjective_agreement" in out
    assert "adjective_comparison" in out
    assert "ordinal_numbers" not in out


def test_run_repl_given_lessons_matching_shortcut_expect_filters_slugs(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: the alternative "lessons matching <sub>" phrasing works too.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    for slug in ("adjective_agreement", "ordinal_numbers"):
        (lessons_root / f"{slug}.json").write_text("{}", encoding="utf-8")

    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for the find shortcut")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s9"))
    _make_input(monkeypatch, ["lessons matching ordinal", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "1 lesson(s) matching 'ordinal'" in out
    assert "ordinal_numbers" in out


def test_run_repl_given_find_with_natural_language_expect_falls_through_to_controller(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: "find me a lesson on adjectives" is natural language, not a bare
    # keyword filter. It must fall through to the controller's LLM router
    # instead of being swallowed by the local substring filter.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    for slug in ("adjective_agreement", "ordinal_numbers"):
        (lessons_root / f"{slug}.json").write_text("{}", encoding="utf-8")

    session = ChatSessionState(session_id="s-natlang")
    calls: list[str] = []
    expected = _turn(
        assistant_text="Routing to the LLM.",
        action="help",
        payload={"capabilities": ["select a lesson slug"]},
        session=session,
    )

    def _fake_handle(message, *, repo_root, session):  # noqa: ANN001
        calls.append(message)
        return expected

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["find me a lesson on adjectives", "quit"])

    # execute
    run_repl(tmp_path)

    # assert: the controller WAS called with the natural-language message
    out = capsys.readouterr().out
    assert calls == ["find me a lesson on adjectives"]
    assert "no lessons match" not in out
    assert "Routing to the LLM." in out


def test_run_repl_given_find_bare_keyword_expect_still_filters_locally(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: a bare single-token keyword ("find adjective") is still a local
    # filter and must NOT reach the controller.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    for slug in ("adjective_agreement", "adjective_comparison", "ordinal_numbers"):
        (lessons_root / f"{slug}.json").write_text("{}", encoding="utf-8")

    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for the find shortcut")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s-bare"))
    _make_input(monkeypatch, ["find adjective", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "2 lesson(s) matching 'adjective'" in out
    assert "adjective_agreement" in out


def test_run_repl_given_show_shortcut_expect_renders_lesson_without_controller(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: "show <slug>" loads and renders the lesson locally, never the LLM.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    (lessons_root / "adjective_agreement.json").write_text(
        '{"key": "adjective_agreement", "title": "Agreement", "cefr_level": "A1", '
        '"elements": [{"element_kind": "section", "id": "sec_0", "role": "orient", '
        '"title": "Intro", "blocks": [{"kind": "paragraph", "spans": '
        '[{"kind": "text", "value": "Hi."}]}]}]}',
        encoding="utf-8",
    )

    def _fake_handle(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("controller must not be invoked for the show shortcut")

    monkeypatch.setattr(repl_module, "handle_chat_message", _fake_handle)
    monkeypatch.setattr(repl_module, "new_session", lambda: ChatSessionState(session_id="s11"))
    _make_input(monkeypatch, ["show adjective_agreement", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "adjective_agreement — Agreement (A1)" in out
    assert "[sec_0]  section · orient" in out


def test_run_repl_given_show_thread_phrase_expect_falls_through_to_controller(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: "show the latest thread" is NOT a slug, so it must reach the
    # controller instead of being swallowed by the show shortcut.
    lessons_root = tmp_path / "data" / "lessons"
    lessons_root.mkdir(parents=True, exist_ok=True)
    (lessons_root / "ordinal_numbers.json").write_text("{}", encoding="utf-8")

    session = ChatSessionState(session_id="s12")
    expected = _turn(
        assistant_text="Showing the latest thread.",
        action="show_thread",
        payload={"slug": "ordinal_numbers", "run_id": "r1"},
        session=session,
    )
    monkeypatch.setattr(
        repl_module,
        "handle_chat_message",
        lambda message, *, repo_root, session: expected,
    )
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["show the latest thread", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "Showing the latest thread." in out


def test_run_repl_given_list_lessons_payload_expect_column_rendering(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: when the action is list_lessons and the payload has a full lessons
    # list, the REPL renders all slugs in aligned columns instead of "(+N more)".
    session = ChatSessionState(session_id="s10")
    slugs = [f"slug_{i:02d}" for i in range(7)]
    expected = _turn(
        assistant_text="Here are the lessons.",
        action="list_lessons",
        payload={"lessons": slugs},
        session=session,
    )
    monkeypatch.setattr(
        repl_module,
        "handle_chat_message",
        lambda message, *, repo_root, session: expected,
    )
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["what lessons exist?", "quit"])

    # execute
    run_repl(tmp_path)

    # assert: all 7 slugs appear (no "(+N more)" truncation).
    out = capsys.readouterr().out
    assert "7 lessons:" in out
    assert "(+2 more)" not in out
    for slug in slugs:
        assert slug in out


def test_run_repl_given_run_review_parked_payload_expect_passed_verdict_line(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: a clean parked review should print the plain verdict line.
    session = ChatSessionState(session_id="s11")
    expected = _turn(
        assistant_text="QA complete.",
        action="run_review",
        payload={
            "slug": "ordinal_numbers",
            "run_id": "r1",
            "park_status": "parked",
            "blocking_issues": [],
        },
        session=session,
    )
    monkeypatch.setattr(
        repl_module,
        "handle_chat_message",
        lambda message, *, repo_root, session: expected,
    )
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["run QA on ordinal_numbers", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "✓ passed QA gate" in out


def test_run_repl_given_run_improvement_blocking_payload_expect_issue_verdict_line(
    monkeypatch, tmp_path: Path, capsys
):
    # setup: an improvement flow that parks with blocking issues should print
    # the plain verdict line with the issue count.
    session = ChatSessionState(session_id="s12")
    expected = _turn(
        assistant_text="Draft parked.",
        action="run_improvement",
        payload={
            "status": "parked_for_review",
            "graph": {
                "slug": "ordinal_numbers",
                "run_id": "r2",
                "park_status": "parked",
                "blocking_issues": ["issue one", "issue two"],
            },
            "draft_path": "tmp/improvement_drafts/ordinal_numbers__r2.json",
        },
        session=session,
    )
    monkeypatch.setattr(
        repl_module,
        "handle_chat_message",
        lambda message, *, repo_root, session: expected,
    )
    monkeypatch.setattr(repl_module, "new_session", lambda: session)
    _make_input(monkeypatch, ["add an exercise", "quit"])

    # execute
    run_repl(tmp_path)

    # assert
    out = capsys.readouterr().out
    assert "✗ 2 issue(s) — parked for review (accept / defer)" in out
