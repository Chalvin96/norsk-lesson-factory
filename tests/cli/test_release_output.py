"""Behavior tests for the release command's bounded output rendering."""

from __future__ import annotations

import json

from lesson_builder.cli import build_parser

K_PLAN_RESULT = {
    "version": 1,
    "source_digest": "digest-1",
    "lesson_count": 431,
    "candidate_count": 8634,
    "unique_count": 8634,
    "reused_count": 8500,
    "missing_count": 134,
    "estimated_cost_usd": 3.21,
    "plan_path": "/tmp/repo/store/scratch/release/release-plan.json",
    "candidates": [{"fingerprint": "abc", "text": "Hei"}],
    "approved": False,
    "next_command": "uv run lesson-data release --approve",
}

K_APPROVED_RESULT = {
    "approved": True,
    "tag": "v2026.09.14",
    "repository": "owner/repo",
    "plan": {
        "lesson_count": 431,
        "unique_count": 8634,
        "reused_count": 8500,
        "missing_count": 134,
        "estimated_cost_usd": 3.21,
        "plan_path": "/tmp/repo/store/scratch/release/release-plan.json",
    },
    "publication": {"published": ["s3_audio", "github_release"]},
}

K_VERIFIED_RESULT = {
    "verified": True,
    "tag": "v2026.09.14",
    "repository": "owner/repo",
    "asset_name": "lessons.tar.gz",
    "archive_sha256": "digest",
    "lesson_count": 431,
    "audio_reference_count": 8634,
}


def _invoke(argv: list[str], monkeypatch, operation: str, result: dict[str, object], capsys) -> str:
    """Run one release CLI command with the operation entry point faked."""
    monkeypatch.setattr(f"lesson_builder.application.operations.release.{operation}", lambda **kwargs: result)
    args = build_parser().parse_args(argv)
    exit_code = args.func(args)
    assert exit_code == 0
    return capsys.readouterr().out


def test_release_command_output_given_plan_result_and_default_format_expect_bounded_summary(
    monkeypatch, capsys
) -> None:
    out = _invoke(["release", "--repo-root", "."], monkeypatch, "run_release", K_PLAN_RESULT, capsys)

    assert "431 lessons · 8634 recordings" in out
    assert "reused: 8500 · missing: 134" in out
    assert "estimated synthesis: $3.21" in out
    assert "plan: /tmp/repo/store/scratch/release/release-plan.json" in out
    assert "next: uv run lesson-data release --approve" in out
    assert "fingerprint" not in out
    assert "candidates" not in out


def test_release_command_output_given_plan_result_and_json_format_expect_full_machine_document(
    monkeypatch, capsys
) -> None:
    out = _invoke(
        ["release", "--repo-root", ".", "--format", "json"], monkeypatch, "run_release", K_PLAN_RESULT, capsys
    )

    assert json.loads(out) == K_PLAN_RESULT


def test_release_command_output_given_approved_result_expect_bounded_summary(monkeypatch, capsys) -> None:
    out = _invoke(["release", "--repo-root", "."], monkeypatch, "run_release", K_APPROVED_RESULT, capsys)

    assert "approved v2026.09.14 for owner/repo" in out
    assert "431 lessons · 8634 recordings · cost: $3.21" in out
    assert "published: s3_audio, github_release" in out
    assert "plan: /tmp/repo/store/scratch/release/release-plan.json" in out
    assert "fingerprint" not in out


def test_release_command_output_given_approved_result_and_json_format_expect_bounded_machine_document(
    monkeypatch, capsys
) -> None:
    out = _invoke(
        ["release", "--repo-root", ".", "--format", "json"], monkeypatch, "run_release", K_APPROVED_RESULT, capsys
    )

    assert json.loads(out) == K_APPROVED_RESULT


def test_release_verify_command_output_given_verified_release_expect_bounded_summary(monkeypatch, capsys) -> None:
    out = _invoke(
        ["release-verify", "--repo-root", ".", "--tag", "v2026.09.14", "--github-repository", "owner/repo"],
        monkeypatch,
        "verify_release",
        K_VERIFIED_RESULT,
        capsys,
    )

    assert "verified v2026.09.14 in owner/repo" in out
    assert "431 lessons · 8634 audio references" in out
    assert "archive: lessons.tar.gz (sha256 digest)" in out


def test_release_verify_command_output_given_json_format_expect_machine_document(monkeypatch, capsys) -> None:
    out = _invoke(
        [
            "release-verify",
            "--repo-root",
            ".",
            "--tag",
            "v2026.09.14",
            "--github-repository",
            "owner/repo",
            "--format",
            "json",
        ],
        monkeypatch,
        "verify_release",
        K_VERIFIED_RESULT,
        capsys,
    )

    assert json.loads(out) == K_VERIFIED_RESULT
