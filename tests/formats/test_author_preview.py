"""Behavior tests for the provider-free local author preview."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from lesson_builder.application.operations.author_preview import build_author_preview
from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.formats.html.author_preview import render_author_preview
from lesson_builder.workspace.preview_server import create_preview_server


def test_preview_resources_given_installed_package_expect_assets_available(tmp_path: Path) -> None:
    """A built wheel installs the renderer resources outside the checkout."""
    root = Path(__file__).parents[2]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(
        [
            "/usr/bin/python3",
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert wheels
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    install = subprocess.run(
        ["/usr/bin/python3", "-m", "pip", "install", "--no-deps", "--target", str(install_dir), str(wheels[-1])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stderr

    probe = """
from types import SimpleNamespace
from importlib.resources import files
import lesson_builder
from lesson_builder.formats.html.author_preview import render_author_preview
package = files("lesson_builder.formats.html")
assert str(lesson_builder.__file__).startswith(INSTALL_DIR)
assert package.joinpath("templates", "author_preview.html").is_file()
assert package.joinpath("templates", "macros.html").is_file()
assert package.joinpath("static", "author_preview.css").is_file()
assert package.joinpath("static", "author_preview.js").is_file()
preview = SimpleNamespace(
    lesson_id="wheel-probe",
    lesson_source="",
    exercise_source="",
    plan_source="",
    packet={"title": "Wheel probe", "sections": [], "exercises": [], "content": []},
    audit={"findings": []},
    errors=(),
)
assert "norsk lesson factory" in render_author_preview(preview)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(install_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = subprocess.run(
        [sys.executable, "-c", probe.replace("INSTALL_DIR", repr(str(install_dir)))],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@dataclass(frozen=True)
class PreviewData:
    """Minimal renderer input matching the format boundary protocol."""

    lesson_id: str
    lesson_source: str
    exercise_source: str
    plan_source: str
    packet: dict[str, Any] | None
    audit: dict[str, Any]
    errors: tuple[str, ...]


def test_preview_preserves_source_and_packet_order_given_real_lesson_expect_source_unchanged(tmp_path: Path) -> None:
    """The preview reads the package and follows public packet content order."""
    root = Path(__file__).parents[2]
    lesson_path = root / "content" / "lessons" / "greet_and_introduce_yourself" / "lesson.md"
    before = hashlib.sha256(lesson_path.read_bytes()).hexdigest()
    preview = build_author_preview(root, "greet_and_introduce_yourself")
    html = render_author_preview(preview)

    assert preview.packet is not None
    content_ids = [item["id"] for item in preview.packet["content"]]
    assert html.index(f'id="{content_ids[0]}"') < html.index(f'id="{content_ids[1]}"')
    assert hashlib.sha256(lesson_path.read_bytes()).hexdigest() == before
    assert preview.errors == ()
    assert '<div class="reading-text" lang="nb">Hei!' in html


def test_preview_renders_every_public_operation_given_packet_expect_authored_evidence() -> None:
    """Every public operation exposes its author evidence in the HTML."""
    spans = [{"kind": "text", "value": "Prompt"}]
    payloads = {
        "choose": {
            "options": [
                {"option_id": "a", "text": "A", "why": "why"},
                {"option_id": "b", "text": "B", "why": "distractor"},
            ],
            "answer_id": "a",
        },
        "recall_fill": {"segments": [{"kind": "blank", "blank_id": "b", "options": ["x", "y"], "answer_index": 1}]},
        "match_pairs": {
            "left": [{"left_id": "l", "text": "L"}],
            "right": [{"right_id": "r", "text": "R"}],
            "pairs": [{"left_id": "l", "right_id": "r"}],
        },
        "judge": {"sentence": spans, "is_correct": False, "feedback": "Fix"},
        "categorize": {
            "buckets": [{"bucket_id": "b", "label": "Bucket"}, {"bucket_id": "other", "label": "Other"}],
            "items": [{"item_id": "i", "text": "Item", "bucket_id": "b"}],
        },
        "build": {"tokens": [{"token_id": "t", "text": "token", "fixed": True}], "answer_order": ["t"]},
        "find_fix": {"tokens": [{"token_id": "t", "text": "wrong"}], "error_token_id": "t", "feedback": "Fix it"},
        "speak": {"target": "Hei"},
        "write": {
            "response_language": "no",
            "judge_prompt": "Judge",
            "criteria": [{"id": "c", "instruction": "Criterion"}],
        },
    }
    exercises = [
        {
            "kind": "exercise",
            "id": op,
            "operation": op,
            "objective_id": "o",
            "prompt": spans,
            "explanation": spans,
            "payload": payload,
        }
        for op, payload in payloads.items()
    ]
    packet = {
        "schema_version": "4.0",
        "id": "demo",
        "kind": "communicative",
        "language": "nb-NO",
        "title": "Demo",
        "cefr_level": "A1",
        "goal": "Goal",
        "objectives": [{"id": "o", "statement": "Objective"}],
        "content": [{"kind": "exercise", "id": op} for op in payloads],
        "sections": [],
        "exercises": exercises,
        "practice_groups": [{"id": "group", "objective_id": "o", "exercise_ids": list(payloads)}],
        "media": {"audio": []},
    }
    ExportedLesson.model_validate(packet)
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))

    operation_evidence = {
        "choose": ("A · Answer", "B", "distractor"),
        "recall_fill": ("b = y", "Options: x · y"),
        "match_pairs": ("L <span>→</span> R", "All authored sides"),
        "judge": ("Author judgment · incorrect", "Fix"),
        "categorize": ("Item <span>→ Bucket</span>",),
        "build": ("token [fixed]", "Author answer · build order"),
        "find_fix": ("wrong [error]", "Authored feedback", "Fix it"),
        "speak": ("Author target", "Hei"),
        "write": ("response language: no", "Criterion", "Judge"),
    }
    for operation, expected_evidence in operation_evidence.items():
        start = html.index(f'id="{operation}"')
        subtree = html[start : html.index("</article>", start)]
        assert f'class="exercise-tag">{operation}' in subtree
        for evidence in expected_evidence:
            assert evidence in subtree
    assert "Author explanation" in html[html.index('id="choose"') : html.index("</article>", html.index('id="choose"'))]
    assert 'data-disabled="true"' in html
    assert "Audio unavailable" in html
    choose_subtree = html[html.index('id="choose"') : html.index("</article>", html.index('id="choose"'))]
    for operation in ("recall_fill", "judge", "build", "find_fix", "speak"):
        subtree_start = html.index(f'id="{operation}"')
        subtree = html[subtree_start : html.index("</article>", subtree_start)]
        assert 'lang="nb"' in subtree
    assert '<h2 lang="en">' in choose_subtree


