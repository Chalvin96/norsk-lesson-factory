"""Entry point: doctor and environment capability checks."""

import json
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace

from lesson_builder.application.operations import check_environment as module
from lesson_builder.application.operations.check_environment import check_environment
from lesson_builder.cli import main


def test_check_environment_given_complete_offline_workspace_expect_ready(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    report = check_environment(tmp_path)
    assert report.status == "ready"
    assert {finding.check_id for finding in report.findings} == {
        "offline.python",
        "offline.imports",
        "offline.workspace_paths",
        "offline.configuration",
        "offline.pandoc",
    }


def test_check_environment_given_missing_audio_credential_expect_capability_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    report = check_environment(tmp_path, capabilities=("audio",))
    credential = next(finding for finding in report.findings if finding.check_id == "audio.credentials")
    assert report.status == "blocked"
    assert credential.status == "failed"
    assert "service-account" in credential.summary


def test_check_environment_given_repo_dotenv_credential_expect_export_contract_failure(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    (tmp_path / ".env").write_text("LESSON_AUDIO_SERVICE_ACCOUNT=account.json\n", encoding="utf-8")
    (tmp_path / "account.json").write_text(
        '{"client_email":"test@example.com","private_key":"secret"}\n', encoding="utf-8"
    )
    monkeypatch.delenv("LESSON_AUDIO_SERVICE_ACCOUNT", raising=False)
    report = check_environment(tmp_path, capabilities=("audio",))
    credential = next(finding for finding in report.findings if finding.check_id == "audio.credentials")
    assert credential.status == "failed"


def test_check_environment_given_empty_audio_client_email_expect_credential_failure(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    account = tmp_path / "account.json"
    account.write_text(json.dumps({"client_email": "", "private_key": "secret"}), encoding="utf-8")
    monkeypatch.setenv("LESSON_AUDIO_SERVICE_ACCOUNT", str(account))

    report = check_environment(tmp_path, capabilities=("audio",))

    credential = next(finding for finding in report.findings if finding.check_id == "audio.credentials")
    assert credential.status == "failed"
    assert report.status == "blocked"


def test_check_environment_given_empty_audio_private_key_expect_credential_failure(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    account = tmp_path / "account.json"
    account.write_text(json.dumps({"client_email": "test@example.com", "private_key": "  "}), encoding="utf-8")
    monkeypatch.setenv("LESSON_AUDIO_SERVICE_ACCOUNT", str(account))

    report = check_environment(tmp_path, capabilities=("audio",))

    credential = next(finding for finding in report.findings if finding.check_id == "audio.credentials")
    assert credential.status == "failed"
    assert report.status == "blocked"


def test_check_environment_given_invalid_audio_voice_profile_expect_configuration_failure(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    (tmp_path / "config.yaml").write_text("audio:\n  voice_profiles:\n    unsupported: [voice]\n", encoding="utf-8")
    report = check_environment(tmp_path, capabilities=("audio",))
    configuration = next(finding for finding in report.findings if finding.check_id == "audio.configuration")
    assert configuration.status == "failed"


def test_check_environment_given_unknown_audio_character_override_expect_configuration_failure(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "audio:\n  character_voice_overrides:\n    missing_character: voice\n", encoding="utf-8"
    )
    report = check_environment(tmp_path, capabilities=("audio",))
    configuration = next(finding for finding in report.findings if finding.check_id == "audio.configuration")
    assert configuration.status == "failed"


def test_check_environment_given_generation_capability_expect_offline_findings_retained(
    monkeypatch, tmp_path: Path
) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    monkeypatch.setattr(module, "load_llm_jobs", lambda **kwargs: {})
    monkeypatch.setattr(module, "load_llm_timeout_seconds", lambda **kwargs: 3)
    report = check_environment(tmp_path, capabilities=("generation",))
    assert report.capabilities == ("offline", "generation")
    assert any(finding.capability == "offline" for finding in report.findings)


def test_check_environment_given_invalid_llm_timeout_expect_generation_blocked(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    monkeypatch.setattr(module, "load_llm_jobs", lambda **kwargs: {})
    monkeypatch.setattr(
        module, "load_llm_timeout_seconds", lambda **kwargs: (_ for _ in ()).throw(ValueError("invalid"))
    )
    report = check_environment(tmp_path, capabilities=("generation",))
    timeout = next(finding for finding in report.findings if finding.check_id == "generation.timeout")
    assert timeout.status == "failed"
    assert report.status == "blocked"


def test_check_environment_given_timed_out_executable_probe_expect_incomplete(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    monkeypatch.setattr(module, "load_llm_jobs", lambda **kwargs: {})
    monkeypatch.setattr(module, "load_llm_timeout_seconds", lambda **kwargs: 3)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutExpired("probe", 3))
    )
    report = check_environment(tmp_path, capabilities=("generation",))
    finding = next(finding for finding in report.findings if finding.check_id == "generation.opencode")
    assert finding.status == "incomplete"
    assert report.status == "incomplete"


def test_doctor_cli_given_offline_workspace_expect_json_report(monkeypatch, tmp_path: Path, capsys) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    assert main(["doctor", "--workspace-root", str(tmp_path), "--format", "json"]) == 0
    assert '"status": "ready"' in capsys.readouterr().out


def test_check_environment_given_old_node_expect_eval_blocked(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    _patch_eval_runtime(monkeypatch, "v16.20.2\n")
    report = check_environment(tmp_path, capabilities=("eval",))
    node = next(finding for finding in report.findings if finding.check_id == "eval.node")
    assert node.status == "failed"
    assert report.status == "blocked"
    assert "22.22.0" in (node.next_action or "")


def test_check_environment_given_unparseable_node_expect_eval_incomplete(monkeypatch, tmp_path: Path) -> None:
    _write_workspace(tmp_path)
    _patch_offline(monkeypatch)
    _patch_eval_runtime(monkeypatch, "node-version-unknown\n")
    report = check_environment(tmp_path, capabilities=("eval",))
    node = next(finding for finding in report.findings if finding.check_id == "eval.node")
    assert node.status == "incomplete"
    assert report.status == "incomplete"


def _write_workspace(root: Path) -> None:
    (root / "content" / "catalog" / "approved").mkdir(parents=True)
    (root / "content" / "lessons").mkdir()
    (root / "content" / "curriculum").mkdir()
    (root / "content" / "catalog" / "approved" / "catalog.yaml").write_text("{}\n", encoding="utf-8")
    (root / "content" / "curriculum" / "plan.yaml").write_text("{}\n", encoding="utf-8")
    (root / "config.yaml").write_text("{}\n", encoding="utf-8")


def _patch_offline(monkeypatch) -> None:
    monkeypatch.setattr(
        module, "compiler_fingerprint", lambda: {"pandoc": "3.0", "panflute": "2.3", "reader": "markdown"}
    )


def _patch_eval_runtime(monkeypatch, node_output: str) -> None:
    monkeypatch.setattr(module, "load_llm_jobs", lambda **kwargs: {})
    monkeypatch.setattr(module, "load_llm_timeout_seconds", lambda **kwargs: 3)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/" + name)

    def run(command, **kwargs):
        output = node_output if command[0].endswith("node") else "tool 1"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
