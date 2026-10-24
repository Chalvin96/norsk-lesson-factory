"""Entry points: Settings API persistence and reset routes are verified over HTTP."""

import json
import shutil
from pathlib import Path

import pytest

from lesson_builder.web.server import create_workbench_server
from tests.web.serving import send_request
from tests.web.serving import serving


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    repo = Path(__file__).parents[2]
    shutil.copy(repo / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(repo / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    shutil.copytree(
        repo / "content" / "lessons" / "greet_and_introduce_yourself",
        tmp_path / "content" / "lessons" / "greet_and_introduce_yourself",
    )
    return tmp_path


def test_workbench_settings_api_given_save_and_reset_expect_override_then_config_defaults(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        initial_reply = send_request(server, "/api/settings")
        initial = json.loads(initial_reply.body)
        settings = json.loads(json.dumps(initial["settings"]))
        settings["roles"]["chat"] = {"model": "gpt-6-luna", "effort": "low"}
        settings["defaults"]["review_scope"] = "section"
        saved_reply = send_request(
            server,
            "/api/settings",
            method="PUT",
            body=json.dumps(settings).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
        saved = json.loads(saved_reply.body)
        reset_reply = send_request(
            server,
            "/api/settings/reset",
            method="POST",
            body=b"{}",
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
        reset = json.loads(reset_reply.body)

    assert initial_reply.status == saved_reply.status == reset_reply.status == 200
    assert initial["settings"]["roles"]["chat"] == {"model": "gpt-6.1-sol", "effort": "medium"}
    assert "gpt-6-luna" in initial["modelOptions"]["chat"]
    assert "openai/gpt-6-luna" not in initial["modelOptions"]["chat"]
    assert "openai/gpt-6-luna" in initial["modelOptions"]["generation"]
    assert saved["settings"]["roles"]["chat"] == {"model": "gpt-6-luna", "effort": "low"}
    assert saved["settings"]["defaults"]["review_scope"] == "section"
    assert reset["settings"] == initial["settings"]
    assert not (workspace / ".workbench" / "settings.yaml").exists()


def test_workbench_settings_api_given_unknown_model_expect_rejection_without_override(workspace: Path) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        settings = json.loads(send_request(server, "/api/settings").body)["settings"]
        settings["roles"]["chat"]["model"] = "unknown/model"
        reply = send_request(
            server,
            "/api/settings",
            method="PUT",
            body=json.dumps(settings).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )

    assert reply.status == 400
    assert not (workspace / ".workbench" / "settings.yaml").exists()


def test_workbench_settings_api_given_opencode_model_for_chat_expect_rejection_without_override(
    workspace: Path,
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        settings = json.loads(send_request(server, "/api/settings").body)["settings"]
        settings["roles"]["chat"]["model"] = "openai/gpt-6.1-sol"
        reply = send_request(
            server,
            "/api/settings",
            method="PUT",
            body=json.dumps(settings).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )

    assert reply.status == 400
    assert not (workspace / ".workbench" / "settings.yaml").exists()


def test_workbench_settings_api_given_opencode_go_role_model_expect_option_validation_and_persistence(
    workspace: Path,
) -> None:
    with serving(create_workbench_server(workspace, "127.0.0.1", 0)) as server:
        initial_reply = send_request(server, "/api/settings")
        initial = json.loads(initial_reply.body)
        settings = initial["settings"]
        settings["roles"]["generation"]["model"] = "opencode-go/glm-5.3"
        saved_reply = send_request(
            server,
            "/api/settings",
            method="PUT",
            body=json.dumps(settings).encode(),
            headers={"Content-Type": "application/json", "X-Workbench-Request": "1"},
        )
        saved = json.loads(saved_reply.body)
        persisted_reply = send_request(server, "/api/settings")
        persisted = json.loads(persisted_reply.body)

    assert initial_reply.status == saved_reply.status == persisted_reply.status == 200
    assert "opencode-go/glm-5.3" in initial["modelOptions"]["generation"]
    assert saved["settings"]["roles"]["generation"]["model"] == "opencode-go/glm-5.3"
    assert persisted["settings"]["roles"]["generation"]["model"] == "opencode-go/glm-5.3"
