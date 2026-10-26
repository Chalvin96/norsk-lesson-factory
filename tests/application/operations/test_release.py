"""Entry point: `run_release` planning, approval, and tag verification behavior.

Only the external boundaries are faked: the synthesis client is injected and
the S3/GitHub publication and release-download adapters are patched at their
call site. Planning, cache seeding, export, validation, packaging, archive
verification, and published-release verification run for real against
temporary workspaces.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import wave
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.compile_lesson_package import compile_lesson_package
from lesson_builder.application.operations.export_distribution import export_distribution
from lesson_builder.application.operations.package_distribution import package_distribution
from lesson_builder.application.operations.release import run_release
from lesson_builder.application.operations.release import verify_release
from lesson_builder.application.operations.synthesize_audio import AudioCandidateIdentity
from lesson_builder.application.operations.synthesize_audio import load_audio_settings
from lesson_builder.application.operations.synthesize_audio import plan_lesson_audio_candidates
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalBlock
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import WorkbenchAudioCheckFinding
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndex
from lesson_builder.application.operations.workbench_records import WorkbenchAudioIndexClip
from lesson_builder.application.operations.workbench_records import WorkbenchAudioReleaseVoices
from lesson_builder.domain.lesson.services.transcript import derive_transcript_blocks
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.fakes import FakeTtsClient
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT

K_LESSON_ID = "question_word_order"
K_PREREQUISITE_LESSON_ID = "word_order_main_clauses"
K_TAG = "v2026.09.14"
K_RELEASE_SCRATCH = ("store", "scratch", "release")


@pytest.fixture(autouse=True)
def no_ambient_github_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)


def test_run_release_given_canonical_edit_without_receipts_expect_source_bound_plan(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    exercises = repo / "content" / "lessons" / K_LESSON_ID / "exercises.yaml"
    exercises.write_text(exercises.read_text() + "\n# Canonical edit reviewed through Git.\n")

    result = run_release(repo_root=repo)

    assert result["lesson_count"] == 1
    assert result["approved"] is False
    assert not (repo / "content" / "approvals").exists()


def _repo(tmp_path: Path) -> Path:
    """Write a minimal workspace with one planned, approved canonical lesson."""
    source_root = Path(__file__).parents[3]
    package = tmp_path / "content" / "lessons" / K_LESSON_ID
    package.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        source = K_CATALOG_PACKAGE_FIXTURE_ROOT / name
        (package / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    curriculum = tmp_path / "content" / "curriculum"
    curriculum.mkdir(parents=True)
    plan = yaml.safe_load((source_root / "content/curriculum/plan.yaml").read_text(encoding="utf-8"))
    plan["slots"] = [slot for slot in plan["slots"] if slot["catalog_id"] == K_LESSON_ID]
    (curriculum / "plan.yaml").write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    config = {
        "audio": {
            "provider": "google-cloud-text-to-speech",
            "language": "nb-NO",
            "synthesis_revision": "test-v1",
        }
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return tmp_path


def _wav(frames: int = 160) -> bytes:
    """Return one minimal valid mono WAV asset."""
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def _identities(repo: Path) -> list[AudioCandidateIdentity]:
    """Derive the current planning identities for the fixture lesson."""
    settings = load_audio_settings(repo_root=repo, require_service_account=False)
    lesson = compile_lesson_package(repo / "content" / "lessons" / K_LESSON_ID).lesson
    assert lesson is not None
    blocks = derive_transcript_blocks(lesson.model_dump(mode="json"))
    return plan_lesson_audio_candidates(transcript_blocks=blocks, settings=settings)


def _prerequisite_order_repo(tmp_path: Path, *, prerequisite_first: bool) -> Path:
    """Add the canonical prerequisite lesson so its order against the dependent varies."""
    repo = _repo(tmp_path)
    source_root = Path(__file__).parents[3]
    package = repo / "content" / "lessons" / K_PREREQUISITE_LESSON_ID
    package.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        source = repo / "content" / "lessons" / K_LESSON_ID / name
        (package / name).write_text(
            source.read_text(encoding="utf-8").replace(K_LESSON_ID, K_PREREQUISITE_LESSON_ID), encoding="utf-8"
        )
    plan_path = repo / "content" / "curriculum" / "plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    canonical = yaml.safe_load((source_root / "content/curriculum/plan.yaml").read_text(encoding="utf-8"))
    plan["slots"].append(next(slot for slot in canonical["slots"] if slot["catalog_id"] == K_PREREQUISITE_LESSON_ID))
    dependent, prerequisite = plan["slots"]
    dependent["sequence_index"], prerequisite["sequence_index"] = (1, 0) if prerequisite_first else (0, 1)
    plan_path.write_text(yaml.safe_dump(plan, allow_unicode=True), encoding="utf-8")
    return repo


def _approve_release(repo: Path, monkeypatch, *, tag: str = K_TAG) -> Path:
    """Publish one release locally with faked upload adapters and return its archive."""
    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release._require_tag_matches_source", lambda *args, **kwargs: None
    )
    run_release(repo_root=repo, tag=tag, github_repository="owner/repo")
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_audio",
        lambda **kwargs: {"uploaded": 0, "skipped": 1},
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_asset",
        lambda **kwargs: None,
    )
    run_release(
        repo_root=repo,
        approve=True,
        tag=tag,
        github_repository="owner/repo",
        service_account_path=account,
        synthesis_client=FakeTtsClient(_wav(frames=200)),
    )
    return repo.joinpath(*K_RELEASE_SCRATCH) / tag / "lessons.tar.gz"


def _serve_release_archive(monkeypatch, archive: Path) -> None:
    """Serve one local archive as the published GitHub release asset."""

    def fake_download(*, repository: str, tag: str, asset_name: str, destination: Path) -> None:
        shutil.copyfile(archive, destination)

    monkeypatch.setattr("lesson_builder.application.operations.release.download_release_asset", fake_download)


def _rewrite_archive_member(source: Path, destination: Path, member_name: str, replacement: bytes) -> Path:
    """Copy one archive, replacing a single file member's bytes."""
    with tarfile.open(source, "r:gz") as reader, tarfile.open(destination, "w:gz") as writer:
        for member in reader.getmembers():
            extracted = reader.extractfile(member)
            payload = extracted.read() if extracted is not None else b""
            if member.name == member_name:
                member.size = len(replacement)
                payload = replacement
            writer.addfile(member, BytesIO(payload))
    return destination


