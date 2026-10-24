"""Entry point: guarded asynchronous workbench chat, review and proposal HTTP behavior tests."""

from __future__ import annotations

import json
import shutil
from http.server import HTTPServer
from pathlib import Path
from threading import Event
from urllib.parse import quote

import pytest

from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_proposals import create_proposal
from lesson_builder.application.operations.workbench_proposals import store_proposal
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.web.server import create_workbench_server
from tests.application.fakes import FakeLlmClient
from tests.application.fakes import workbench_review_reply
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexGate
from tests.clients.codex.fakes import FakeCodexTurn
from tests.domain.curriculum.factories import CurriculumPlanFactory
from tests.domain.curriculum.factories import CurriculumPlanSlotFactory
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.web.serving import HttpReply
from tests.web.serving import send_request
from tests.web.serving import serving
from tests.workspace.builders import write_curriculum_plan


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = Path(__file__).parents[2]
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(root / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    write_curriculum_plan(tmp_path, CurriculumPlanFactory(slots=[CurriculumPlanSlotFactory(catalog_id=lesson_id)]))
    return tmp_path


@pytest.mark.parametrize("route", ["/api/curriculum/chat", "/api/lessons/greet_and_introduce_yourself/chat"])
def test_chat_route_given_slow_client_expect_immediate_ticket_running_then_persisted_reply(
    workspace: Path, route: str
) -> None:
    gate = FakeCodexGate.create()
    response = (
        '{"reply": "Curriculum context.", "proposed_edit": null}'
        if route.startswith("/api/curriculum")
        else '{"reply": "Clearer wording.", "edits": [], "regenerate": null}'
    )
    partial = '{"reply":"Curriculum' if route.startswith("/api/curriculum") else '{"reply":"Clearer'
    client = FakeCodexAppServer(FakeCodexTurn(text=response, gate=gate, deltas=(partial,)))
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=client)) as server:
        try:
            reply = _post(server, route, {"message": "Explain"})
            assert reply.status == 202
            ticket = json.loads(reply.body)
            identity = ticket["job_id"]
            assert ticket["turn_id"]
            assert gate.entered.wait(K_TEST_HANG_GUARD_SECONDS)
            job = json.loads(send_request(server, f"/api/jobs/{identity}").body)
            assert job["status"] == "running"
            assert job["partial"] == ("Curriculum" if route.startswith("/api/curriculum") else "Clearer")
            assert send_request(server, "/api/curriculum").status == 200
        finally:
            gate.release.set()
        result = server.jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
        assert result["status"] == "done"
        payload = json.loads(send_request(server, route.removesuffix("/chat")).body)
        transcript = payload["chat"] if route.startswith("/api/curriculum") else payload["state"]["chat"]
        assert transcript[-1]["text"] == result["result"]["reply"]


def test_explanation_given_running_turn_expect_active_metadata_outside_visible_chat(workspace: Path) -> None:
    gate = FakeCodexGate.create()
    client = FakeCodexAppServer(
        FakeCodexTurn(text='{"translations": [], "item_tested": "greeting"}', gate=gate, deltas=("partial",))
    )
    lesson_id = "greet_and_introduce_yourself"
    block_id = next(
        block.key
        for block in read_workbench_source(workspace, lesson_id).blocks
        if block.kind in ("exercise", "reading")
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=client)) as server:
        try:
            reply = _post(server, f"/api/lessons/{lesson_id}/explain", {"block_id": block_id})
            assert reply.status == 202
            ticket = json.loads(reply.body)
            assert gate.entered.wait(K_TEST_HANG_GUARD_SECONDS)

            payload = json.loads(send_request(server, f"/api/lessons/{lesson_id}").body)

            assert not any(turn.get("turnId") == ticket["turn_id"] for turn in payload["state"]["chat"])
            assert payload["state"]["activeTurn"] == {
                "turnId": ticket["turn_id"],
                "status": "running",
                "partial": "",
            }
        finally:
            gate.release.set()


