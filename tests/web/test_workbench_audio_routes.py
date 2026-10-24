"""Entry point: `test_workbench_audio_routes_given_confirmed_plan_expect_job_completion_and_playback_bytes` verifies HTTP audio behavior."""

from __future__ import annotations

import json
import shutil
from http.server import HTTPServer
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_records import WorkbenchAudioPlanRecord
from lesson_builder.application.operations.workbench_records import WorkbenchAudioSpendLedger
from lesson_builder.web.server import create_workbench_server
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.fakes import FakeTtsClient
from tests.application.fakes import fake_audio_for_text
from tests.domain.curriculum.factories import CurriculumPlanFactory
from tests.domain.curriculum.factories import CurriculumPlanSlotFactory
from tests.web.serving import HttpReply
from tests.web.serving import send_request
from tests.web.serving import serving
from tests.workspace.builders import write_curriculum_plan

K_LESSON_ID = "greet_and_introduce_yourself"
K_AUDIO_BLOCK_KEY = "a-first-meeting#1"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    repository = Path(__file__).parents[2]
    shutil.copytree(
        repository / "content" / "lessons" / K_LESSON_ID,
        tmp_path / "content" / "lessons" / K_LESSON_ID,
    )
    authoring = tmp_path / "content" / "authoring"
    authoring.mkdir(parents=True)
    shutil.copy(repository / "content" / "authoring" / "characters.yaml", authoring / "characters.yaml")
    shutil.copy(repository / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(repository / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    write_curriculum_plan(
        tmp_path,
        CurriculumPlanFactory(slots=[CurriculumPlanSlotFactory(catalog_id=K_LESSON_ID)]),
    )
    return tmp_path


def test_workbench_audio_routes_given_confirmed_plan_expect_job_completion_and_playback_bytes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeTtsClient.from_fn(fake_audio_for_text)
    credentials = workspace / "fake-service-account.json"
    credentials.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LESSON_AUDIO_SERVICE_ACCOUNT", str(credentials))
    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_audio.GoogleTextToSpeechClient",
        lambda **kwargs: client,
    )

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        planned = _post(
            server,
            f"/api/lessons/{K_LESSON_ID}/audio/plan",
            {"scope": "block", "selection": "missing_and_stale", "block_key": K_AUDIO_BLOCK_KEY},
        )
        assert planned.status == 200
        plan = json.loads(planned.body)["plan"]
        assert plan["newCount"] == 1
        assert plan["estimatedCostUsd"] == 0.01

        started = _post(
            server,
            f"/api/lessons/{K_LESSON_ID}/audio/synthesize",
            {"plan_token": plan["token"]},
        )
        assert started.status == 202
        job = server.jobs.wait(json.loads(started.body)["job_id"], timeout=30)
        assert job["status"] == "done"
        assert job["result"] == {"completed_count": 1, "errors": []}

        audio = send_request(
            server,
            f"/api/lessons/{K_LESSON_ID}/audio/{plan['clips'][0]['fingerprint']}",
        )
        missing = _post(
            server,
            f"/api/lessons/{K_LESSON_ID}/audio/plan",
            {"scope": "missing_and_stale", "selection": "missing_and_stale"},
        )

    assert len(client.calls) == 1
    assert audio.status == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.headers["cache-control"] == "no-store"
    assert audio.body.startswith("RIFF")
    assert json.loads(missing.body)["plan"]["reusedCount"] == 0


def test_workbench_audio_server_start_given_running_plan_expect_interrupted_reservation_reconciled(
    workspace: Path,
) -> None:
    plan = plan_workbench_audio(workspace, K_LESSON_ID, "block", block_key=K_AUDIO_BLOCK_KEY)
    stored_plan = read_workbench_record(workspace, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
    stored_plan.status = "running"
    stored_plan.reservation_month = "2026-10"
    stored_plan.result = {"spent_characters": 4}
    write_workbench_record(workspace, "audio_plans", plan.token, stored_plan)
    write_workbench_record(
        workspace,
        "audio_spend",
        "2026-10",
        WorkbenchAudioSpendLedger(
            month="2026-10",
            synthesized_characters=4,
            reservations={plan.token: plan.characters_to_synthesize - 4},
        ),
    )

    with serving(create_workbench_server(workspace, "127.0.0.1", 0)):
        recovered = read_workbench_record(workspace, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
        ledger = read_workbench_record(workspace, "audio_spend", "2026-10", WorkbenchAudioSpendLedger)
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)):
        recovered_again = read_workbench_record(workspace, "audio_plans", plan.token, WorkbenchAudioPlanRecord)
        ledger_again = read_workbench_record(workspace, "audio_spend", "2026-10", WorkbenchAudioSpendLedger)

    assert recovered.status == "failed"
    assert recovered.result["error"] == "interrupted"
    assert recovered.result["spent_characters"] == plan.characters_to_synthesize
    assert ledger.synthesized_characters == plan.characters_to_synthesize
    assert plan.token not in ledger.reservations
    assert recovered_again == recovered
    assert ledger_again == ledger


def _post(server: HTTPServer, route: str, body: object) -> HttpReply:
    return send_request(
        server,
        route,
        method="POST",
        headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        body=json.dumps(body).encode(),
    )