def test_preview_given_mixed_choose_text_expect_no_vocabulary_inference() -> None:
    """Choose text stays untagged because its schema field is mixed prose, while target fields are Norwegian."""
    packet = {
        "title": "Language contract",
        "cefr_level": "A1",
        "kind": "grammar",
        "content": [{"kind": "exercise", "id": "choose"}],
        "sections": [],
        "exercises": [
            {
                "kind": "exercise",
                "id": "choose",
                "operation": "choose",
                "objective_id": "obj",
                "prompt": [{"kind": "text", "value": "Choose the authored line."}],
                "payload": {
                    "options": [
                        {"option_id": "english", "text": "Their names are here.", "why": "English explanation."},
                        {"option_id": "norwegian", "text": "Takk for hjelpen.", "why": "Norwegian target phrase."},
                    ],
                    "answer_id": "norwegian",
                },
            }
        ],
    }
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))
    choose_start = html.index('id="choose"')
    choose_subtree = html[choose_start : html.index("</article>", choose_start)]
    assert "Their names are here." in choose_subtree
    assert "Takk for hjelpen." in choose_subtree
    assert '<span lang="nb">Their' not in choose_subtree
    assert '<span lang="nb">Takk' not in choose_subtree

    packet["exercises"][0]["operation"] = "speak"
    packet["exercises"][0]["payload"] = {"target": "Takk for hjelpen."}
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))
    assert '<p lang="nb">Takk for hjelpen.</p>' in html


