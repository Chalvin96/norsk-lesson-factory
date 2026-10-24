"""Behavior tests for the loopback workbench server: app shell, JSON API, assets and request guard."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from lesson_builder.application.operations.author_preview import build_package_preview
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_records import ReviewFinding
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_review import load_workbench_reviewer_version
from lesson_builder.web.server import create_workbench_server
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.factories import AuthorPreviewFactory
from tests.application.factories import ReviewRunFactory
from tests.domain.curriculum.factories import CurriculumPlanFactory
from tests.domain.curriculum.factories import CurriculumPlanSlotFactory
from tests.web.serving import HttpReply
from tests.web.serving import send_raw_request
from tests.web.serving import send_request
from tests.web.serving import serving
from tests.workspace.builders import write_curriculum_plan

K_LESSON_ID = "greet_and_introduce_yourself"
K_SECOND_LESSON_ID = "second_lesson"
K_REAL_LESSON_PACKAGE = Path(__file__).parents[2] / "content" / "lessons" / K_LESSON_ID


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A workspace with a two-slot plan and one canonical lesson package."""
    write_curriculum_plan(
        tmp_path,
        CurriculumPlanFactory(
            slots=[
                CurriculumPlanSlotFactory(sequence_index=0, catalog_id=K_LESSON_ID, title="Greetings slot"),
                CurriculumPlanSlotFactory(sequence_index=1, catalog_id=K_SECOND_LESSON_ID, title="Second slot"),
            ]
        ),
    )
    shutil.copytree(K_REAL_LESSON_PACKAGE, tmp_path / "content" / "lessons" / K_LESSON_ID)
    return tmp_path