def test_run_release_given_provider_free_workspace_expect_plan_without_credentials(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_release(repo_root=repo)

    assert result["approved"] is False
    assert result["next_command"].startswith("uv run lesson-data release --approve --tag v")
    assert result["lesson_count"] == 1
    assert result["candidate_count"] > 0
    assert result["missing_count"] == result["unique_count"]
    assert result["reused_count"] == 0
    assert result["estimated_cost_usd"] >= 0
    plan_path = Path(str(result["plan_path"]))
    assert plan_path.is_file()
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["source_digest"] == result["source_digest"]
    assert persisted["version"] == 2
    assert not (repo / "dist").exists()


@pytest.mark.parametrize("approve", [False, True])
def test_run_release_given_lesson_precedes_planned_prerequisite_expect_release_blocked(
    tmp_path: Path,
    approve: bool,
) -> None:
    repo = _prerequisite_order_repo(tmp_path, prerequisite_first=False)

    with pytest.raises(ValueError, match="release blocked by curriculum prerequisite order") as caught:
        run_release(repo_root=repo, approve=approve)

    message = str(caught.value)
    assert K_LESSON_ID in message
    assert K_PREREQUISITE_LESSON_ID in message
    assert "Move the prerequisite earlier" in message


def test_run_release_given_prerequisite_precedes_dependent_expect_release_plan_proceeds(
    tmp_path: Path,
) -> None:
    repo = _prerequisite_order_repo(tmp_path, prerequisite_first=True)

    result = run_release(repo_root=repo)

    assert result["approved"] is False
    assert result["lesson_count"] == 2


def test_run_release_given_draft_shadow_and_open_proposal_expect_pending_proposal_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    shutil.copytree(repo / "content" / "lessons" / K_LESSON_ID, repo / "drafts" / K_LESSON_ID)
    draft_lesson = repo / "drafts" / K_LESSON_ID / "lesson.md"
    draft_lesson.write_text(draft_lesson.read_text(encoding="utf-8") + "\nDraft-only paragraph.\n", encoding="utf-8")
    draft = read_workbench_source(repo, K_LESSON_ID)
    proposal_path = repo / "store" / "workbench" / "proposals" / f"{K_LESSON_ID}.json"
    proposal_path.parent.mkdir(parents=True)
    proposal_path.write_text(
        ProposalLog(
            proposals=[
                Proposal(
                    proposal_id="open-draft-proposal",
                    lesson_id=K_LESSON_ID,
                    source="chat",
                    created_against_sha256="draft-source",
                    note="Draft proposal",
                    blocks=[
                        ProposalBlock(
                            key="lesson.md",
                            before=draft.files["lesson.md"],
                            after="Proposed replacement",
                            note="Keep the draft suggestion",
                        )
                    ],
                )
            ]
        ).model_dump_json(),
        encoding="utf-8",
    )
    proposal_before = proposal_path.read_bytes()

    run_release(repo_root=repo)

    proposal_after = ProposalLog.model_validate_json(proposal_path.read_bytes())
    assert proposal_path.read_bytes() == proposal_before
    assert proposal_after.proposals[0].blocks[0].status == "pending"


def test_run_release_given_explicit_destination_expect_exact_approval_command(tmp_path: Path) -> None:
    result = run_release(
        repo_root=_repo(tmp_path),
        tag=K_TAG,
        github_repository="owner/repo",
    )

    assert result["next_command"] == (
        "uv run lesson-data release --approve --tag v2026.09.14 --github-repository owner/repo"
    )


def test_run_release_given_fingerprint_cache_audio_expect_reuse_without_provider(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = run_release(repo_root=repo)
    identity = _identities(repo)[0]
    cache = repo / "store" / "cache" / "v4-audio"
    cache.mkdir(parents=True)
    (cache / f"{identity.fingerprint}.wav").write_bytes(_wav())

    second = run_release(repo_root=repo)

    assert second["reused_count"] == 1
    assert second["missing_count"] == first["missing_count"] - 1
    reused = [item for item in second["candidates"] if item["reuse_source"]]
    assert len(reused) == 1
    assert reused[0]["fingerprint"] == identity.fingerprint


def test_run_release_given_open_audio_check_finding_expect_plan_and_export_regenerate_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    identity = _identities(repo)[0]
    cache = repo / "store" / "cache" / "v4-audio"
    cache.mkdir(parents=True)
    (cache / f"{identity.fingerprint}.wav").write_bytes(_wav())
    write_workbench_record(
        repo,
        "audio_index",
        K_LESSON_ID,
        WorkbenchAudioIndex(
            clips={
                identity.binding: WorkbenchAudioIndexClip(
                    fingerprint=identity.fingerprint,
                    source_digest="captured-source",
                    voice=identity.voice,
                    audio_id=identity.audio_id,
                    findings=[WorkbenchAudioCheckFinding(code="silent", message="Recording is silent.")],
                )
            }
        ),
    )

    plan = run_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")

    planned = next(item for item in plan["candidates"] if item["fingerprint"] == identity.fingerprint)
    assert planned["reuse_source"] is None
    assert plan["missing_count"] == plan["unique_count"]

    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release._require_tag_matches_source", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_audio",
        lambda **kwargs: {"uploaded": 0, "skipped": 1},
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_asset", lambda **kwargs: None
    )
    client = FakeTtsClient(_wav(frames=16_000))
    run_release(
        repo_root=repo,
        approve=True,
        tag=K_TAG,
        github_repository="owner/repo",
        service_account_path=account,
        synthesis_client=client,
    )

    assert (identity.text, identity.voice) in client.calls
    audio_index = read_workbench_record(repo, "audio_index", K_LESSON_ID, WorkbenchAudioIndex)
    assert [finding.code for finding in audio_index.clips[identity.binding].findings] == ["silent"]


def test_run_release_given_historical_scratch_audio_expect_reuse_by_uuid_stem(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identity = _identities(repo)[0]
    historical = repo / "store" / "scratch" / "old-export" / "dist" / "audio" / "lessons" / K_LESSON_ID
    historical.mkdir(parents=True)
    (historical / identity.filename).write_bytes(_wav())

    result = run_release(repo_root=repo)

    assert result["reused_count"] == 1
    reused = next(item for item in result["candidates"] if item["reuse_source"])
    assert Path(str(reused["reuse_source"])) == historical / identity.filename


def test_run_release_given_historical_audio_changed_after_plan_expect_approval_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    identity = _identities(repo)[0]
    historical = repo / "store" / "scratch" / "old-export" / "dist" / "audio" / "lessons" / K_LESSON_ID
    historical.mkdir(parents=True)
    recording = historical / identity.filename
    recording.write_bytes(_wav())
    run_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")
    recording.write_bytes(_wav(frames=200))
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release._require_tag_matches_source", lambda *args, **kwargs: None
    )

    with pytest.raises(ValueError, match="changed after planning"):
        run_release(
            repo_root=repo,
            approve=True,
            tag=K_TAG,
            github_repository="owner/repo",
            service_account_path=account,
        )

    assert not (repo / "store" / "cache" / "v4-audio" / f"{identity.fingerprint}.wav").exists()


def test_run_release_given_interrupted_historical_copy_expect_clean_retry(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    identity = _identities(repo)[0]
    historical = repo / "store" / "scratch" / "old-export" / "dist" / "audio" / "lessons" / K_LESSON_ID
    historical.mkdir(parents=True)
    (historical / identity.filename).write_bytes(_wav())
    run_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release._require_tag_matches_source", lambda *args, **kwargs: None
    )
    original_copy = shutil.copy2

    def interrupted_copy(source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError("simulated interruption")

    monkeypatch.setattr("lesson_builder.application.operations.release.shutil.copy2", interrupted_copy)

    with pytest.raises(OSError, match="simulated interruption"):
        run_release(
            repo_root=repo,
            approve=True,
            tag=K_TAG,
            github_repository="owner/repo",
            service_account_path=account,
        )

    cache = repo / "store" / "cache" / "v4-audio"
    destination = cache / f"{identity.fingerprint}.wav"
    assert not destination.exists()
    assert list(cache.glob("*.tmp")) == []

    monkeypatch.setattr("lesson_builder.application.operations.release.shutil.copy2", original_copy)
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_audio",
        lambda **kwargs: {"uploaded": 0, "skipped": 1},
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_asset",
        lambda **kwargs: None,
    )
    result = run_release(
        repo_root=repo,
        approve=True,
        tag=K_TAG,
        github_repository="owner/repo",
        service_account_path=account,
        synthesis_client=FakeTtsClient(_wav(frames=200)),
    )

    assert result["approved"] is True
    assert destination.read_bytes() == (historical / identity.filename).read_bytes()


def test_run_release_given_conflicting_historical_audio_expect_fail_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    identity = _identities(repo)[0]
    for root, frames in (("store/cache/v4-audio", 160), ("store/cache/audio", 200)):
        legacy = repo / root
        legacy.mkdir(parents=True)
        (legacy / f"{identity.fingerprint}.wav").write_bytes(_wav(frames))

    with pytest.raises(ValueError, match="conflicting historical audio"):
        run_release(repo_root=repo)


def test_run_release_given_approval_without_plan_expect_refusal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(ValueError, match="no release plan found"):
        run_release(repo_root=repo, approve=True, github_repository="owner/repo")


def test_run_release_given_source_changed_after_plan_expect_approval_rejects_stale_plan(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    run_release(repo_root=repo)
    lesson = repo / "content" / "lessons" / K_LESSON_ID / "lesson.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="stale"):
        run_release(repo_root=repo, approve=True, github_repository="owner/repo")


def test_run_release_given_estimated_cost_over_cap_expect_approval_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    plan_result = run_release(repo_root=repo)
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    plan_path = Path(str(plan_result["plan_path"]))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["estimated_cost_usd"] = 12.34
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds --max-cost"):
        run_release(
            repo_root=repo,
            approve=True,
            github_repository="owner/repo",
            service_account_path=account,
            max_cost=5.0,
        )


def test_run_release_given_approved_plan_expect_synthesizes_missing_and_publishes_audio_first(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release._require_tag_matches_source", lambda *args, **kwargs: None
    )
    identities = _identities(repo)
    cache = repo / "store" / "cache" / "v4-audio"
    cache.mkdir(parents=True)
    reused_identity = identities[0]
    (cache / f"{reused_identity.fingerprint}.wav").write_bytes(_wav())
    plan_result = run_release(repo_root=repo, tag=K_TAG)
    assert plan_result["reused_count"] == 1
    publication_order: list[str] = []

    def fake_upload_audio(**kwargs: object) -> dict[str, int]:
        publication_order.append("s3_audio")
        return {"uploaded": 0, "skipped": 1}

    def fake_upload_asset(**kwargs: object) -> None:
        publication_order.append("github_release")

    monkeypatch.setattr("lesson_builder.application.operations.publish_release.upload_release_audio", fake_upload_audio)
    monkeypatch.setattr("lesson_builder.application.operations.publish_release.upload_release_asset", fake_upload_asset)
    client = FakeTtsClient(_wav(frames=200))

    result = run_release(
        repo_root=repo,
        approve=True,
        tag=K_TAG,
        github_repository="owner/repo",
        service_account_path=account,
        synthesis_client=client,
    )

    assert result["approved"] is True
    assert result["tag"] == K_TAG
    assert result["repository"] == "owner/repo"
    release_voices = read_workbench_record(repo, "audio_release", "current", WorkbenchAudioReleaseVoices)
    assert release_voices.tag == K_TAG
    assert K_LESSON_ID in release_voices.lessons
    assert publication_order == ["s3_audio", "github_release"]
    assert "candidates" not in result["plan"]
    assert result["plan"]["unique_count"] == plan_result["unique_count"]
    assert result["plan"]["missing_count"] == plan_result["missing_count"]
    plan_candidates = json.loads(Path(str(plan_result["plan_path"])).read_text(encoding="utf-8"))["candidates"]
    synthesized = {tuple(pair) for pair in client.calls}
    missing_pairs = {(str(item["text"]), str(item["voice"])) for item in plan_candidates if not item["reuse_source"]}
    reused_pairs = {(str(item["text"]), str(item["voice"])) for item in plan_candidates if item["reuse_source"]}
    assert synthesized == missing_pairs
    assert reused_pairs.isdisjoint(synthesized)
    for identity in identities:
        assert (cache / f"{identity.fingerprint}.wav").is_file()
    release_dist = repo / "store" / "scratch" / "release" / K_TAG / "dist"
    packet = json.loads((release_dist / "lessons" / f"{K_LESSON_ID}.json").read_text(encoding="utf-8"))
    audio_entries = packet["media"]["audio"]
    assert audio_entries
    for entry in audio_entries:
        assert entry["url"].startswith(f"https://media.example.com/audio/lessons/{K_LESSON_ID}/")
        local = release_dist / entry["path"]
        assert local.is_file()
        assert hashlib.sha256(local.read_bytes()).hexdigest() == entry["sha256"]
    archive = repo / "store" / "scratch" / "release" / K_TAG / "lessons.tar.gz"
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert f"dist/lessons/{K_LESSON_ID}.json" in names
    assert not any(name.endswith(".wav") for name in names)
    assert result["publication"]["published"] == ["s3_audio", "github_release"]


@pytest.mark.parametrize("tag", ("../../dist", "/tmp/release", "v2026/09/14", "release-2026.09.14"))
def test_run_release_given_unsafe_tag_expect_refusal_before_synthesis(tmp_path: Path, tag: str) -> None:
    repo = _repo(tmp_path)
    client = FakeTtsClient(_wav())

    with pytest.raises(ValueError, match="vYYYY.MM.DD"):
        run_release(
            repo_root=repo,
            tag=tag,
            github_repository="owner/repo",
            synthesis_client=client,
        )

    assert client.calls == []


def test_run_release_given_tag_at_different_commit_expect_refusal_before_synthesis_or_publication(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "tagged source"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "tag", K_TAG], cwd=repo, check=True)
    (repo / "operator-note.txt").write_text("later commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "operator-note.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "later checkout"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    run_release(repo_root=repo, tag=K_TAG)
    client = FakeTtsClient(_wav())
    publication_calls: list[str] = []
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_audio",
        lambda **kwargs: publication_calls.append("s3"),
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.publish_release.upload_release_asset",
        lambda **kwargs: publication_calls.append("github"),
    )
    with pytest.raises(ValueError, match="does not identify"):
        run_release(
            repo_root=repo,
            approve=True,
            tag=K_TAG,
            github_repository="owner/repo",
            service_account_path=account,
            synthesis_client=client,
        )

    assert client.calls == []
    assert publication_calls == []


def test_run_release_given_tagged_head_with_uncommitted_source_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "tagged source"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "tag", K_TAG], cwd=repo, check=True)
    lesson = repo / "content" / "lessons" / K_LESSON_ID / "lesson.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setenv("LESSON_AUDIO_PUBLIC_BASE_URL", "https://media.example.com")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    run_release(repo_root=repo, tag=K_TAG)
    client = FakeTtsClient(_wav())

    with pytest.raises(ValueError, match="does not include the current source changes"):
        run_release(
            repo_root=repo,
            approve=True,
            tag=K_TAG,
            github_repository="owner/repo",
            service_account_path=account,
            synthesis_client=client,
        )

    assert client.calls == []