def test_jobs_route_given_unknown_id_or_failed_provider_expect_404_or_failed_error(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeCodexAppServer(FakeCodexTurn(error=CodexAppServerError("provider rejected the request")))
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=client)) as server:
        assert send_request(server, "/api/jobs/unknown").status == 404
        reply = _post(server, "/api/lessons/greet_and_introduce_yourself/chat", {"message": "Explain"})
        result = server.jobs.wait(json.loads(reply.body)["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
    assert result == {
        "status": "failed",
        "error": "The Codex call failed.",
        "error_detail": "CodexAppServerError: provider rejected the request",
        "can_cancel": False,
    }
    diagnostic = capsys.readouterr().err
    assert "Workbench job " in diagnostic and "CodexAppServerError" in diagnostic
    assert "Traceback (most recent call last)" in diagnostic
    assert "run_workbench_chat_turn" in diagnostic
    assert "provider rejected the request" not in diagnostic


def test_provider_job_given_cancel_request_expect_conflict_and_non_cancelable_job_state(workspace: Path) -> None:
    gate = Event()
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=FakeCodexAppServer())) as server:
        entered = Event()

        def hold_job() -> None:
            entered.set()
            gate.wait(K_TEST_HANG_GUARD_SECONDS)

        identity = server.jobs.submit(hold_job)
        try:
            assert entered.wait(K_TEST_HANG_GUARD_SECONDS)
            job = server.jobs.find(identity)
            reply = _post(server, f"/api/jobs/{identity}/cancel", {})
            assert reply.status == 409
            assert json.loads(reply.body) == {"error": "This job cannot be cancelled."}
            assert job["can_cancel"] is False
        finally:
            gate.set()


def test_explicit_regeneration_job_given_provider_wait_expect_cancelled_job(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    provider_entered = Event()
    release_provider = Event()
    response = json.dumps(
        {
            "reply": "Regenerated",
            "edits": [
                {
                    "key": block.key,
                    "after_source": block.source.replace('translation="', 'translation="Hello: ', 1),
                    "note": "translation",
                }
            ],
        }
    )

    def respond(prompt: str, model: str | None) -> str:
        if "ChatResponse" not in prompt:
            return workbench_review_reply(prompt, model)
        provider_entered.set()
        release_provider.wait(K_TEST_HANG_GUARD_SECONDS)
        return response

    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: FakeLlmClient.from_fn(respond)
    )
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        try:
            ticket_reply = _post(
                server,
                f"/api/lessons/{lesson_id}/regenerate",
                {"mode": "scope", "key": block.key},
            )
            ticket = json.loads(ticket_reply.body)
            assert ticket_reply.status == 202
            assert provider_entered.wait(K_TEST_HANG_GUARD_SECONDS)

            job = json.loads(send_request(server, f"/api/jobs/{ticket['job_id']}").body)
            assert job["status"] == "running"
            assert job["can_cancel"] is True

            cancelled = _post(server, f"/api/jobs/{ticket['job_id']}/cancel", {})
            assert cancelled.status == 200
            assert json.loads(cancelled.body) == {"cancelled": True}
            assert server.jobs.wait(ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)["status"] == "cancelled"
            run = json.loads(send_request(server, f"/api/runs/{ticket['run_id']}").body)
            assert run["status"] == "cancelled"
        finally:
            release_provider.set()