@pytest.fixture
def built_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide generated HTML and assets without depending on a repository build."""
    app_root = tmp_path / "app"
    assets = app_root / "assets"
    assets.mkdir(parents=True)
    (app_root / "index.html").write_text(
        '<div id="root"></div><script src="/assets/app.js"></script><link href="/assets/app.css" rel="stylesheet">',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("document.querySelector('#root');", encoding="utf-8")
    (assets / "app.css").write_text("#root { display: block; }", encoding="utf-8")
    monkeypatch.setattr("lesson_builder.web.server._app_root", lambda: app_root)
    return app_root


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.2", "192.168.1.5", "::1", "", "example.com"])
def test_create_workbench_server_given_non_loopback_host_expect_value_error(workspace: Path, host: str) -> None:
    with pytest.raises(ValueError, match="host"):
        create_workbench_server(workspace, host, 0)


def test_create_workbench_server_given_stale_opencode_directory_expect_startup_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "opencode-runtime"
    runtime_root.mkdir()
    stale_dir = runtime_root / "norsk-opencode-state-legacy"
    stale_dir.mkdir()
    old = time.time() - 24 * 60 * 60
    os.utime(stale_dir, (old, old))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))

    server = create_workbench_server(tmp_path, "127.0.0.1", 0)
    try:
        assert not stale_dir.exists()
    finally:
        server.server_close()


@pytest.mark.parametrize("path", ["/", "/settings", f"/lessons/{K_LESSON_ID}", f"/lessons/{K_SECOND_LESSON_ID}"])
def test_workbench_app_page_given_supported_route_expect_uncached_app_shell(
    workspace: Path, built_app: Path, path: str
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, path)

    assert reply.status == 200
    assert reply.headers["cache-control"] == "no-store"
    assert '<div id="root"></div>' in reply.body


@pytest.mark.parametrize("path", ["/nope/x", "/unknown-page", "/nope/x/?view=source", "/etc/passwd"])
def test_workbench_app_page_given_unknown_page_path_expect_uncached_app_shell_with_404(
    workspace: Path, built_app: Path, path: str
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, path)

    assert reply.status == 404
    assert reply.headers["content-type"] == "text/html; charset=utf-8"
    assert reply.headers["cache-control"] == "no-store"
    assert reply.body == (built_app / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("path", ["/", "/nope/x"])
def test_workbench_app_page_given_missing_build_expect_uncached_rebuild_page_with_503(
    workspace: Path, built_app: Path, path: str
) -> None:
    (built_app / "index.html").unlink()

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, path)

    assert reply.status == 503
    assert reply.headers["cache-control"] == "no-store"
    assert "<h1>Workbench unavailable</h1>" in reply.body
    assert "Fix the authored source and reload this page." in reply.body


def test_workbench_app_assets_given_built_app_expect_immutable_typed_assets(workspace: Path, built_app: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        shell = send_request(server, "/").body
        script = re.search(r'src="/assets/([^"]+\.js)"', shell)
        stylesheet = re.search(r'href="/assets/([^"]+\.css)"', shell)
        assert script and stylesheet
        js = send_request(server, f"/assets/{script.group(1)}")
        css = send_request(server, f"/assets/{stylesheet.group(1)}")

    assert (js.status, css.status) == (200, 200)
    assert js.headers["content-type"] == "text/javascript; charset=utf-8"
    assert css.headers["content-type"] == "text/css; charset=utf-8"
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert js.headers["x-content-type-options"] == "nosniff"
    assert "fonts.googleapis.com" not in css.body


def test_workbench_api_curriculum_given_plan_expect_ordered_lessons_with_live_status(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, "/api/curriculum")

    payload = json.loads(reply.body)
    assert reply.status == 200
    assert reply.headers["cache-control"] == "no-store"
    assert reply.headers["content-type"] == "application/json; charset=utf-8"
    assert [lesson["title"] for lesson in payload["lessons"]] == ["Greetings slot", "Second slot"]
    assert payload["lessons"][0] == {
        "lessonId": K_LESSON_ID,
        "title": "Greetings slot",
        "cefrLevel": payload["lessons"][0]["cefrLevel"],
        "sequenceIndex": 0,
        "source": "canonical",
    }
    assert payload["entries"][K_SECOND_LESSON_ID]["dependsOn"] == []
    assert payload["lessons"][1]["source"] == "missing"
    assert payload["entries"][K_SECOND_LESSON_ID]["statusChips"] == [{"label": "no package", "tone": "neutral"}]
    assert "isMock" not in payload


def test_curriculum_chips_given_one_open_and_one_resolved_stored_finding_expect_real_open_count(
    workspace: Path,
) -> None:
    findings = [
        ReviewFinding(
            finding_id=str(index),
            key="a-first-meeting#0",
            criterion="naturalness",
            excerpt="Hei",
            explanation="Review text",
            severity="minor",
            status=status,
        )
        for index, status in enumerate(("open", "resolved"))
    ]
    source = read_workbench_source(workspace, K_LESSON_ID)
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=findings,
    )
    write_workbench_record(workspace, "reviews", K_LESSON_ID, ReviewLog(runs=[run]))
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        payload = json.loads(send_request(server, "/api/curriculum").body)
    assert {"label": "1 AI", "tone": "neutral"} in payload["entries"][K_LESSON_ID]["statusChips"]
    assert all("checks" not in chip["label"] for chip in payload["entries"][K_LESSON_ID]["statusChips"])


def test_workbench_api_lesson_given_canonical_lesson_expect_ordered_elements_and_real_state(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}?tab=lesson")

    payload = json.loads(reply.body)
    assert reply.status == 200
    assert payload["source"] == "canonical"
    assert payload["sourceLabel"] == f"content/lessons/{K_LESSON_ID}"
    assert payload["elements"][0]["kind"] == "section"
    first_reading = next(block for block in payload["elements"][0]["blocks"] if block["kind"] == "reading")
    assert first_reading["spans"][0]["value"].startswith("Hei!")
    assert "isMock" not in payload["state"]
    assert payload["state"]["proposals"] == []
    assert payload["state"]["chat"] == []
    assert all(item["status"] == "unknown" for item in payload["state"]["exerciseStaleness"])
    assert all("snapshot is unavailable" in item["reason"] for item in payload["state"]["exerciseStaleness"])
    assert any(blocker["kind"] == "review_coverage" for blocker in payload["state"]["approveBlockers"])
    assert payload["state"]["approvable"] is False


def test_workbench_api_lesson_given_draft_directory_expect_draft_source_wins(workspace: Path) -> None:
    shutil.copytree(K_REAL_LESSON_PACKAGE, workspace / "drafts" / K_LESSON_ID)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        payload = json.loads(send_request(server, f"/api/lessons/{K_LESSON_ID}").body)

    assert payload["source"] == "draft"
    assert payload["sourceLabel"] == f"drafts/{K_LESSON_ID}"


def test_workbench_server_given_malformed_request_target_expect_400_response(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        request = f"GET http://[x/ HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n\r\n".encode()
        reply = send_raw_request(server, request)

    assert reply.status == 400


@pytest.mark.parametrize(
    "path",
    [
        "/lessons/../content/lessons/greet_and_introduce_yourself",
        "/lessons/..%2F..%2Fetc%2Fpasswd",
        "/lessons//etc/passwd",
        "/lessons/Bad-Slug",
        f"/lessons/{K_LESSON_ID}/",
        "/lessons/",
        "/api",
        "/index.html",
        "/assets",
        "/assets/../index.html",
        "/assets/%2e%2e/index.html",
        "/assets/missing.js",
        "/assets/index.html",
        "/assets/.hidden.js",
        "/static",
        "/static/missing.css",
        "/nope/../x",
        "/nope/%2e%2e/x",
        "/nope/..%2Fx",
        "/nope//x",
        "/nope/%5Cx",
        "/nope/x.css",
    ],
)
def test_workbench_server_given_unknown_path_or_unsafe_asset_expect_404(
    workspace: Path, built_app: Path, path: str
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, path)

    assert reply.status == 404
    assert "<h1>Error response</h1>" in reply.body
    assert '<div id="root"></div>' not in reply.body


@pytest.mark.parametrize(
    "path",
    [
        "/api/lessons/../content/lessons/greet_and_introduce_yourself",
        "/api/lessons/..%2F..%2Fetc%2Fpasswd",
        "/api/lessons/Bad-Slug",
        f"/api/lessons/{K_SECOND_LESSON_ID}",
        "/api/lessons/",
        "/api/unknown",
    ],
)
def test_workbench_api_given_unknown_or_escaping_resource_expect_404_json(workspace: Path, path: str) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, path)

    assert reply.status == 404
    assert reply.headers["content-type"] == "application/json; charset=utf-8"


def test_workbench_api_lesson_given_incomplete_draft_directory_expect_503(workspace: Path) -> None:
    (workspace / "drafts" / K_LESSON_ID).mkdir(parents=True)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}")

    assert reply.status == 503
    assert json.loads(reply.body) == {"error": "rebuild_failed"}
    assert reply.headers["cache-control"] == "no-store"


def test_workbench_api_lesson_given_draft_file_symlink_escaping_drafts_root_expect_503(workspace: Path) -> None:
    draft = workspace / "drafts" / K_LESSON_ID
    shutil.copytree(K_REAL_LESSON_PACKAGE, draft)
    secret = workspace / "secret.md"
    secret.write_text("TOP-SECRET-OUTSIDE-DRAFTS", encoding="utf-8")
    (draft / "lesson.md").unlink()
    (draft / "lesson.md").symlink_to(secret)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}")

    assert reply.status == 503
    assert "TOP-SECRET-OUTSIDE-DRAFTS" not in reply.body


def test_workbench_api_lesson_given_draft_symlink_loop_expect_503_without_dropped_connection(workspace: Path) -> None:
    drafts = workspace / "drafts"
    drafts.mkdir()
    (drafts / K_LESSON_ID).symlink_to(K_LESSON_ID, target_is_directory=True)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}")

    assert reply.status == 503
    assert reply.headers["cache-control"] == "no-store"


def test_workbench_server_given_untrusted_or_missing_authority_expect_safe_errors(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        port = server.server_port
        assert send_request(server, "/", omit_host=True).status == 403
        assert send_request(server, "/", host=f"attacker.example:{port}").status == 403
        assert send_request(server, "/", headers={"Origin": f"http://attacker.example:{port}"}).status == 403
        assert send_request(server, "/", headers={"Origin": "not-an-origin"}).status == 400
        assert send_request(server, "/api/curriculum", host=f"attacker.example:{port}").status == 403
        assert send_request(server, f"/api/lessons/{K_LESSON_ID}", omit_host=True).status == 403
        assert send_request(server, "/assets/missing.js", omit_host=True).status == 403


@pytest.mark.parametrize("method", ["DELETE", "PATCH", "HEAD"])
def test_workbench_server_given_non_get_method_without_request_header_expect_403(workspace: Path, method: str) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        assert send_request(server, f"/api/lessons/{K_LESSON_ID}", method=method).status == 403


def test_workbench_api_given_source_change_between_gets_expect_rebuilt_payload(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        before = json.loads(send_request(server, "/api/curriculum").body)
        shutil.copytree(K_REAL_LESSON_PACKAGE, workspace / "drafts" / K_LESSON_ID)
        after = json.loads(send_request(server, "/api/curriculum").body)

    assert before["lessons"][0]["source"] == "canonical"
    assert after["lessons"][0]["source"] == "draft"


def test_workbench_api_curriculum_given_unreadable_plan_expect_503_without_stale_content(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        assert send_request(server, "/api/curriculum").status == 200
        (workspace / "content" / "curriculum" / "plan.yaml").write_text("slots: [unterminated", encoding="utf-8")
        reply = send_request(server, "/api/curriculum")

    assert reply.status == 503
    assert "Greetings slot" not in reply.body
    assert reply.headers["cache-control"] == "no-store"


def test_workbench_api_lesson_given_invalid_curriculum_slot_expect_lesson_still_served(workspace: Path) -> None:
    write_curriculum_plan(
        workspace,
        CurriculumPlanFactory(
            slots=[
                CurriculumPlanSlotFactory(sequence_index=0, catalog_id=K_LESSON_ID),
                CurriculumPlanSlotFactory(sequence_index=1, catalog_id="../invalid"),
            ]
        ),
    )

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        lesson = send_request(server, f"/api/lessons/{K_LESSON_ID}")
        curriculum = send_request(server, "/api/curriculum")

    assert lesson.status == 200
    assert json.loads(lesson.body)["lessonId"] == K_LESSON_ID
    assert curriculum.status == 503


def test_workbench_api_lesson_given_malformed_exercises_expect_errors_without_elements(workspace: Path) -> None:
    draft = workspace / "drafts" / K_LESSON_ID
    shutil.copytree(K_REAL_LESSON_PACKAGE, draft)
    (draft / "exercises.yaml").write_text("- [unterminated", encoding="utf-8")

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}")

    payload = json.loads(reply.body)
    assert reply.status == 200
    assert payload["elements"] == []
    assert any("compile preview" in error for error in payload["errors"])


def test_workbench_api_curriculum_given_untrusted_slot_title_expect_verbatim_json_string(tmp_path: Path) -> None:
    hostile = "<script>alert(1)</script>"
    write_curriculum_plan(
        tmp_path, CurriculumPlanFactory(slots=[CurriculumPlanSlotFactory(catalog_id="hostile", title=hostile)])
    )

    with serving(create_workbench_server(tmp_path, "127.0.0.1", 0)) as server:
        reply = send_request(server, "/api/curriculum")
        shell = send_request(server, "/")

    assert json.loads(reply.body)["lessons"][0]["title"] == hostile
    assert reply.headers["content-type"] == "application/json; charset=utf-8"
    assert hostile not in shell.body


def test_workbench_source_route_given_canonical_lesson_expect_exact_files_and_source_rows(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}/source")
    payload = json.loads(reply.body)
    assert reply.status == 200
    assert payload["files"]["lesson.md"] == (workspace / "content" / "lessons" / K_LESSON_ID / "lesson.md").read_text()
    assert payload["blocks"][0]["key"] == "a-first-meeting"
    assert payload["draft"] == {"exists": False, "startedFrom": None, "dirty": False, "lastCommit": None}


def test_workbench_block_route_given_field_then_source_edit_expect_refreshed_draft_and_checks(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        source = json.loads(send_request(server, f"/api/lessons/{K_LESSON_ID}/source").body)
        reading = next(block for block in source["blocks"] if block["kind"] == "reading")
        route = f"/api/lessons/{K_LESSON_ID}/blocks/{reading['key'].replace('#', '%23')}"
        reply = _write_request(server, route, {"fields": {"translation": "Hello **Mina**."}})
        updated = json.loads(reply.body)
        assert reply.status == 200
        assert updated["lesson"]["source"] == "draft"
        assert updated["lesson"]["draft"]["dirty"]
        assert {"findings", "errors", "notes"} <= set(updated["check"])
        assert not any(item["origin"] == "check" for item in updated["lesson"]["state"]["findings"])
        raw = next(block for block in updated["lesson"]["blocks"] if block["key"] == reading["key"])["source"]
        reply = _write_request(server, route, {"source": raw.replace("Hello **Mina**.", "Hi again.")})
    assert reply.status == 200
    assert "Hi again." in json.loads(reply.body)["lesson"]["lessonSource"]


@pytest.mark.parametrize("name", ["plan.md", "lesson.md", "exercises.yaml"])
def test_workbench_file_route_given_allowed_name_expect_normalized_write_and_check_errors(
    workspace: Path, name: str
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = _write_request(server, f"/api/lessons/{K_LESSON_ID}/files/{name}", {"text": "malformed  \r\n\r\n"})
    assert reply.status == 200
    assert (workspace / "drafts" / K_LESSON_ID / name).read_bytes() == b"malformed\n"
    assert json.loads(reply.body)["check"]["errors"]


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("files/draft.yaml", {"text": "x"}),
        ("files/..%2Fsecret", {"text": "x"}),
        ("blocks/missing%230", {"source": "x"}),
    ],
)
def test_workbench_write_route_given_unknown_file_or_block_expect_404_and_no_draft(
    workspace: Path, suffix: str, body: dict[str, str]
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = _write_request(server, f"/api/lessons/{K_LESSON_ID}/{suffix}", body)
    assert reply.status == 404
    assert not (workspace / "drafts").exists()


@pytest.mark.parametrize("lesson_id", ["..%2F..%2Fsecret", "Bad-Slug", "unknown"])
@pytest.mark.parametrize("suffix", ["source", "files/lesson.md", "save"])
def test_workbench_editing_routes_given_escaping_or_absent_lesson_expect_404(
    workspace: Path, lesson_id: str, suffix: str
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        route = f"/api/lessons/{lesson_id}/{suffix}"
        reply = (
            send_request(server, route)
            if suffix == "source"
            else _write_request(server, route, {"text": "x"}, method="POST" if suffix == "save" else "PUT")
        )
    assert reply.status == 404


@pytest.mark.parametrize("method", ["PUT", "POST"])
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"X-Workbench-Request": "0", "Content-Type": "application/json"}, 403),
        ({"X-Workbench-Request": "1"}, 415),
        ({"X-Workbench-Request": "1", "Content-Type": "text/plain"}, 415),
        (
            {"X-Workbench-Request": "1", "Content-Type": "application/json", "Origin": "http://foreign.example:1234"},
            403,
        ),
    ],
)
def test_workbench_mutation_guard_given_missing_or_foreign_authority_expect_refusal(
    workspace: Path, method: str, headers: dict[str, str], status: int
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, f"/api/lessons/{K_LESSON_ID}/save", method=method, headers=headers, body=b"{}")
    assert reply.status == status
    assert not (workspace / "drafts").exists()


def test_workbench_save_route_given_dirty_draft_expect_commit_and_clean_draft_status(workspace: Path) -> None:
    for arguments in (
        ("init", "-b", "authoring"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
        ("add", "content"),
        ("commit", "--no-verify", "-m", "initial"),
    ):
        subprocess.run(["git", "-C", str(workspace), *arguments], check=True, capture_output=True)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        assert (
            _write_request(server, f"/api/lessons/{K_LESSON_ID}/files/lesson.md", {"text": "malformed"}).status == 200
        )
        reply = _write_request(server, f"/api/lessons/{K_LESSON_ID}/save", {}, method="POST")
        status = json.loads(send_request(server, f"/api/lessons/{K_LESSON_ID}/source").body)["draft"]
    payload = json.loads(reply.body)
    assert reply.status == 200
    assert len(payload["commit"]) == 40
    assert payload["check"]["errors"]
    assert status["lastCommit"] == payload["commit"]
    assert not status["dirty"]


def test_workbench_checks_given_resolvable_locations_expect_margin_notes_and_unmatched_top_findings(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = workspace / "content" / "lessons" / K_LESSON_ID
    actual = build_package_preview(package, source_label=package.name)
    lesson_text = (package / "lesson.md").read_text()
    line = next(index for index, text in enumerate(lesson_text.splitlines(), 1) if text.startswith("Hei!"))
    exercises = yaml.safe_load((package / "exercises.yaml").read_text())
    exercise = exercises[0].get("id", exercises[0]["handle"])
    findings = [
        {
            "artifact": "lesson.md",
            "location": f"line {line}",
            "code": "reading-check",
            "severity": "minor",
            "evidence": "Hei!",
            "explanation": "Check this reading",
        },
        {
            "artifact": "exercises.yaml",
            "location": exercises[0]["handle"],
            "code": "exercise-check",
            "severity": "major",
            "evidence": "item",
            "explanation": "Check this exercise",
        },
        {
            "artifact": "lesson.md",
            "location": "document",
            "code": "document-check",
            "severity": "minor",
            "evidence": "source",
            "explanation": "Check source",
        },
    ]
    preview = AuthorPreviewFactory(
        packet=actual.packet,
        lesson_source=actual.lesson_source,
        exercise_source=actual.exercise_source,
        plan_source=actual.plan_source,
        audit={"findings": findings},
    )
    monkeypatch.setattr("lesson_builder.web.workbench_api.build_workbench_preview", lambda package: preview)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        payload = json.loads(send_request(server, f"/api/lessons/{K_LESSON_ID}").body)
    notes = [finding for finding in payload["state"]["findings"] if finding["origin"] == "check"]
    assert [(note["code"], note["target"], note["severity"]) for note in notes] == [
        ("reading-check", {"elementId": "a-first-meeting", "blockIndex": 1}, "warning"),
        ("exercise-check", {"elementId": exercise, "blockIndex": None}, "error"),
    ]
    assert payload["sourceFindings"] == [findings[-1]]


def test_curriculum_checks_given_source_findings_and_errors_expect_lesson_page_check_count(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = workspace / "content" / "lessons" / K_LESSON_ID
    actual = build_package_preview(package, source_label=package.name)
    finding = {"artifact": "lesson.md", "location": "document", "code": "document-check", "severity": "minor"}
    preview = AuthorPreviewFactory(
        packet=actual.packet,
        lesson_source=actual.lesson_source,
        exercise_source=actual.exercise_source,
        plan_source=actual.plan_source,
        audit={"findings": [finding, finding | {"code": "second-check"}]},
        errors=("broken exercise",),
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_lessons.build_package_preview",
        lambda package, *, source_label: preview,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        counts = json.loads(send_request(server, "/api/curriculum/checks").body)["counts"]
        lesson = json.loads(send_request(server, f"/api/lessons/{K_LESSON_ID}").body)
    lesson_chip = next(chip["label"] for chip in lesson["state"]["statusChips"] if " AI" in chip["label"])
    assert counts == {K_LESSON_ID: 3}
    assert lesson_chip.startswith("3 checks")


def test_curriculum_payload_given_multiple_packages_expect_one_plan_read_and_one_digest_per_lesson(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from lesson_builder.application.operations import workbench_approval
    from lesson_builder.application.operations import workbench_curriculum
    from lesson_builder.web import workbench_api

    shutil.copytree(K_REAL_LESSON_PACKAGE, workspace / "content" / "lessons" / K_SECOND_LESSON_ID)
    read = Mock(wraps=workbench_curriculum.read_curriculum)
    digest = Mock(wraps=workbench_approval.curriculum_slot_digest)
    monkeypatch.setattr(workbench_api, "read_curriculum", read)
    monkeypatch.setattr(workbench_approval, "read_curriculum", read)
    monkeypatch.setattr(workbench_approval, "curriculum_slot_digest", digest)
    payload = workbench_api.build_curriculum_payload(workspace)
    assert len(payload["entries"]) == 2
    assert read.call_count == 1
    assert digest.call_count == 2


def test_curriculum_projection_given_review_record_invalidation_expect_status_chip_updates_without_lesson_changes(
    workspace: Path,
) -> None:
    from lesson_builder.application.operations.workbench_records import ReviewLog
    from lesson_builder.web import workbench_api

    first = workbench_api.build_curriculum_payload(workspace)
    source = read_workbench_source(workspace, K_LESSON_ID)
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=[
            ReviewFinding(
                finding_id="current-review",
                key="a-first-meeting",
                criterion="naturalness",
                severity="minor",
                excerpt="Hei",
                explanation="Current review finding",
            )
        ],
    )
    write_workbench_record(workspace, "reviews", K_LESSON_ID, ReviewLog(runs=[run]))
    repeated = workbench_api.build_curriculum_payload(workspace)

    assert repeated["lessons"] == first["lessons"]
    assert {chip["label"] for chip in repeated["entries"][K_LESSON_ID]["statusChips"]} >= {"1 AI"}


def test_lesson_payload_given_legacy_baseline_expect_one_plan_read_and_one_slot_digest(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    from lesson_builder.application.operations import workbench_approval
    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
    from lesson_builder.web.workbench_api import build_lesson_payload

    baseline = workspace / "content" / "approvals" / "lessons" / K_LESSON_ID / "seed"
    shutil.copytree(K_REAL_LESSON_PACKAGE, baseline / "source")
    (baseline / "approval.yaml").write_text(f"lesson_id: {K_LESSON_ID}\napproved_at: seed\n")
    read = Mock(wraps=workbench_approval.read_curriculum)
    digest = Mock(wraps=workbench_approval.curriculum_slot_digest)
    monkeypatch.setattr(workbench_approval, "read_curriculum", read)
    monkeypatch.setattr(workbench_approval, "curriculum_slot_digest", digest)
    build_lesson_payload(workspace, resolve_lesson_package(workspace, K_LESSON_ID))
    assert read.call_count == 1
    assert digest.call_count == 1


def test_lesson_payload_given_regeneration_proposal_expect_keep_only_for_current_exercise(
    workspace: Path,
) -> None:
    from lesson_builder.application.operations.workbench_editing import read_workbench_source
    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
    from lesson_builder.application.operations.workbench_records import Proposal
    from lesson_builder.application.operations.workbench_records import ProposalBlock
    from lesson_builder.application.operations.workbench_records import ProposalLog
    from lesson_builder.web.workbench_api import build_lesson_payload

    source = read_workbench_source(workspace, K_LESSON_ID)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    reading = next(block for block in source.blocks if block.kind == "reading")
    proposal = Proposal(
        proposal_id="regenerated-blocks",
        lesson_id=K_LESSON_ID,
        source="regenerate",
        created_against_sha256="0" * 64,
        note="Regenerated scope",
        blocks=[
            ProposalBlock(
                key=exercise.key, before=exercise.source, after=exercise.source + "# revised\n", note="exercise"
            ),
            ProposalBlock(
                key=reading.key, before=reading.source, after=reading.source + "<!-- revised -->\n", note="reading"
            ),
        ],
    )
    write_workbench_record(workspace, "proposals", K_LESSON_ID, ProposalLog(proposals=[proposal]))

    payload = build_lesson_payload(workspace, resolve_lesson_package(workspace, K_LESSON_ID))

    allowed = {block["key"]: block["allowedDecisions"] for block in payload["state"]["proposals"]}
    assert allowed == {exercise.key: ["accept", "reject", "keep"], reading.key: ["accept", "reject"]}


def test_lesson_payload_given_canonical_indentless_plan_sequences_expect_structured_constraints(
    workspace: Path,
) -> None:
    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
    from lesson_builder.web.workbench_api import build_lesson_payload

    lesson_id = "agree_on_informal_plans"
    canonical_package = Path(__file__).parents[2] / "content" / "lessons" / lesson_id
    shutil.copytree(canonical_package, workspace / "content" / "lessons" / lesson_id)

    payload = build_lesson_payload(workspace, resolve_lesson_package(workspace, lesson_id))

    assert payload["planPreview"]["frontmatter"]["prerequisites"] == ["invite_someone_and_respond"]
    assert payload["planPreview"]["frontmatter"]["exclusions"] == [
        "open speaking assessment",
        "learner-specific scoring",
    ]
    assert payload["planSource"].startswith("---\npackage_version:")


def _write_request(server: HTTPServer, route: str, body: dict[str, Any], *, method: str = "PUT") -> HttpReply:
    return send_request(
        server,
        route,
        method=method,
        headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        body=json.dumps(body).encode(),
    )


@pytest.mark.parametrize(
    "field,value,stale",
    [
        ("sequence_index", 5, False),
        ("title", "New title", True),
        ("learner_outcome", "New outcome", True),
        ("prerequisites", [K_SECOND_LESSON_ID], True),
    ],
)
def test_curriculum_chips_given_saved_slot_change_expect_only_content_changes_stale(
    workspace: Path,
    field: str,
    value: object,
    stale: bool,
) -> None:
    from lesson_builder.application.operations.workbench_curriculum import read_curriculum
    from lesson_builder.application.operations.workbench_records import CurriculumBaseline
    from lesson_builder.web.workbench_api import build_curriculum_payload

    plan = read_curriculum(workspace)
    slot = plan.slots[0]
    write_workbench_record(
        workspace, "curriculum", "baseline", CurriculumBaseline(slots={K_LESSON_ID: slot.model_dump_json()})
    )
    changed = plan.model_copy(update={"slots": [slot.model_copy(update={field: value}), plan.slots[1]]})
    (workspace / "content/curriculum/plan.yaml").write_text(yaml.safe_dump(changed.model_dump(mode="json")))

    payload = build_curriculum_payload(workspace)

    labels = [chip["label"] for chip in payload["entries"][K_LESSON_ID]["statusChips"]]
    assert ("plan stale" in labels) is stale
