"""Entry point: guarded curriculum write routes and progress polling tests."""

import json
import threading
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_records import CurriculumBaseline
from lesson_builder.web.server import create_workbench_server
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.fakes import FakeLlmClient
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.web.serving import send_request
from tests.web.serving import serving
from tests.workspace.builders import build_workbench_author_response
from tests.workspace.builders import build_workbench_curriculum_repository

K_TEST_NEW_LESSON_JOB_TIMEOUT_SECONDS = 300


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return build_workbench_curriculum_repository(tmp_path)


def test_curriculum_route_given_legacy_baseline_expect_successful_payload(workspace: Path) -> None:
    slot = read_curriculum(workspace).slots[0]
    legacy_slot = slot.model_dump(mode="json") | {"human_gate": "pending"}
    write_workbench_record(
        workspace, "curriculum", "baseline", CurriculumBaseline(slots={slot.catalog_id: json.dumps(legacy_slot)})
    )

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(server, "/api/curriculum")

    assert reply.status == 200
    assert slot.catalog_id in json.loads(reply.body)["entries"]


@pytest.mark.parametrize(
    "route,body",
    [
        (
            "/api/curriculum/save",
            {"edits": [{"kind": "reorder", "lesson_ids": ["second", "greet_and_introduce_yourself"]}]},
        ),
        (
            "/api/curriculum/save",
            {
                "edits": [
                    {
                        "kind": "update",
                        "lesson_id": "second",
                        "title": "Edited title",
                        "learner_outcome": "Use a phrase",
                        "cefr_level": "A2",
                        "prerequisites": ["greet_and_introduce_yourself"],
                        "helpful_prerequisites": [],
                    }
                ]
            },
        ),
    ],
)
def test_curriculum_write_route_given_valid_payload_expect_commit_and_fresh_state(
    workspace: Path, route: str, body: dict
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        preview_reply = send_request(
            server,
            "/api/curriculum/preview",
            method="POST",
            body=json.dumps({"edits": body["edits"]}).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
        preview = json.loads(preview_reply.body)
        body.update(
            review_digest=preview["reviewDigest"],
        )
        reply = send_request(
            server,
            route,
            method="PUT",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
    assert reply.status == 200
    payload = json.loads(reply.body)
    assert len(payload["commit"]) == 40
    assert payload["curriculum"]["entries"]["second"]["learnerOutcome"]
    if payload["curriculum"]["lessons"][0]["lessonId"] == "second":
        assert payload["findings"][0]["lessonId"] == "second"
        assert payload["curriculum"]["lessons"][0]["lessonId"] == "second"
    else:
        assert payload["curriculum"]["lessons"][1]["title"] == "Edited title"


@pytest.mark.parametrize(
    "method,route,body",
    [
        (
            "PUT",
            "/api/curriculum/save",
            {"edits": [{"kind": "reorder", "lesson_ids": ["second", "greet_and_introduce_yourself"]}]},
        ),
        (
            "POST",
            "/api/curriculum/preview",
            {"edits": [{"kind": "remove", "lesson_ids": ["greet_and_introduce_yourself"]}]},
        ),
        (
            "POST",
            "/api/curriculum/lessons",
            {"title": "Title", "learner_outcome": "Outcome", "cefr_level": "A1", "slug": "new_lesson"},
        ),
    ],
)
@pytest.mark.parametrize("guard", ["header", "origin", "host", "content_type"])
def test_curriculum_write_route_given_rejected_guard_expect_no_mutations(
    workspace: Path, method: str, route: str, body: dict, guard: str
) -> None:
    before = (workspace / "content" / "curriculum" / "plan.yaml").read_bytes()
    headers = {"Content-Type": "application/json", "X-Workbench-Request": "1"}
    if guard == "header":
        del headers["X-Workbench-Request"]
    if guard == "origin":
        headers["Origin"] = "https://untrusted.example"
    if guard == "content_type":
        headers["Content-Type"] = "text/plain"
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(
            server,
            route,
            method=method,
            body=json.dumps(body).encode(),
            headers=headers,
            host="untrusted.example" if guard == "host" else None,
        )
    assert reply.status == (400 if guard == "origin" else 415 if guard == "content_type" else 403)
    assert (workspace / "content" / "curriculum" / "plan.yaml").read_bytes() == before
    assert not (workspace / "drafts").exists()


def test_new_lesson_route_given_slow_invalid_client_expect_progress_failure_errors_and_no_saved_entry(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    released = threading.Event()

    def respond(prompt: str, model: str | None) -> str:
        entered.set()
        released.wait(K_TEST_HANG_GUARD_SECONDS)
        return "invalid"

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        try:
            reply = send_request(
                server,
                "/api/curriculum/lessons",
                method="POST",
                body=json.dumps(
                    {"title": "Title", "learner_outcome": "Outcome", "cefr_level": "A1", "slug": "new_lesson"}
                ).encode(),
                headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
            )
            assert reply.status == 202
            identity = json.loads(reply.body)["job_id"]
            assert entered.wait(K_TEST_HANG_GUARD_SECONDS)
            running = json.loads(send_request(server, f"/api/jobs/{identity}").body)
            assert running["steps"] == ["adding to curriculum", "drafting lesson"]
        finally:
            released.set()
        server.jobs.wait(identity, timeout=K_TEST_NEW_LESSON_JOB_TIMEOUT_SECONDS)
        result = json.loads(send_request(server, f"/api/jobs/{identity}").body)
        assert result["status"] == "failed"
        assert result["check"]["errors"]
        assert "No curriculum changes or draft were saved" in result["error"]
        assert "Invalid JSON" in result["error"]
        assert result["check"]["errors"][0] in result["error_detail"]
        assert not any(
            lesson["lessonId"] == "new_lesson"
            for lesson in json.loads(send_request(server, "/api/curriculum").body)["lessons"]
        )


def test_new_lesson_route_given_complete_response_expect_draft_only_in_list_and_lesson_tab(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeLlmClient.responding(build_workbench_author_response(workspace))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(
            server,
            "/api/curriculum/lessons",
            method="POST",
            body=json.dumps(
                {
                    "title": "New title",
                    "learner_outcome": "New outcome.",
                    "cefr_level": "A1",
                    "slug": "new_lesson",
                    "after_lesson_id": "greet_and_introduce_yourself",
                }
            ).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
        assert reply.status == 202
        identity = json.loads(reply.body)["job_id"]
        server.jobs.wait(identity, timeout=K_TEST_NEW_LESSON_JOB_TIMEOUT_SECONDS)
        job = json.loads(send_request(server, f"/api/jobs/{identity}").body)
        assert job["status"] == "done"
        assert job["result"]["check"] == {"status": "passed", "errors": []}
        curriculum = json.loads(send_request(server, "/api/curriculum").body)
        lesson = next(item for item in curriculum["lessons"] if item["lessonId"] == "new_lesson")
        assert lesson["source"] == "draft"
        preview = send_request(server, "/api/lessons/new_lesson")
        assert preview.status == 200
        payload = json.loads(preview.body)
        assert payload["source"] == "draft"
        assert payload["sourceLabel"] == "drafts/new_lesson"
        assert payload["elements"]
    assert not (workspace / "content" / "lessons" / "new_lesson").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "learner_outcome": "Outcome", "cefr_level": "A1", "slug": "new_lesson"},
        {"title": "Title", "learner_outcome": "Outcome", "cefr_level": "A1", "slug": "../../secret"},
        {"title": "Title", "learner_outcome": "Outcome", "cefr_level": "A1", "slug": "second"},
    ],
)
def test_new_lesson_route_given_invalid_creation_expect_refusal_before_job(workspace: Path, body: dict) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(
            server,
            "/api/curriculum/lessons",
            method="POST",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
    assert reply.status in (404, 409)
    assert not (workspace / "drafts").exists()


def test_new_lesson_route_given_detached_head_expect_english_refusal_before_provider(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from unittest.mock import Mock

    subprocess.run(["git", "-C", str(workspace), "checkout", "--detach"], check=True, capture_output=True)
    provider = Mock(side_effect=AssertionError("provider must not run"))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", provider)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(
            server,
            "/api/curriculum/lessons",
            method="POST",
            body=json.dumps(
                {"title": "New title", "learner_outcome": "New outcome.", "cefr_level": "A1", "slug": "new_lesson"}
            ).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
    assert reply.status == 409
    assert (
        json.loads(reply.body)["error"]
        == "Saving drafts needs a checked-out branch; this workspace is on a detached HEAD."
    )
    provider.assert_not_called()