@pytest.mark.parametrize(
    "suffix",
    [
        "chat",
        "review",
        "approve",
        "promote",
        "pull",
        "discard",
        "regenerate",
        "findings/f1/fix",
        "findings/f1/resolve",
        "proposals/p1/accept",
        "proposals/p1/reject",
        "proposals/p1/blocks/intro%230/accept",
        "proposals/p1/blocks/intro%230/reject",
    ],
)
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"X-Workbench-Request": "1"}, 415),
        (
            {"X-Workbench-Request": "1", "Content-Type": "application/json", "Origin": "http://foreign.invalid:47700"},
            403,
        ),
    ],
)
def test_agent_write_guards_given_missing_header_json_or_foreign_origin_expect_refusal(
    workspace: Path, suffix: str, headers: dict[str, str], status: int
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = send_request(
            server, f"/api/lessons/greet_and_introduce_yourself/{suffix}", method="POST", headers=headers, body=b"{}"
        )
    assert reply.status == status
    assert not (workspace / "store").exists()


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_proposal_routes_given_real_proposal_expect_per_block_or_bulk_draft_decisions(
    workspace: Path, action: str
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    blocks = [block for block in source.blocks if block.kind == "reading"][:2]
    proposal, _ = create_proposal(
        lesson_id,
        source,
        ChatResponse.model_validate(
            {
                "reply": "Improve",
                "edits": [
                    {
                        "key": block.key,
                        "after_source": block.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "wording",
                    }
                    for block in blocks
                ],
            }
        ),
        origin="chat",
    )
    assert proposal is not None
    store_proposal(workspace, lesson_id, proposal)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        route = f"/api/lessons/{lesson_id}/proposals/{proposal.proposal_id}"
        single = _post(server, f"{route}/blocks/{quote(blocks[0].key, safe='')}/{action}", {})
        assert single.status == 200
        assert json.loads(single.body)["proposal"]["blocks"][0]["status"] == (
            "accepted" if action == "accept" else "rejected"
        )
        bulk = _post(server, f"{route}/{action}", {})
    assert bulk.status == 200
    assert json.loads(bulk.body)["proposal"]["status"] == "settled"
    assert (workspace / "drafts").exists() == (action == "accept")


def test_review_resolve_fix_routes_given_scoped_review_expect_mapped_findings_resolution_and_fix_proposal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")

    def respond(prompt: str, model: str | None) -> str:
        if "PedagogyReview" in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                    ),
                    "summary": "One wording issue.",
                    "passes": [],
                    "issues": [
                        {
                            "category": "bokmal",
                            "severity": "P2",
                            "title": "Wording",
                            "evidence": block.fields["body"],
                            "fix": "Improve wording",
                        }
                    ],
                }
            )
        return '{"scores": {"idiomatic_phrasing": 4, "register_appropriateness": 4, "terminology_consistency": 4}, "issues": []}'

    client = FakeLlmClient.from_fn(respond)
    codex_client = FakeCodexAppServer(
        FakeCodexTurn(
            text=json.dumps(
                {
                    "reply": "Fixed the finding.",
                    "edits": [
                        {
                            "key": block.key,
                            "after_source": block.source.replace('translation="', 'translation="Hello: ', 1),
                            "note": "wording",
                        }
                    ],
                    "regenerate": None,
                }
            )
        )
    )
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=codex_client)) as server:
        route = f"/api/lessons/{lesson_id}"
        ticket = _post(server, route + "/review", {"scope": "item", "key": block.key})
        result = server.jobs.wait(json.loads(ticket.body)["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
        assert result["status"] == "done"
        finding = result["result"]["findings"][0]
        before = read_workbench_source(workspace, lesson_id).files
        fixed = _post(server, route + "/findings/" + finding["finding_id"] + "/fix", {})
        proposal = server.jobs.wait(json.loads(fixed.body)["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)
        assert proposal["status"] == "done"
        assert proposal["result"]["proposal"]["source"] == "review_fix"
        assert read_workbench_source(workspace, lesson_id).files == before
        payload = json.loads(send_request(server, route).body)
        assert payload["state"]["proposals"][0]["status"] == "pending"
        assert payload["state"]["proposals"][0]["key"] == block.key
        rejected = _post(
            server,
            route + "/findings/" + finding["finding_id"] + "/resolve",
            {"finding_ids": [finding["finding_id"], "stale"], "note": "Reviewed", "reason": "intended"},
        )
        assert rejected.status == 404
        current = json.loads(send_request(server, route).body)
        assert current["state"]["findings"][0]["status"] == "open"
        resolved = _post(
            server,
            route + "/findings/" + finding["finding_id"] + "/resolve",
            {"finding_ids": [finding["finding_id"]], "note": "Reviewed", "reason": "intended"},
        )
        assert resolved.status == 200
        assert json.loads(resolved.body)["lesson"]["state"]["findings"][0]["status"] == "resolved"
        author = payload["state"]["chat"][-2]
        assert author["text"].startswith("Fix this finding. Propose only the affected block(s).")
        assert "Quoted source: " in author["text"]
        assert "Suggested change: Improve wording" in author["text"]
        assert "finding_id" not in author["text"]
        assert author["chips"][0]["referenceId"] == finding["finding_id"]
        assert author["chips"][0]["kind"] == "finding"
        assert author["chips"][0]["label"] == "Bokmål quality"
        proposal_id = proposal["result"]["proposal"]["proposal_id"]
        accepted = _post(server, route + "/proposals/" + proposal_id + "/accept", {})
        assert accepted.status == 200
        assert read_workbench_source(workspace, lesson_id).files != before
        assert json.loads(accepted.body)["proposal"]["status"] == "settled"
    assert (workspace / "drafts" / lesson_id).is_dir()


def test_chat_turn_given_active_turn_expect_conflict_then_cancel_and_retry_same_thread(
    workspace: Path,
) -> None:
    gate = FakeCodexGate.create()
    client = FakeCodexAppServer(
        FakeCodexTurn(text='{"reply":"Finished.","edits":[],"regenerate":null}', gate=gate),
        FakeCodexTurn(text='{"reply":"Retried.","edits":[],"regenerate":null}'),
    )
    route = "/api/lessons/greet_and_introduce_yourself/chat"
    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=client)) as server:
        first = _post(server, route, {"message": "Explain"})
        ticket = json.loads(first.body)
        assert first.status == 202
        assert gate.entered.wait(K_TEST_HANG_GUARD_SECONDS)

        duplicate = _post(server, route, {"message": "Explain more"})
        assert duplicate.status == 409
        cancelled = _post(server, f"{route}/{ticket['turn_id']}/cancel", {})
        assert json.loads(cancelled.body)["cancelled"] is True
        assert server.jobs.wait(ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)["status"] == "cancelled"

        retry = _post(server, f"{route}/{ticket['turn_id']}/retry", {})
        retried_ticket = json.loads(retry.body)
        assert retry.status == 202
        assert retried_ticket["turn_id"] == ticket["turn_id"]
        assert server.jobs.wait(retried_ticket["job_id"], timeout=K_TEST_HANG_GUARD_SECONDS)["status"] == "done"
        transcript = json.loads(send_request(server, "/api/lessons/greet_and_introduce_yourself").body)["state"]["chat"]
        assert [turn["turnId"] for turn in transcript if turn["role"] == "author"] == [ticket["turn_id"]]
        assert transcript[-1]["text"] == "Retried."
        assert client.calls[1].thread_id == "fake-thread-1"
        assert client.interrupts == [("fake-thread-1", "fake-turn-1")]


def _post(server: HTTPServer, route: str, body: object) -> HttpReply:
    return send_request(
        server,
        route,
        method="POST",
        headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        body=json.dumps(body).encode(),
    )


@pytest.mark.parametrize(
    "suffix",
    ["findings/f1/resolve", "exercises/notice_name_question/keep", "proposals/p1/blocks/notice_name_question/keep"],
)
def test_owner_decision_given_missing_reason_expect_400(workspace: Path, suffix: str) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        reply = _post(server, f"/api/lessons/greet_and_introduce_yourself/{suffix}", {})
    assert reply.status == 400
    assert "reason" in json.loads(reply.body)["error"].lower()
