"""Entry point: owner lifecycle and checkpointed regeneration HTTP integration tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from _thread import RLock
from http.server import HTTPServer
from pathlib import Path
from threading import Event

import pytest
import yaml

from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_audio import synthesize_workbench_audio
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_promotion import canonical_source_digest
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.web.server import create_workbench_server
from lesson_builder.workflow.workbench_regeneration.runner import execute_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import start_regeneration
from lesson_builder.workspace.workbench_store import read_workbench_record
from tests.application.fakes import FakeLlmClient
from tests.application.fakes import FakeTtsClient
from tests.application.fakes import fake_audio_for_text
from tests.application.fakes import workbench_review_reply
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexTurn
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.web.serving import HttpReply
from tests.web.serving import send_request
from tests.web.serving import serving


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = Path(__file__).parents[2]
    lesson_id = "greet_and_introduce_yourself"
    for relative in (
        f"content/lessons/{lesson_id}",
        "content/authoring",
        "content/terminology",
        "content/catalog",
        ".opencode/agents",
    ):
        shutil.copytree(root / relative, tmp_path / relative)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    plan = yaml.safe_load((root / "content/curriculum/plan.yaml").read_bytes())
    plan["slots"] = [slot for slot in plan["slots"] if slot["catalog_id"] == lesson_id]
    path = tmp_path / "content/curriculum/plan.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(plan))
    _git(tmp_path, "init", "-b", "authoring")
    _git(tmp_path, "config", "user.name", "Author")
    _git(tmp_path, "config", "user.email", "author@example.test")
    _git(tmp_path, "add", "content", "config.yaml", ".opencode")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_owner_routes_given_edit_without_review_expect_promoted_dist(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    write_workbench_file(workspace, lesson_id, "lesson.md", source.files["lesson.md"] + "\nOwner note.\n")
    audio_plan = plan_workbench_audio(workspace, lesson_id, "missing_and_stale")
    synthesize_workbench_audio(
        workspace,
        audio_plan.token,
        synthesis_client=FakeTtsClient.from_fn(fake_audio_for_text),
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        promoted = _post(server, f"/api/lessons/{lesson_id}/promote", {})
        assert promoted.status == 200
        payload = json.loads(promoted.body)
        assert payload["lesson"]["source"] == "canonical"
        assert f"dist/lessons/{lesson_id}.json" in payload["dist_files"]
        assert payload["commit"]
        assert "approvalStages" not in payload["lesson"]["state"]
    assert not (workspace / "drafts" / lesson_id).exists()


def test_owner_routes_given_canonical_conflict_expect_409_diff_pull_and_confirmed_discard(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _reviewed_edit(workspace, lesson_id)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        canonical = workspace / "content/lessons" / lesson_id / "lesson.md"
        canonical.write_text(canonical.read_text().replace('translation="', 'translation="Canonical: ', 1))
        reply = _post(server, f"/api/lessons/{lesson_id}/promote", {})
        assert reply.status == 409
        payload = json.loads(reply.body)
        assert payload["error"] == "canonical_changed"
        assert "Canonical:" in payload["diff"]
        pulled = _post(server, f"/api/lessons/{lesson_id}/pull", {})
        assert pulled.status == 200
        assert json.loads(pulled.body)["proposal"]["source"] == "canonical"
        assert _post(server, f"/api/lessons/{lesson_id}/discard", {}).status == 409
        assert (workspace / "drafts" / lesson_id).exists()
        assert _post(server, f"/api/lessons/{lesson_id}/discard", {"confirmed": True}).status == 200
    assert "Canonical:" in canonical.read_text()
    assert not (workspace / "drafts" / lesson_id).exists()


def test_owner_routes_given_canonical_matches_draft_expect_provenance_updated_without_empty_proposal(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _reviewed_edit(workspace, lesson_id)
    source = read_workbench_source(workspace, lesson_id)
    canonical = workspace / "content/lessons" / lesson_id
    for name, text in source.files.items():
        (canonical / name).write_text(text)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        conflict = _post(server, f"/api/lessons/{lesson_id}/promote", {})
        assert conflict.status == 409
        pulled = _post(server, f"/api/lessons/{lesson_id}/pull", {})
        assert pulled.status == 200
        payload = json.loads(pulled.body)
        assert payload["message"] == "Canonical already matches; provenance updated."
        assert "proposal" not in payload
        assert payload["lesson"]["draft"]["startedFrom"] == canonical_source_digest(workspace, lesson_id)
        assert payload["lesson"]["draft"]["startedFrom"] != source.started_from


@pytest.mark.parametrize("chat", [False, True])
def test_regeneration_routes_given_section_request_expect_persistent_run_proposal_and_resume(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, chat: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    key = reading.key.split("#")[0]
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Changed",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    chat_client = FakeCodexAppServer(
        FakeCodexTurn(text=json.dumps({"reply": "Regenerating section", "edits": [], "regenerate": key}))
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=chat_client)) as server:
        reply = _post(
            server,
            f"/api/lessons/{lesson_id}/{'chat' if chat else 'regenerate'}",
            {"message": "Rewrite the whole section"} if chat else {"mode": "scope", "key": key},
        )
        assert reply.status == 202
        ticket = json.loads(reply.body)
        job = server.jobs.wait(ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
        assert job["status"] == "done", job
        identity = job["run_id"] if chat else ticket["run_id"]
        run = json.loads(send_request(server, f"/api/runs/{identity}").body)
        assert run["status"] == "waiting"
        assert run["mode"] == "scope"
        assert run["steps"] == ["generate", "check", "review", "waiting for you"]
        assert run["key"] == key
        proposal = run["proposal_id"]
        rejected = _post(server, f"/api/lessons/{lesson_id}/proposals/{proposal}/reject", {})
        assert rejected.status == 200
        resumed = json.loads(send_request(server, f"/api/runs/{identity}").body)
        assert resumed["status"] == "done"
        assert resumed["decision"] == "reject"
        assert send_request(server, "/api/runs/../escape").status == 404
        assert send_request(server, f"/api/runs/{identity}", host="foreign.invalid").status == 403


def test_regeneration_run_given_provider_call_is_running_expect_active_job_id_in_run_projection(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    entered = Event()
    release = Event()

    def wait_for_release(prompt: str, model: str | None) -> str:
        del prompt, model
        entered.set()
        if not release.wait(K_TEST_HANG_GUARD_SECONDS):
            raise AssertionError("fake regeneration provider was not released")
        return json.dumps(
            {
                "reply": "Changed",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )

    author = FakeLlmClient.from_fn(wait_for_release)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        try:
            reply = _post(
                server,
                f"/api/lessons/{lesson_id}/regenerate",
                {"mode": "scope", "key": reading.key.split("#")[0]},
            )
            ticket = json.loads(reply.body)
            assert ticket["run_id"]
            assert entered.wait(K_TEST_HANG_GUARD_SECONDS), "regeneration did not reach the fake provider"

            run = json.loads(send_request(server, f"/api/runs/{ticket['run_id']}").body)

            assert run["status"] == "running"
            assert run["job_id"] == ticket["job_id"]
            release.set()
            job = server.jobs.wait(ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
            assert job["status"] == "done", job
        finally:
            release.set()


def test_server_restart_given_captured_run_expect_automatic_checkpoint_recovery(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Changed",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        job_id = server.jobs.find_active_job_for_run(identity)
        if job_id is not None:
            job = server.jobs.wait(job_id, timeout=K_TEST_HANG_GUARD_SECONDS)
            assert job is not None and job["status"] == "done", job
        run = json.loads(send_request(server, f"/api/runs/{identity}").body)
        assert run["status"] == "waiting", run
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as restarted:
        run = json.loads(send_request(restarted, f"/api/runs/{identity}").body)
        assert run["status"] == "waiting"
    assert len(author.calls) == 1


def test_server_restart_given_shutdown_during_regeneration_expect_run_resumed_at_next_start(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    provider_entered = Event()
    response = json.dumps(
        {
            "reply": "Changed",
            "edits": [
                {
                    "key": reading.key,
                    "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                    "note": "translation",
                }
            ],
        }
    )

    def respond(_prompt: str, _model: str | None) -> str:
        if len(author.calls) == 1:
            cancellation = author.cancellation_signals[-1]
            assert cancellation is not None
            provider_entered.set()
            assert cancellation.wait(K_TEST_HANG_GUARD_SECONDS), (
                "server shutdown did not cancel the active provider call"
            )
        return response

    author = FakeLlmClient.from_fn(respond)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        ticket_reply = _post(
            server,
            f"/api/lessons/{lesson_id}/regenerate",
            {"mode": "scope", "key": reading.key.split("#")[0]},
        )
        ticket = json.loads(ticket_reply.body)
        assert ticket_reply.status == 202
        assert provider_entered.wait(K_TEST_HANG_GUARD_SECONDS)

        server.jobs.close()

        job = server.jobs.find(ticket["job_id"])
        run = json.loads(send_request(server, f"/api/runs/{ticket['run_id']}").body)
        assert job is not None and job["status"] == "cancelled"
        assert run["status"] == "shutdown_interrupted"

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as restarted:
        active_job = restarted.jobs.find_active_job_for_run(ticket["run_id"])
        if active_job is not None:
            result = restarted.jobs.wait(active_job, timeout=K_TEST_HANG_GUARD_SECONDS)
            assert result is not None and result["status"] == "done", result
        resumed = json.loads(send_request(restarted, f"/api/runs/{ticket['run_id']}").body)

    assert resumed["status"] == "waiting"
    assert len(author.calls) == 2


def test_server_restart_given_unchanged_exercise_expect_kept_without_retry(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    write_workbench_file(
        workspace,
        lesson_id,
        "lesson.md",
        source.files["lesson.md"].replace("Two new students arrive early", "Two students arrive early", 1),
    )
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    calls = 0

    def unchanged_reply(_prompt: str, _model: str | None) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "reply": "No change",
                "edits": [{"key": exercise.key, "after_source": exercise.source, "note": "unchanged"}],
            }
        )

    author = FakeLlmClient.from_fn(unchanged_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: author)
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])
    result = execute_regeneration(workspace, identity, RLock())

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as restarted:
        run = json.loads(send_request(restarted, f"/api/runs/{identity}").body)

    assert result["status"] == "done"
    assert run["status"] == "done"
    assert run["decision"] == "kept unchanged by regeneration"
    assert run["kept_exercises"] == [exercise.key]
    assert len(author.calls) == 1


def test_server_restart_given_cancelled_run_expect_terminal_cancelled_without_recovery(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    author_started = Event()
    release_author = Event()

    def respond(prompt: str, model: str | None) -> str:
        if "ChatResponse" in prompt:
            author_started.set()
            assert release_author.wait(K_TEST_HANG_GUARD_SECONDS)
            return json.dumps(
                {
                    "reply": "Changed",
                    "edits": [
                        {
                            "key": reading.key,
                            "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                            "note": "translation",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    author = FakeLlmClient.from_fn(respond)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    chat_client = FakeCodexAppServer(
        FakeCodexTurn(text=json.dumps({"reply": "Regenerating section", "edits": [], "regenerate": reading.key}))
    )
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=chat_client)) as server:
        ticket = json.loads(_post(server, f"/api/lessons/{lesson_id}/chat", {"message": "Rewrite the reading"}).body)
        assert author_started.wait(K_TEST_HANG_GUARD_SECONDS)
        cancelled = _post(server, f"/api/lessons/{lesson_id}/chat/{ticket['turn_id']}/cancel", {})
        assert json.loads(cancelled.body)["cancelled"] is True
        release_author.set()
        job = server.jobs.wait(ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
        assert job["status"] == "cancelled"
        run = json.loads(send_request(server, f"/api/runs/{job['run_id']}").body)
        assert run["status"] == "cancelled"

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as restarted:
        resumed = json.loads(send_request(restarted, f"/api/runs/{job['run_id']}").body)

    assert resumed["status"] == "cancelled"
    assert resumed["errors"] == ["Chat turn cancelled."]
    assert len(author.calls) == 1
    assert read_workbench_record(workspace, "proposals", lesson_id, ProposalLog).proposals == []


def _reviewed_edit(root: Path, lesson_id: str) -> None:
    source = read_workbench_source(root, lesson_id)
    write_workbench_file(
        root, lesson_id, "lesson.md", source.files["lesson.md"].replace('translation="', 'translation="Hello: ', 1)
    )
    client = FakeLlmClient.from_fn(workbench_review_reply)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
        review_workbench_lesson(root, lesson_id, "lesson", None, RLock())


def _post(server: HTTPServer, route: str, body: object) -> HttpReply:
    return send_request(
        server,
        route,
        method="POST",
        headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        body=json.dumps(body).encode(),
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.mark.parametrize("action", ["save", "promote"])
def test_owner_route_given_detached_head_expect_shared_branch_precondition_without_commit(
    workspace: Path, action: str
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _reviewed_edit(workspace, lesson_id)
    if action == "promote":
        audio = plan_workbench_audio(workspace, lesson_id, "missing_and_stale")
        synthesize_workbench_audio(workspace, audio.token, synthesis_client=FakeTtsClient.from_fn(fake_audio_for_text))
    _git(workspace, "checkout", "--detach")
    head = _git(workspace, "rev-parse", "HEAD")
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = _post(server, f"/api/lessons/{lesson_id}/{action}", {})
    assert reply.status == 409
    assert (
        json.loads(reply.body)["error"]
        == "Saving drafts needs a checked-out branch; this workspace is on a detached HEAD."
    )
    assert _git(workspace, "rev-parse", "HEAD") == head
    assert _git(workspace, "diff", "--cached", "--name-only") == ""
