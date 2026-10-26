"""Entry point: test_workbench_http_payloads_given_spa_routes_expect_committed_schema_validation."""

from __future__ import annotations

import json
import shutil
import subprocess
from _thread import RLock
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import cast
from urllib.parse import quote

import pytest
import yaml
from jsonschema import Draft202012Validator

from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_proposals import create_proposal
from lesson_builder.application.operations.workbench_proposals import store_proposal
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.application.operations.workbench_settings_operations import read_workbench_settings
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.web.server import WorkbenchServer
from lesson_builder.web.server import create_workbench_server
from lesson_builder.workflow.workbench_regeneration.runner import read_regeneration
from lesson_builder.workflow.workbench_regeneration.state import RegenerationResponse
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexTurn
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.web.serving import send_request
from tests.web.serving import serving

K_TEST_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
K_TEST_SCHEMA_PATH = K_TEST_REPOSITORY_ROOT / "workbench" / "schema" / "workbench-api.schema.json"
K_TEST_LESSON_ID = "greet_and_introduce_yourself"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Build the source and configuration needed by the workbench API in a temporary workspace."""
    for relative in (
        f"content/lessons/{K_TEST_LESSON_ID}",
        "content/authoring",
        "content/terminology",
        "content/catalog",
        ".opencode/agents",
    ):
        shutil.copytree(K_TEST_REPOSITORY_ROOT / relative, tmp_path / relative)
    shutil.copy(K_TEST_REPOSITORY_ROOT / "config.yaml", tmp_path / "config.yaml")
    plan_source = yaml.safe_load((K_TEST_REPOSITORY_ROOT / "content/curriculum/plan.yaml").read_bytes())
    plan_source["slots"] = [slot for slot in plan_source["slots"] if slot["catalog_id"] == K_TEST_LESSON_ID]
    plan_path = tmp_path / "content/curriculum/plan.yaml"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(yaml.safe_dump(plan_source), encoding="utf-8")
    for arguments in (
        ("init", "-b", "authoring"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.test"),
        ("add", "."),
        ("commit", "--no-verify", "-m", "initial"),
    ):
        subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True)
    return tmp_path


@dataclass
class WorkbenchHttpContract:
    server: WorkbenchServer
    validator: Draft202012Validator

    def request(
        self,
        method: str,
        route: str,
        response_name: str,
        *,
        expected_status: int = 200,
        request_name: str | None = None,
        request_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        body = (request_body or {}) if request_name is not None else None
        if request_name is not None:
            self._check_payload("requests", request_name, f"{method} {route} request", body)
        reply = send_request(
            self.server,
            route,
            method=method,
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
            body=None if body is None else json.dumps(body).encode("utf-8"),
        )
        assert reply.status == expected_status, route
        payload = cast(dict[str, object], json.loads(reply.body))
        self._check_payload("responses", response_name, f"{method} {route}", payload)
        return payload

    def get(self, route: str, response_name: str, *, expected_status: int = 200) -> dict[str, object]:
        return self.request("GET", route, response_name, expected_status=expected_status)

    def post(
        self,
        route: str,
        request_name: str,
        response_name: str,
        request_body: dict[str, object] | None = None,
        *,
        expected_status: int = 200,
    ) -> dict[str, object]:
        return self.request(
            "POST",
            route,
            response_name,
            expected_status=expected_status,
            request_name=request_name,
            request_body=request_body,
        )

    def put(
        self, route: str, request_name: str, response_name: str, request_body: dict[str, object]
    ) -> dict[str, object]:
        return self.request("PUT", route, response_name, request_name=request_name, request_body=request_body)

    def _check_payload(self, section: str, name: str, route: str, payload: object) -> None:
        instance: dict[str, object] = {"requests": {}, "responses": {}, "components": {}}
        cast(dict[str, object], instance[section])[name] = payload
        errors = list(self.validator.iter_errors(instance))
        assert not errors, "\n".join(f"{route} {json_path(error.absolute_path)}: {error.message}" for error in errors)


def test_workbench_http_payloads_given_spa_routes_expect_committed_schema_validation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = Draft202012Validator(json.loads(K_TEST_SCHEMA_PATH.read_text(encoding="utf-8")))
    release_regeneration = Event()
    regeneration_started = Event()

    def hold_regeneration(
        root: Path, identity: str, lock: RLock, *, cancellation: CancellationToken | None = None
    ) -> RegenerationResponse:
        regeneration_started.set()
        release_regeneration.wait(K_TEST_HANG_GUARD_SECONDS)
        return read_regeneration(root, identity, lock)

    monkeypatch.setattr("lesson_builder.web.workbench_actions.execute_regeneration", hold_regeneration)
    codex = FakeCodexAppServer(
        FakeCodexTurn(text='{"reply":"Curriculum response.","proposed_edit":null}'),
        FakeCodexTurn(text='{"reply":"Lesson response.","edits":[],"regenerate":null}'),
    )
    block_key = _reading_block_key(workspace)

    with serving(create_workbench_server(workspace, "127.0.0.1", 0, codex_client=codex)) as server:
        contract = WorkbenchHttpContract(server, validator)
        _check_get_routes(contract)
        _check_settings_and_curriculum_routes(contract, workspace)
        _check_chat_routes(contract)
        _check_proposal_routes(contract, workspace)
        _check_write_routes(contract, workspace, block_key)
        _check_regeneration_routes(contract, block_key, regeneration_started, release_regeneration)
        for route in ("/api/jobs/unknown", "/api/runs/unknown"):
            contract.get(route, "error", expected_status=404)


def _reading_block_key(workspace: Path) -> str:
    source = read_workbench_source(workspace, K_TEST_LESSON_ID)
    return next(block for block in source.blocks if block.kind == "reading").key


def _store_proposal(workspace: Path, prefix: str) -> str:
    """Create and store one open chat proposal editing the reading block translation."""
    source = read_workbench_source(workspace, K_TEST_LESSON_ID)
    reading = next(block for block in source.blocks if block.kind == "reading")
    proposal, _ = create_proposal(
        K_TEST_LESSON_ID,
        source,
        ChatResponse.model_validate(
            {
                "reply": f"Improve this line with {prefix.strip()}.",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', f'translation="{prefix}', 1),
                        "note": "clarify translation",
                    }
                ],
            }
        ),
        origin="chat",
    )
    assert proposal is not None
    store_proposal(workspace, K_TEST_LESSON_ID, proposal)
    return proposal.proposal_id


def _check_get_routes(contract: WorkbenchHttpContract) -> None:
    for route, name in (
        ("/api/curriculum", "curriculum"),
        ("/api/settings", "settings"),
        (f"/api/lessons/{K_TEST_LESSON_ID}", "lesson"),
        (f"/api/lessons/{K_TEST_LESSON_ID}/source", "source"),
    ):
        contract.get(route, name)
    audio_route = f"/api/lessons/{K_TEST_LESSON_ID}/audio/{'0' * 64}"
    contract.get(audio_route, "error", expected_status=404)


def _check_settings_and_curriculum_routes(contract: WorkbenchHttpContract, workspace: Path) -> None:
    contract.put(
        "/api/settings",
        "settings.put",
        "settings",
        read_workbench_settings(workspace).model_dump(mode="json"),
    )
    contract.post("/api/settings/reset", "settings.reset", "settings")
    _check_curriculum_save(contract)


def _check_curriculum_save(contract: WorkbenchHttpContract) -> None:
    curriculum = contract.get("/api/curriculum", "curriculum")
    lesson = cast(dict[str, object], curriculum["lessons"][0])
    entry = cast(dict[str, object], curriculum["entries"][K_TEST_LESSON_ID])
    edit = {
        "kind": "update",
        "lesson_id": K_TEST_LESSON_ID,
        "title": f"{cast(str, lesson['title'])} (edited)",
        "learner_outcome": entry["learnerOutcome"],
        "cefr_level": entry["cefrLevel"],
        "prerequisites": entry["dependsOn"],
        "helpful_prerequisites": entry["helpfulPrerequisites"],
    }
    preview = contract.post("/api/curriculum/preview", "curriculum.preview", "curriculumPreview", {"edits": [edit]})
    contract.put(
        "/api/curriculum/save",
        "curriculum.save",
        "curriculumSave",
        {
            "edits": [edit],
            "review_digest": preview["reviewDigest"],
        },
    )


def _check_chat_routes(contract: WorkbenchHttpContract) -> None:
    chat_routes = (
        ("/api/curriculum/chat/new", "chat.new", "resetResult"),
        (f"/api/lessons/{K_TEST_LESSON_ID}/chat/new", "chat.new", "resetResult"),
    )
    for route, request_name, response_name in chat_routes:
        contract.post(route, request_name, response_name)
    for route, request_name in (
        ("/api/curriculum/chat", "curriculum.chat"),
        (f"/api/lessons/{K_TEST_LESSON_ID}/chat", "lesson.chat"),
    ):
        ticket = contract.post(route, request_name, "chatJobTicket", {"message": "Explain this."}, expected_status=202)
        job_id = str(ticket["job_id"])
        contract.get(f"/api/jobs/{job_id}", "job")
        result = contract.server.jobs.wait(job_id, timeout=K_TEST_HANG_GUARD_SECONDS)
        assert result is not None and result["status"] == "done"


def _check_proposal_routes(contract: WorkbenchHttpContract, workspace: Path) -> None:
    accepted_id = _store_proposal(workspace, "Hello: ")
    contract.post(
        f"/api/lessons/{K_TEST_LESSON_ID}/proposals/{accepted_id}/accept",
        "lesson.proposal.accept",
        "lessonAction",
    )
    rejected_id = _store_proposal(workspace, "Welcome: ")
    contract.post(
        f"/api/lessons/{K_TEST_LESSON_ID}/proposals/{rejected_id}/reject",
        "lesson.proposal.reject",
        "lessonAction",
    )
    contract.post(f"/api/lessons/{K_TEST_LESSON_ID}/promote", "lesson.promote", "error", expected_status=409)


def _check_write_routes(contract: WorkbenchHttpContract, workspace: Path, block_key: str) -> None:
    source = read_workbench_source(workspace, K_TEST_LESSON_ID)
    fields = dict(next(block for block in source.blocks if block.key == block_key).fields)
    fields["translation"] = fields["translation"].replace("Hello: ", "Hi: ", 1)
    contract.put(
        f"/api/lessons/{K_TEST_LESSON_ID}/blocks/{quote(block_key, safe='')}",
        "lesson.block.put",
        "write",
        {"fields": fields},
    )
    contract.put(
        f"/api/lessons/{K_TEST_LESSON_ID}/files/lesson.md",
        "lesson.file.put",
        "write",
        {"text": source.files["lesson.md"]},
    )
    contract.post(f"/api/lessons/{K_TEST_LESSON_ID}/save", "lesson.save", "save")


def _check_regeneration_routes(
    contract: WorkbenchHttpContract,
    block_key: str,
    regeneration_started: Event,
    release_regeneration: Event,
) -> None:
    route = f"/api/lessons/{K_TEST_LESSON_ID}/regenerate"
    ticket = contract.post(
        route,
        "lesson.regenerate",
        "regenerationTicket",
        {"mode": "scope", "key": block_key},
        expected_status=202,
    )
    run_id = str(ticket["run_id"])
    job_id = str(ticket["job_id"])
    assert regeneration_started.wait(K_TEST_HANG_GUARD_SECONDS)
    contract.get(f"/api/runs/{run_id}", "run")
    contract.get(f"/api/jobs/{job_id}", "job")
    contract.post(f"/api/jobs/{job_id}/cancel", "job.cancel", "cancelResult")
    release_regeneration.set()


def json_path(path: Iterable[object]) -> str:
    """Render a JSON Schema error path as a stable slash-delimited location."""
    return "/" + "/".join(str(part) for part in path)
