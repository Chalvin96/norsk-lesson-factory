"""Entry point: runner aggregation tests exercise the validation gate contract."""

import subprocess
from pathlib import Path

import pytest

import scripts.check_all as check_all
from scripts.check_all import Gate
from scripts.check_all import GateResult
from scripts.check_all import build_gates
from scripts.check_all import main
from scripts.check_all import run_checks


def test_run_checks_given_regeneration_failure_expect_command_failure(tmp_path, capsys):
    calls = []

    def run_command(command, _root):
        calls.append(tuple(command))
        return _completed(command, 1, "regeneration failed\n")

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "regeneration failed" in capsys.readouterr().out
    assert calls == [("regenerate",)]


def test_run_checks_given_tracked_export_diff_expect_diff_failure(tmp_path, capsys):
    def run_command(command, _root):
        if command == ("regenerate",):
            return _completed(command)
        return _completed(command, 1, "tracked diff\n")

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "tracked diff" in capsys.readouterr().out


def test_run_checks_given_untracked_lesson_export_expect_untracked_failure(tmp_path, capsys):
    def run_command(command, _root):
        if command == ("regenerate",) or command[:2] == ("git", "diff"):
            return _completed(command)
        return _completed(command, stdout="?? dist/lessons/new.json\n")

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "regeneration created untracked dist/lessons files" in capsys.readouterr().out


def test_run_checks_given_lesson_filename_mismatch_expect_structure_failure(tmp_path, capsys):
    (tmp_path / "content" / "lessons" / "greet").mkdir(parents=True)
    (tmp_path / "dist" / "lessons").mkdir(parents=True)
    (tmp_path / "dist" / "lessons" / "other.json").write_text("{}", encoding="utf-8")

    def run_command(command, _root):
        return _completed(command)

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "filenames do not match" in capsys.readouterr().out


def test_run_checks_given_schema_version_command_failure_expect_command_failure(tmp_path, capsys):
    _write_matching_distribution(tmp_path)

    def run_command(command, _root):
        if command[:2] == ("uv", "run"):
            return _completed(command, 1, "schema command failed\n")
        return _completed(command)

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "schema command failed" in capsys.readouterr().out


def test_run_checks_given_malformed_lesson_export_expect_read_failure(tmp_path, capsys):
    _write_matching_distribution(tmp_path)
    (tmp_path / "dist" / "lessons" / "greet.json").write_text("not json", encoding="utf-8")

    def run_command(command, _root):
        if command[:2] == ("uv", "run"):
            return _completed(command, stdout="1\n")
        return _completed(command)

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "could not read" in capsys.readouterr().out


def test_run_checks_given_retired_schema_version_expect_schema_failure(tmp_path, capsys):
    _write_matching_distribution(tmp_path, schema_version="retired")

    def run_command(command, _root):
        if command[:2] == ("uv", "run"):
            return _completed(command, stdout="current\n")
        return _completed(command)

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 1
    assert "retired or missing lesson schema version" in capsys.readouterr().out


def test_run_checks_given_current_exports_expect_pass(tmp_path, capsys):
    _write_matching_distribution(tmp_path, schema_version="current")

    def run_command(command, _root):
        if command[:2] == ("uv", "run"):
            return _completed(command, stdout="current\n")
        return _completed(command)

    result = run_checks((Gate("regenerate-dist", ("regenerate",)),), repo_root=tmp_path, run_command=run_command)

    assert result == 0
    assert "PASS regenerate-dist " in capsys.readouterr().out


def test_main_given_gate_added_to_build_table_expect_name_is_allowed(monkeypatch, capsys):
    monkeypatch.setattr(check_all, "build_gates", lambda conventions_base=None: (Gate("new gate", ("new",)),))

    result = main(["--only", "new gate"], execute_gate=_passing_gate)

    assert result == 0
    assert "PASS new gate " in capsys.readouterr().out


def test_main_given_only_filter_expect_matching_gates_in_declared_order(capsys):
    result = main(["--only", "pytest,conventions"], execute_gate=_passing_gate)

    assert result == 0
    assert [line.split()[1] for line in capsys.readouterr().out.splitlines()] == ["conventions", "pytest"]


def test_main_given_skip_filter_expect_omitted_gate_and_remaining_order(capsys):
    result = main(["--skip", "pytest"], execute_gate=_passing_gate)

    assert result == 0
    summaries = capsys.readouterr().out.splitlines()
    assert [line.removeprefix("PASS ").rsplit(" ", 1)[0] for line in summaries] == [
        gate.name for gate in build_gates() if gate.name != "pytest"
    ]


@pytest.mark.parametrize("option", ("--only", "--skip"))
def test_main_given_unknown_gate_name_expect_argument_error(option):
    with pytest.raises(SystemExit) as error:
        main([option, "missing gate"], execute_gate=_passing_gate)

    assert error.value.code == 2


def test_run_checks_given_one_fake_gate_fails_expect_all_gates_run_and_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gates = (Gate("first", ("first",)), Gate("second", ("second",)), Gate("third", ("third",)))
    calls: list[str] = []

    def execute(gate: Gate, _root: Path) -> GateResult:
        calls.append(gate.name)
        return GateResult(
            gate.name, 1 if gate.name == "second" else 0, 0.01, "second output\n" if gate.name == "second" else ""
        )

    result = run_checks(gates, repo_root=Path("."), execute_gate=execute)

    assert result == 1
    assert sorted(calls) == ["first", "second", "third"]
    output = capsys.readouterr().out
    assert output.count("PASS ") == 2
    assert output.count("FAIL second ") == 1
    assert "second output" in output


def test_run_checks_given_all_fake_gates_pass_expect_zero(capsys: pytest.CaptureFixture[str]) -> None:
    gates = (Gate("first", ("first",)), Gate("second", ("second",)))

    def execute(gate: Gate, _root: Path) -> GateResult:
        return GateResult(gate.name, 0, 0.01, "hidden output\n")

    result = run_checks(gates, repo_root=Path("."), execute_gate=execute)

    assert result == 0
    output = capsys.readouterr().out
    assert output.count("PASS ") == 2
    assert "hidden output" not in output


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _write_matching_distribution(tmp_path, schema_version="1"):
    (tmp_path / "content" / "lessons" / "greet").mkdir(parents=True)
    dist_root = tmp_path / "dist" / "lessons"
    dist_root.mkdir(parents=True)
    (dist_root / "greet.json").write_text(
        '{"schema_version": "' + schema_version + '"}',
        encoding="utf-8",
    )


def _passing_gate(gate: Gate, _root: Path) -> GateResult:
    return GateResult(gate.name, 0, 0.01, "")