def test_run_release_given_remote_tag_at_different_commit_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "tagged source"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "tag", K_TAG], cwd=repo, check=True)
    run_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")
    account = repo / "service-account.json"
    account.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "lesson_builder.application.operations.release.resolve_release_tag_commit",
        lambda **kwargs: "different-remote-commit",
    )

    with pytest.raises(ValueError, match="GitHub repository.*does not identify HEAD"):
        run_release(
            repo_root=repo,
            approve=True,
            tag=K_TAG,
            github_repository="owner/repo",
            service_account_path=account,
        )


def test_verify_release_given_locally_published_archive_expect_tagged_source_match(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    archive = _approve_release(repo, monkeypatch)
    _serve_release_archive(monkeypatch, archive)

    result = verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")

    assert result["verified"] is True
    assert result["tag"] == K_TAG
    assert result["repository"] == "owner/repo"
    assert result["asset_name"] == "lessons.tar.gz"
    assert result["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["lesson_count"] == 1
    assert result["audio_reference_count"] > 0
    extracted_packets = list(
        (repo.joinpath(*K_RELEASE_SCRATCH) / "verify" / K_TAG / "extracted" / "dist" / "lessons").glob("*.json")
    )
    assert [packet.stem for packet in extracted_packets] == [K_LESSON_ID]


def test_verify_release_given_missing_release_asset_expect_fail_closed(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)

    def refusing_download(**kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["gh", "release", "download"])

    monkeypatch.setattr("lesson_builder.application.operations.release.download_release_asset", refusing_download)

    with pytest.raises(ValueError, match="does not expose"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_tampered_published_packet_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    archive = _approve_release(repo, monkeypatch)
    packet_member = f"dist/lessons/{K_LESSON_ID}.json"
    with tarfile.open(archive, "r:gz") as reader:
        extracted = reader.extractfile(packet_member)
        assert extracted is not None
        packet = json.loads(extracted.read().decode("utf-8"))
    packet["title"] = packet["title"] + " (tampered)"
    tampered = _rewrite_archive_member(
        archive,
        tmp_path / "tampered.tar.gz",
        packet_member,
        (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _serve_release_archive(monkeypatch, tampered)

    with pytest.raises(ValueError, match="stale against canonical source"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_archive_embedding_wav_bytes_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    _approve_release(repo, monkeypatch)
    package_distribution(
        repo_root=repo,
        distribution_root=repo.joinpath(*K_RELEASE_SCRATCH) / K_TAG,
        output_path=tmp_path / "with-wav.tar.gz",
        include_audio=True,
    )
    _serve_release_archive(monkeypatch, tmp_path / "with-wav.tar.gz")

    with pytest.raises(ValueError, match="must not embed audio"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_provider_free_text_archive_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    export_distribution(repo)
    package_distribution(
        repo_root=repo,
        distribution_root=repo,
        output_path=tmp_path / "provider-free.tar.gz",
    )
    _serve_release_archive(monkeypatch, tmp_path / "provider-free.tar.gz")

    with pytest.raises(ValueError, match="carries no audio"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_one_audio_binding_removed_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    archive = _approve_release(repo, monkeypatch)
    packet_member = f"dist/lessons/{K_LESSON_ID}.json"
    with tarfile.open(archive, "r:gz") as reader:
        extracted = reader.extractfile(packet_member)
        assert extracted is not None
        packet = json.loads(extracted.read().decode("utf-8"))
    removed_id = _remove_first_audio_reference(packet)
    packet["media"]["audio"] = [entry for entry in packet["media"]["audio"] if entry["id"] != removed_id]
    partial = _rewrite_archive_member(
        archive,
        tmp_path / "partial.tar.gz",
        packet_member,
        (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _serve_release_archive(monkeypatch, partial)

    with pytest.raises(ValueError, match="audio bindings do not match canonical source"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_two_audio_bindings_swapped_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    archive = _approve_release(repo, monkeypatch)
    packet_member = f"dist/lessons/{K_LESSON_ID}.json"
    with tarfile.open(archive, "r:gz") as reader:
        extracted = reader.extractfile(packet_member)
        assert extracted is not None
        packet = json.loads(extracted.read().decode("utf-8"))
    references = _audio_reference_objects(packet)
    assert len(references) >= 2
    references[0]["audio_id"], references[1]["audio_id"] = references[1]["audio_id"], references[0]["audio_id"]
    swapped = _rewrite_archive_member(
        archive,
        tmp_path / "swapped.tar.gz",
        packet_member,
        (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _serve_release_archive(monkeypatch, swapped)

    with pytest.raises(ValueError, match="audio bindings do not match canonical source"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def test_verify_release_given_two_audio_media_locations_swapped_expect_refusal(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    archive = _approve_release(repo, monkeypatch)
    packet_member = f"dist/lessons/{K_LESSON_ID}.json"
    with tarfile.open(archive, "r:gz") as reader:
        extracted = reader.extractfile(packet_member)
        assert extracted is not None
        packet = json.loads(extracted.read().decode("utf-8"))
    media = packet["media"]["audio"]
    assert len(media) >= 2
    for key in ("path", "url", "sha256"):
        media[0][key], media[1][key] = media[1][key], media[0][key]
    swapped = _rewrite_archive_member(
        archive,
        tmp_path / "media-swapped.tar.gz",
        packet_member,
        (json.dumps(packet, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    _serve_release_archive(monkeypatch, swapped)

    with pytest.raises(ValueError, match="audio media do not match canonical identities"):
        verify_release(repo_root=repo, tag=K_TAG, github_repository="owner/repo")


def _audio_reference_objects(value: object) -> list[dict[str, object]]:
    """Return packet objects carrying nested audio bindings outside media inventory."""
    if isinstance(value, list):
        return [reference for item in value for reference in _audio_reference_objects(item)]
    if not isinstance(value, dict):
        return []
    references = [value] if isinstance(value.get("audio_id"), str) else []
    for key, item in value.items():
        if key != "media":
            references.extend(_audio_reference_objects(item))
    return references


def _remove_first_audio_reference(value: object) -> str:
    """Remove and return one nested public audio binding from a packet fixture."""
    if isinstance(value, list):
        for item in value:
            try:
                return _remove_first_audio_reference(item)
            except LookupError:
                continue
    elif isinstance(value, dict):
        audio_id = value.pop("audio_id", None)
        if isinstance(audio_id, str):
            return audio_id
        for item in value.values():
            try:
                return _remove_first_audio_reference(item)
            except LookupError:
                continue
    raise LookupError("packet contains no audio reference")