def test_preview_escapes_authored_html_given_untrusted_text_expect_inert_markup() -> None:
    """Authored markup is inert and the server refuses network interfaces."""
    packet = {
        "title": "<script>alert(1)</script>",
        "cefr_level": "A1",
        "kind": "grammar",
        "content": [],
        "sections": [],
        "exercises": [],
    }
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    with pytest.raises(ValueError, match="loopback"):
        create_preview_server("0.0.0.0", 0, html)


def test_preview_rejects_source_symlink_escape_given_outside_package_expect_safe_failure(tmp_path: Path) -> None:
    """A source symlink may not make the preview read outside the repository."""
    root = tmp_path / "repo"
    package = root / "content" / "lessons" / "demo"
    outside = tmp_path / "outside.md"
    package.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    (package / "lesson.md").symlink_to(outside)
    (package / "exercises.yaml").write_text("[]", encoding="utf-8")
    (package / "plan.md").write_text("---\nkind: grammar\n---\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing lesson.md"):
        build_author_preview(root, "demo")


def test_preview_server_returns_not_found_given_unknown_path_expect_404(tmp_path: Path) -> None:
    """The in-memory server does not expose filesystem paths."""
    server = create_preview_server("127.0.0.1", 0, "<html></html>")
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/etc/passwd")
        assert connection.getresponse().status == 404
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_preview_server_rejects_untrusted_authorities_given_http_headers_expect_safe_errors() -> None:
    """Host and Origin checks prevent DNS rebinding from reaching source HTML."""
    server = create_preview_server("127.0.0.1", 0, "<html>source</html>")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:

        def request(headers: dict[str, str]) -> int:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/", headers=headers)
            response = connection.getresponse()
            status = response.status
            connection.close()
            return status

        port = server.server_port
        assert request({"Host": f"127.0.0.1:{port}"}) == 200
        assert request({"Host": f"attacker.example:{port}"}) == 403
        assert request({"Host": f"127.0.0.1:{port}", "Origin": f"https://attacker.example:{port}"}) == 403
        assert request({"Host": f"127.0.0.1:{port}", "Origin": "not-an-origin"}) == 400
    finally:
        server.shutdown()
        server.server_close()


