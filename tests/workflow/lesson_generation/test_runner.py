"""Tests for the catalog-package runner: run, resume, show, list.

These exercise the real default collaborators (file-backed copier, export
compiler, ledger writer) and the real LangGraph SQLite checkpointer against a
tmp_path repo root. The checked-in catalog-package fixture is the source; all
outputs are disposable scratch under the tmp_path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from lesson_builder.workflow.lesson_generation.runner import LessonPackageThreadNotFoundError
from lesson_builder.workflow.lesson_generation.runner import LessonPackageThreadNotParkedError
from lesson_builder.workflow.lesson_generation.runner import build_checkpoints_path
from lesson_builder.workflow.lesson_generation.runner import list_lesson_packages
from lesson_builder.workflow.lesson_generation.runner import resume_lesson_package
from lesson_builder.workflow.lesson_generation.runner import run_lesson_package
from lesson_builder.workflow.lesson_generation.runner import show_lesson_package
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REQUIRED_FILES
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_TRANSCRIPT_FILE
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import FakeLessonPackageDeps
from tests.workflow.lesson_generation.fakes import FakeRichAuthoringStages


def test_run_catalog_package_given_default_fixture_expect_parks_at_human_gate(tmp_path: Path):
    result = run_lesson_package(repo_root=tmp_path, run_id="run1")

    assert result["stage"] == "parked"
    assert result["next"] == ["human_gate"]
    assert result["run_id"] == "run1"
    assert result["thread_id"] == "catalog_generation:run1"
    assert result["source_hash"].startswith("sha256:")
    assert not (tmp_path / "store/scratch/catalog_generation/run1/export").exists()
    assert not (tmp_path / "store/scratch/catalog_generation/run1/audio").exists()


def test_run_catalog_package_given_generate_flag_expect_inspectable_model_package_at_gate(tmp_path: Path):
    fake_stages = FakeRichAuthoringStages()
    fake_deps = FakeLessonPackageDeps()
    deps = fake_deps.deps()
    deps.rich_stages = fake_stages

    result = run_lesson_package(
        repo_root=tmp_path,
        run_id="generated1",
        deps=deps,
        generate=True,
        job="author",
    )

    generated_dir = tmp_path / "store/scratch/catalog_generation/generated1/generated"
    copied_dir = tmp_path / "store/scratch/catalog_generation/generated1/source"
    assert result["stage"] == "parked"
    assert result["generation_job"] == "author"
    assert result["generated_source_dir"] == str(generated_dir)
    assert result["llm_receipt_path"] == str(tmp_path / "store/scratch/catalog_generation/generated1/llm_receipt.json")
    assert set(K_LESSON_GENERATION_REQUIRED_FILES) <= {path.name for path in generated_dir.iterdir()}
    assert {path.name for path in copied_dir.iterdir()} == {
        *K_LESSON_GENERATION_REQUIRED_FILES,
        "exercise_requests.yaml",
        "stage_attestations.json",
    }
    assert fake_stages.calls == [
        "draft_authored",
        "draft_reviewed",
        "draft_normalized",
        "normalization_preserved",
        "intent_reviewed",
        "exercises_authored",
        "exercises_compiled",
        "exercises_verified",
        "complete",
    ]


def test_run_catalog_package_given_rich_stage_failure_expect_same_run_resumes_failed_stage(tmp_path: Path):
    fake_deps = FakeLessonPackageDeps()
    stages = FakeRichAuthoringStages(fail_stage="draft_reviewed")
    deps = fake_deps.deps()
    deps.rich_stages = stages

    with pytest.raises(RuntimeError, match="injected draft_reviewed failure"):
        run_lesson_package(
            repo_root=tmp_path,
            run_id="generated-retry",
            deps=deps,
            generate=True,
            job="author",
        )

    result = run_lesson_package(
        repo_root=tmp_path,
        run_id="generated-retry",
        deps=deps,
        generate=True,
        job="author",
    )

    assert result["stage"] == "parked"
    assert stages.calls.count("draft_authored") == 1
    assert stages.calls.count("draft_reviewed") == 2


def test_run_catalog_package_given_stop_after_generation_retry_expect_prepare_boundary_remains(tmp_path: Path):
    fake_deps = FakeLessonPackageDeps()
    stages = FakeRichAuthoringStages(fail_stage="draft_reviewed")
    deps = fake_deps.deps()
    deps.rich_stages = stages

    with pytest.raises(RuntimeError, match="injected draft_reviewed failure"):
        run_lesson_package(
            repo_root=tmp_path,
            run_id="generated-stop-retry",
            deps=deps,
            generate=True,
            job="author",
            stop_after_generation=True,
        )

    result = run_lesson_package(
        repo_root=tmp_path,
        run_id="generated-stop-retry",
        deps=deps,
        generate=True,
        job="author",
        stop_after_generation=True,
    )

    assert result["next"] == ["prepare"]
    assert stages.calls.count("draft_authored") == 1
    assert stages.calls.count("draft_reviewed") == 2


def test_run_catalog_package_given_changed_generation_source_expect_resume_refused(tmp_path: Path):
    source = tmp_path / "approved-plan"
    shutil.copytree(K_CATALOG_PACKAGE_FIXTURE_ROOT, source)
    fake_deps = FakeLessonPackageDeps()
    stages = FakeRichAuthoringStages(fail_stage="draft_reviewed")
    deps = fake_deps.deps()
    deps.rich_stages = stages

    with pytest.raises(RuntimeError, match="injected draft_reviewed failure"):
        run_lesson_package(
            repo_root=tmp_path,
            run_id="changed-input",
            fixture_source=source,
            deps=deps,
            generate=True,
            job="author",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            catalog_id="question_word_order",
        )

    source.joinpath("plan.md").write_text(
        source.joinpath("plan.md").read_text(encoding="utf-8") + "\nChanged after checkpoint.\n",
        encoding="utf-8",
    )
    calls_before_retry = list(stages.calls)
    with pytest.raises(ValueError, match="changed immutable input"):
        run_lesson_package(
            repo_root=tmp_path,
            run_id="changed-input",
            fixture_source=source,
            deps=deps,
            generate=True,
            job="author",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            catalog_id="question_word_order",
        )

    assert stages.calls == calls_before_retry


def test_run_catalog_package_given_generated_source_without_attestations_expect_refusal(
    tmp_path: Path,
) -> None:
    fake_deps = FakeLessonPackageDeps()
    deps = fake_deps.deps()
    deps.rich_stages = FakeRichAuthoringStages(with_stage_attestations=False)

    with pytest.raises(ValueError, match="stage attestations are missing"):
        run_lesson_package(
            repo_root=tmp_path,
            run_id="generated-missing-review",
            deps=deps,
            generate=True,
            job="author",
        )


def test_run_catalog_package_given_same_approved_fixture_expect_stable_slot_and_hashes(tmp_path: Path):
    first = run_lesson_package(repo_root=tmp_path / "one", run_id="same1")
    second = run_lesson_package(repo_root=tmp_path / "two", run_id="same2")

    assert first["source_hash"] == second["source_hash"]
    assert first["scheduled_slot"] == second["scheduled_slot"]
    assert first["export_hash"] == second["export_hash"]
    assert first["semantic_hash"] == second["semantic_hash"]
    assert first["dependency_hash"] == second["dependency_hash"]


def test_run_catalog_package_given_approved_fixture_expect_derived_transcript_and_no_mutation(tmp_path: Path):
    original = {
        name: (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_bytes() for name in K_LESSON_GENERATION_REQUIRED_FILES
    }
    run_lesson_package(repo_root=tmp_path, run_id="source1")

    copied = tmp_path / "store/scratch/catalog_generation/source1/source"
    assert sorted(path.name for path in copied.iterdir()) == sorted(
        (
            *K_LESSON_GENERATION_REQUIRED_FILES,
            K_LESSON_GENERATION_TRANSCRIPT_FILE,
            K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE,
        )
    )
    assert (copied / K_LESSON_GENERATION_TRANSCRIPT_FILE).read_text(encoding="utf-8").startswith("package_version:")
    (copied / "lesson.md").write_text(
        (copied / "lesson.md").read_text(encoding="utf-8") + "\nLocal revision only.\n",
        encoding="utf-8",
    )
    assert {
        name: (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_bytes() for name in K_LESSON_GENERATION_REQUIRED_FILES
    } == original


def test_run_catalog_package_given_accept_resume_expect_exports_and_ledger(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="accept1")

    result = resume_lesson_package("accept1", "accept", repo_root=tmp_path)

    assert result["next"] == []
    assert result["stage"] == "accepted"
    assert result["export_path"] is not None
    assert result["ledger_path"] is not None

    export_file = Path(result["export_path"])
    assert export_file.exists()
    export_doc = json.loads(export_file.read_text())
    assert "lesson" in export_doc
    assert "transcript_ids" in export_doc

    ledger_file = Path(result["ledger_path"])
    assert ledger_file.exists()
    lines = [line for line in ledger_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "accepted"
    ledger_entry = json.loads(lines[0])
    assert {
        "artifact_id",
        "source_hash",
        "semantic_hash",
        "dependency_hash",
        "decision",
        "reviewer",
        "ts",
    } <= ledger_entry.keys()


def test_resume_catalog_package_given_batch_bound_evidence_changed_at_gate_expect_no_canonical_write(
    tmp_path: Path,
) -> None:
    fake_deps = FakeLessonPackageDeps()
    deps = fake_deps.deps()
    deps.rich_stages = FakeRichAuthoringStages()
    run_lesson_package(
        repo_root=tmp_path,
        run_id="stale-batch-evidence",
        deps=deps,
        generate=True,
        job="author",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        catalog_id="question_word_order",
    )
    evidence = tmp_path / "store/scratch/catalog_generation/stale-batch-evidence/source/stage_attestations.json"
    evidence.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh stage evidence is stale"):
        resume_lesson_package("stale-batch-evidence", "accept", repo_root=tmp_path)
    assert not (tmp_path / "content/approvals").exists()
    assert not (tmp_path / "content/lessons").exists()


def test_resume_catalog_package_given_promotion_failed_after_approval_expect_same_approval_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_deps = FakeLessonPackageDeps()
    deps = fake_deps.deps()
    deps.rich_stages = FakeRichAuthoringStages()
    run_lesson_package(
        repo_root=tmp_path,
        run_id="retry-batch-approval",
        deps=deps,
        generate=True,
        job="author",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        catalog_id="question_word_order",
    )
    import lesson_builder.workflow.lesson_generation.promotion as promotion_module

    original = promotion_module.promote_lesson_approval
    failed = False

    def fail_once(*args: object, **kwargs: object) -> str:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected promotion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(promotion_module, "promote_lesson_approval", fail_once)
    with pytest.raises(RuntimeError, match="injected promotion failure"):
        resume_lesson_package("retry-batch-approval", "accept", repo_root=tmp_path)
    approvals = list((tmp_path / "content/approvals/lessons/question_word_order").iterdir())
    assert len(approvals) == 1
    approval_id = yaml.safe_load((approvals[0] / "approval.yaml").read_text(encoding="utf-8"))["approval_id"]
    monkeypatch.setattr(promotion_module, "promote_lesson_approval", original)
    accepted = resume_lesson_package("retry-batch-approval", "accept", repo_root=tmp_path)
    assert accepted["stage"] == "accepted"
    assert len(list((tmp_path / "content/approvals/lessons/question_word_order").iterdir())) == 1
    assert yaml.safe_load((approvals[0] / "approval.yaml").read_text(encoding="utf-8"))["approval_id"] == approval_id
    assert (tmp_path / "content/lessons/question_word_order").is_dir()


def test_run_catalog_package_given_defer_resume_expect_ends_without_export(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="defer1")

    result = resume_lesson_package("defer1", "defer", repo_root=tmp_path)

    assert result["next"] == []
    assert result["stage"] == "deferred"
    assert result["export_path"] is None


def test_run_catalog_package_given_reject_resume_expect_ends_without_export(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="reject1")

    result = resume_lesson_package("reject1", "reject", repo_root=tmp_path)

    assert result["next"] == []
    assert result["stage"] == "rejected"
    assert result["export_path"] is None


def test_resume_catalog_package_given_accepted_thread_resumed_again_expect_no_duplicate_ledger(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="once1")
    accepted = resume_lesson_package("once1", "accept", repo_root=tmp_path)
    ledger_path = Path(accepted["ledger_path"])
    before = ledger_path.read_text(encoding="utf-8")

    with pytest.raises(LessonPackageThreadNotParkedError):
        resume_lesson_package("once1", "accept", repo_root=tmp_path)

    assert ledger_path.read_text(encoding="utf-8") == before
    assert len(before.splitlines()) == 1


def test_show_catalog_package_given_parked_thread_expect_returns_state(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="show1")

    result = show_lesson_package("show1", repo_root=tmp_path)

    assert result["stage"] == "parked"
    assert result["next"] == ["human_gate"]
    assert "export_doc" not in result


def test_show_catalog_package_given_full_flag_expect_export_doc_in_summary(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="show2")

    result = show_lesson_package("show2", repo_root=tmp_path, full=True)

    assert result["export_doc"] is not None
    assert "lesson" in result["export_doc"]


def test_list_catalog_packages_given_two_parked_runs_expect_both_listed(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="list-a")
    run_lesson_package(repo_root=tmp_path, run_id="list-b")

    rows = list_lesson_packages(repo_root=tmp_path)

    run_ids = {row["run_id"] for row in rows}
    assert {"list-a", "list-b"} <= run_ids
    for row in rows:
        assert row["stage"] == "parked"


def test_list_catalog_packages_given_accepted_run_expect_not_listed(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="done1")
    resume_lesson_package("done1", "accept", repo_root=tmp_path)

    rows = list_lesson_packages(repo_root=tmp_path)

    assert "done1" not in {row["run_id"] for row in rows}


def test_resume_catalog_package_given_unknown_thread_expect_not_found_error(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="exists")

    with pytest.raises(LessonPackageThreadNotFoundError):
        resume_lesson_package("never-ran", "accept", repo_root=tmp_path)


def test_resume_catalog_package_given_already_terminal_thread_expect_not_parked_error(tmp_path: Path):
    run_lesson_package(repo_root=tmp_path, run_id="terminal")
    resume_lesson_package("terminal", "accept", repo_root=tmp_path)

    with pytest.raises(LessonPackageThreadNotParkedError):
        resume_lesson_package("terminal", "accept", repo_root=tmp_path)


def test_show_catalog_package_given_unknown_thread_expect_not_found_error(tmp_path: Path):
    with pytest.raises(LessonPackageThreadNotFoundError):
        show_lesson_package("never-ran", repo_root=tmp_path)


def test_checkpoints_path_given_repo_root_expect_store_db(tmp_path: Path):
    assert build_checkpoints_path(tmp_path) == tmp_path / "store" / "catalog_generation_checkpoints.db"


def test_run_catalog_package_given_generated_run_id_expect_valid_thread_id(tmp_path: Path):
    result = run_lesson_package(repo_root=tmp_path)

    assert result["run_id"] is not None
    assert len(result["run_id"]) > 0
    assert result["thread_id"] == f"catalog_generation:{result['run_id']}"


def test_run_catalog_package_given_invalid_run_id_expect_value_error(tmp_path: Path):
    with pytest.raises(ValueError, match="run_id"):
        run_lesson_package(repo_root=tmp_path, run_id="bad run id with spaces")


def test_run_catalog_package_given_missing_fixture_expect_filenotfounderror(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        run_lesson_package(repo_root=tmp_path, run_id="r1", fixture_source=tmp_path / "no-such-dir")