def test_preview_server_accepts_bracketed_ipv6_authority_given_loopback_request_expect_200() -> None:
    """IPv6 loopback authorities use the exact bracketed Host form."""
    try:
        server = create_preview_server("::1", 0, "<html>source</html>")
    except OSError as exc:
        pytest.skip(f"IPv6 loopback unavailable: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("::1", server.server_port)
        connection.request("GET", "/", headers={"Host": f"[::1]:{server.server_port}"})
        assert connection.getresponse().status == 200
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_preview_server_rebuilds_document_for_each_get_given_factory_expect_fresh_content() -> None:
    """The CLI preview reflects edits made while the server is running."""
    current = ["<html>first</html>"]
    server = create_preview_server("127.0.0.1", 0, current[0], lambda: current[0])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        assert b"first" in connection.getresponse().read()
        connection.close()
        current[0] = "<html>second</html>"
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        assert b"second" in connection.getresponse().read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_preview_server_shows_rebuild_error_ui_given_factory_failure_expect_actionable_503() -> None:
    """A source reload failure remains understandable to an author."""
    server = create_preview_server("127.0.0.1", 0, "<html>first</html>", lambda: (_ for _ in ()).throw(ValueError()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 503
        assert "Preview unavailable" in body
        assert "Fix the authored source" in body
        assert "first" not in body
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_preview_given_packet_expect_accessible_language_and_navigation_semantics() -> None:
    """The author view exposes language, focus, and non-tab jump-link semantics."""
    packet = {
        "title": "Demo",
        "cefr_level": "A1",
        "kind": "grammar",
        "goal": "Goal",
        "content": [{"kind": "section", "id": "first_section"}],
        "sections": [
            {
                "kind": "section",
                "id": "first_section",
                "title": "First",
                "blocks": [{"kind": "reading", "spans": [{"kind": "text", "value": "Hei"}], "translation": "Hi"}],
            }
        ],
        "exercises": [],
    }
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))
    assert '<section class="panel lesson-column" id="lesson-preview" tabindex="-1"' in html
    assert '<div class="reading-text" lang="nb">' in html
    assert "aria-current=" not in html
    assert 'class="section-card" lang="nb"' not in html
    assert 'class="tab' not in html
    assert "First" in html
    assert '--font-body: "Segoe UI"' in html
    assert "@font-face" not in html
    assert "outline: 3px solid var(--primary-70)" in html
    assert "requestAnimationFrame" in html
    assert 'id="source-view" tabindex="-1"' in html
    assert "<h1>" in html and '<article class="section-card"' in html


def test_preview_contains_source_error_given_malformed_exercises_yaml_expect_no_crash(tmp_path: Path) -> None:
    """Malformed exercise YAML remains inspectable as an explicit preview error."""
    source = Path(__file__).parents[2] / "content" / "lessons" / "greet_and_introduce_yourself"
    package = tmp_path / "repo" / "content" / "lessons" / "demo"
    shutil.copytree(source, package)
    (package / "exercises.yaml").write_text("- [unterminated", encoding="utf-8")

    preview = build_author_preview(tmp_path / "repo", "demo")

    assert preview.packet is None
    assert preview.exercise_source == "- [unterminated"
    assert any("compile preview" in error for error in preview.errors)


def test_preview_contains_source_error_given_malformed_plan_frontmatter_expect_no_crash(tmp_path: Path) -> None:
    """Malformed plan YAML remains inspectable as an explicit preview error."""
    source = Path(__file__).parents[2] / "content" / "lessons" / "greet_and_introduce_yourself"
    package = tmp_path / "repo" / "content" / "lessons" / "demo"
    shutil.copytree(source, package)
    (package / "plan.md").write_text("---\nkind: [unterminated\n---\n", encoding="utf-8")

    preview = build_author_preview(tmp_path / "repo", "demo")

    assert preview.packet is None
    assert "kind: [unterminated" in preview.plan_source
    assert any("compile preview" in error for error in preview.errors)


def test_preview_renders_recursive_public_blocks_given_section_packet_expect_all_block_text() -> None:
    """Nested callouts and public block variants stay visible in the preview."""

    def span(value: str) -> list[dict[str, str]]:
        return [{"kind": "text", "value": value}]

    section = {
        "kind": "section",
        "id": "section",
        "role": "model",
        "title": "Blocks",
        "objective_ids": [],
        "blocks": [
            {"kind": "heading", "id": "h3", "level": 3, "spans": span("Heading three")},
            {"kind": "heading", "id": "h4", "level": 4, "spans": span("Heading four")},
            {"kind": "paragraph", "id": "p", "spans": span("paragraph")},
            {"kind": "reading", "id": "r", "spans": span("Hei"), "translation": "Hello"},
            {"kind": "examples", "id": "e", "items": [{"no": span("Norsk"), "en": span("Norwegian")}]},
            {"kind": "word_list", "id": "w", "items": [{"term": "hei", "form": "greeting"}]},
            {
                "kind": "table",
                "id": "t",
                "col_langs": ["no", "en"],
                "headers": [span("NO"), span("EN")],
                "rows": [[span("hei"), span("hi")]],
            },
            {
                "kind": "callout",
                "id": "c",
                "level": "tip",
                "blocks": [{"kind": "rule", "id": "rule", "statement": span("Nested rule")}],
            },
        ],
    }
    packet = {
        "title": "Blocks",
        "cefr_level": "A1",
        "kind": "grammar",
        "goal": "Goal",
        "content": [{"kind": "section", "id": "section"}],
        "sections": [section],
        "exercises": [],
        "media": {"audio": []},
    }
    html = render_author_preview(PreviewData("demo", "", "", "", packet, {"findings": []}, ()))

    section_start = html.index('id="section"')
    section_subtree = html[section_start : html.index("</article>", section_start)]
    for text in ("paragraph", "Hei", "Hello", "Norsk", "Norwegian", "greeting", "Nested rule"):
        assert text in section_subtree
    for marker in (
        '<h3 class="content-heading" lang="en">Heading three</h3>',
        '<h4 class="content-heading" lang="en">Heading four</h4>',
        '<p lang="en">paragraph</p>',
        '<div class="reading-block">',
        '<div class="examples">',
        '<dl class="word-list">',
        '<div class="table-wrap">',
        '<aside class="callout callout-tip" lang="en">',
        '<div class="rule" lang="en"><span>Rule</span>',
    ):
        assert marker in section_subtree
