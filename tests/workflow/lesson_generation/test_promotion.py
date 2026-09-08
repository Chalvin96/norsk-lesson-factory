"""Behavior tests for explicit catalog lesson promotion."""

import hashlib
import json
import shutil
from functools import partial
from pathlib import Path

import pytest
import yaml

from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.workflow.lesson_generation.batch_plan import render_plan as render_lesson_plan
from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.dependencies import default_export_compiler
from lesson_builder.workflow.lesson_generation.dependencies import default_proposal_source_copier
from lesson_builder.workflow.lesson_generation.models import LessonBatchResult
from lesson_builder.workflow.lesson_generation.models import LessonPromotionResult
from lesson_builder.workflow.lesson_generation.models import LessonResult
from lesson_builder.workflow.lesson_generation.promotion import LessonPromotionError
from lesson_builder.workflow.lesson_generation.promotion import promote_lesson_batch
from lesson_builder.workflow.lesson_generation.runner import run_lesson_package
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import write_passing_stage_attestations


def test_promote_catalog_batch_given_parked_complete_batch_expect_source_and_export(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)

    result = promote_lesson_batch(
        repo_root=tmp_path,
        source_batch_path=batch_path.relative_to(tmp_path),
        auto_approve=True,
    )

    assert result.promoted == 1
    source_dir = tmp_path / "content" / "lessons" / "question_word_order"
    assert sorted(path.name for path in source_dir.iterdir()) == [
        "exercises.yaml",
        "lesson.md",
        "plan.md",
    ]
    assert (tmp_path / "dist" / "lessons" / "question_word_order.json").is_file()
    catalog = yaml.safe_load((tmp_path / "dist" / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["lessons"][0]["lesson_id"] == "question_word_order"
    assert catalog["lessons"][0]["position"] == 0
    approval = yaml.safe_load(
        (
            tmp_path / "store" / "scratch" / "catalog_generation" / "approvals" / "promotion-batch-lesson-approval.yaml"
        ).read_text(encoding="utf-8")
    )
    assert approval["schema_version"] == "compatibility-index-v1"
    assert approval["approvals"]
    assert all("content/approvals/lessons/" in path for path in approval["approvals"])


def test_promote_catalog_batch_given_pending_result_expect_no_canonical_write(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path, result_status="pending", human_gate="not_reached")

    with pytest.raises(LessonPromotionError, match="completed batch|parked"):
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )

    assert not (tmp_path / "content" / "lessons").exists()
    assert not (tmp_path / "dist").exists()


def test_promote_catalog_batch_given_auto_approval_without_attestations_expect_refusal(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path, with_stage_attestations=False)

    with pytest.raises(LessonPromotionError, match="stage attestations are missing"):
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )

    assert not (tmp_path / "content" / "lessons").exists()
    assert not (tmp_path / "dist").exists()


def test_promote_catalog_batch_given_stale_lesson_attestation_expect_refusal(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    lesson_path = tmp_path / "store" / "scratch" / "source-package" / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8") + "\nA stale post-review edit.\n",
        encoding="utf-8",
    )

    with pytest.raises(LessonPromotionError, match="attestation is stale"):
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )


def test_promote_catalog_batch_given_changed_existing_source_without_replace_expect_refusal(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    existing = tmp_path / "content" / "lessons" / "question_word_order"
    existing.mkdir(parents=True)
    (existing / "lesson.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(LessonPromotionError, match="--replace"):
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )

    assert (existing / "lesson.md").read_text(encoding="utf-8") == "changed\n"


def test_promote_catalog_batch_given_same_package_twice_expect_idempotent_result(
    tmp_path: Path,
) -> None:
    batch_path = _write_batch(tmp_path)
    first = promote_lesson_batch(
        repo_root=tmp_path,
        source_batch_path=batch_path.relative_to(tmp_path),
        auto_approve=True,
    )
    second = promote_lesson_batch(
        repo_root=tmp_path,
        source_batch_path=batch_path.relative_to(tmp_path),
        auto_approve=True,
    )

    assert first.promoted == 1
    assert second.promoted == 0
    assert second.unchanged == 1


def test_promote_catalog_batch_given_assembly_error_expect_promotions_retained(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)
    plan_path = tmp_path / "content" / "curriculum" / "plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["slots"].append({**plan["slots"][0], "catalog_id": "missing_package"})
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

    with pytest.raises(LessonPromotionError, match="distribution export failed") as error:
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )
    assert error.value.result is not None
    assert error.value.result.status == "distribution_failed"
    assert error.value.result.distribution_status == "failed"
    assert error.value.result.outcomes[0].approval_id is not None
    assert error.value.result.promoted == 1

    assert (tmp_path / "content" / "lessons" / "question_word_order").exists()
    assert (tmp_path / "content" / "approvals" / "lessons" / "question_word_order").exists()
    assert not (tmp_path / "dist").exists()
    assert not (tmp_path / "store" / "scratch" / "catalog_generation" / "approvals").exists()
    assert not list(tmp_path.glob(".promotion-staging-*"))


def test_promote_catalog_batch_given_missing_curriculum_plan_expect_refusal(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)
    (tmp_path / "content" / "curriculum" / "plan.yaml").unlink()

    with pytest.raises(LessonPromotionError, match="curriculum plan not found"):
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )

    assert not (tmp_path / "content" / "lessons").exists()
    assert not (tmp_path / "dist").exists()


def test_promote_catalog_batch_given_install_rename_error_expect_previous_state_restored(
    tmp_path: Path, monkeypatch
) -> None:
    batch_path = _write_batch(tmp_path)
    first = promote_lesson_batch(
        repo_root=tmp_path,
        source_batch_path=batch_path.relative_to(tmp_path),
        auto_approve=True,
    )
    assert first.promoted == 1
    previous_lesson = (tmp_path / "content" / "lessons" / "question_word_order" / "lesson.md").read_bytes()
    previous_dist = (tmp_path / "dist" / "catalog.json").read_bytes()
    previous_ledger = (
        tmp_path / "store" / "scratch" / "catalog_generation" / "approvals" / "promotion-batch-lesson-approval.yaml"
    ).read_bytes()

    original_rename = Path.rename
    failing_target = str(tmp_path / "dist")
    failed_once = False

    def failing_rename(self: Path, target: str | Path) -> Path:
        nonlocal failed_once
        if not failed_once and str(target) == failing_target:
            failed_once = True
            raise OSError("simulated rename failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", failing_rename)
    with pytest.raises(LessonPromotionError, match="distribution export failed") as error:
        promote_lesson_batch(
            repo_root=tmp_path,
            source_batch_path=batch_path.relative_to(tmp_path),
            auto_approve=True,
        )
    assert error.value.result is not None
    assert error.value.result.status == "distribution_failed"
    monkeypatch.undo()

    assert (tmp_path / "content" / "lessons" / "question_word_order" / "lesson.md").read_bytes() == previous_lesson
    assert (tmp_path / "dist" / "catalog.json").read_bytes() == previous_dist
    assert (
        tmp_path / "store" / "scratch" / "catalog_generation" / "approvals" / "promotion-batch-lesson-approval.yaml"
    ).read_bytes() == previous_ledger
    assert (tmp_path / "content" / "approvals" / "lessons" / "question_word_order").exists()


def test_promote_catalog_batch_given_two_parked_packages_expect_both_installed(tmp_path: Path) -> None:
    batch_path = _write_batch(tmp_path)
    second_source = tmp_path / "store" / "scratch" / "source-package-two"
    second_source.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        content = (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        content = content.replace("question_word_order", "question_word_order_two").replace(
            "question-word-order", "question-word-order-two"
        )
        (second_source / name).write_text(content, encoding="utf-8")
    write_passing_stage_attestations(second_source)
    plan_path = tmp_path / "content" / "curriculum" / "plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    from lesson_builder.domain.curriculum.models import CurriculumPlanSlot

    second_slot = CurriculumPlanSlot(
        sequence_index=1,
        catalog_id="question_word_order_two",
        catalog_kind="grammar",
        title="Question word order two",
        learner_outcome="Form direct Norwegian questions.",
        cefr_level="A1",
        cefr_source="catalog",
    )
    plan["slots"].append(second_slot.model_dump(mode="json"))
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    batch_payload = yaml.safe_load(batch_path.read_text(encoding="utf-8"))
    second_hash = "sha256:" + hashlib.sha256(render_lesson_plan(second_slot).encode()).hexdigest()
    batch_payload["total"] = 2
    batch_payload["parked"] = 2
    batch_payload["human_gates_pending"] = 2
    batch_payload["results"].append(
        {
            **batch_payload["results"][0],
            "catalog_id": "question_word_order_two",
            "run_id": "promotion-run-question-word-order-two",
            "generated_source_dir": "store/scratch/source-package-two",
            "output_root": "store/scratch/source-package-two",
            "slot_plan_hash": second_hash,
        }
    )
    batch_path.write_text(yaml.safe_dump(batch_payload, sort_keys=False), encoding="utf-8")
    run_lesson_package(
        repo_root=tmp_path,
        run_id="promotion-run-question-word-order-two",
        fixture_source=second_source,
        deps=_promotion_deps(),
        curriculum_slot_sha256=second_hash,
        catalog_id="question_word_order_two",
    )

    result = promote_lesson_batch(
        repo_root=tmp_path,
        source_batch_path=batch_path.relative_to(tmp_path),
        auto_approve=True,
    )

    assert result.promoted == 2
    assert sorted(result.catalog_ids) == ["question_word_order", "question_word_order_two"]
    assert len(result.outcomes) == 2
    assert all(outcome.approval_id for outcome in result.outcomes)
    catalog = json.loads((tmp_path / "dist" / "catalog.json").read_text(encoding="utf-8"))
    assert [entry["lesson_id"] for entry in catalog["lessons"]] == [
        "question_word_order",
        "question_word_order_two",
    ]
    approval = yaml.safe_load(
        (
            tmp_path / "store" / "scratch" / "catalog_generation" / "approvals" / "promotion-batch-lesson-approval.yaml"
        ).read_text(encoding="utf-8")
    )
    assert len(approval["approvals"]) == 2
    assert all(path.startswith("content/approvals/lessons/") for path in approval["approvals"])


def test_promote_catalog_batch_given_second_preparation_failure_expect_structured_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_path = _write_two_lesson_batch(tmp_path)
    original = __import__(
        "lesson_builder.workflow.lesson_generation.promotion", fromlist=["_prepare_package"]
    )._prepare_package

    def fail_second(root: Path, result: LessonResult):
        if result.catalog_id == "question_word_order_two":
            raise LessonPromotionError("stage attestations are stale")
        return original(root, result)

    monkeypatch.setattr("lesson_builder.workflow.lesson_generation.promotion._prepare_package", fail_second)
    with pytest.raises(LessonPromotionError) as error:
        promote_lesson_batch(repo_root=tmp_path, source_batch_path=batch_path.relative_to(tmp_path), auto_approve=True)
    result = error.value.result
    assert result is not None
    assert (result.status, result.distribution_status, result.total, result.promoted, result.unchanged) == (
        "incomplete",
        "not_attempted",
        2,
        1,
        0,
    )
    assert [outcome.status for outcome in result.outcomes] == ["promoted", "failed"]
    assert result.outcomes[0].approval_path and result.outcomes[0].approval_id
    assert result.outcomes[1].catalog_id == "question_word_order_two"
    assert (tmp_path / "content/lessons/question_word_order").is_dir()
    assert not (tmp_path / "dist").exists()


def test_promote_catalog_batch_given_second_acceptance_failure_expect_retryable_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_path = _write_two_lesson_batch(tmp_path)
    import lesson_builder.workflow.lesson_generation.runner as runner_module

    original = runner_module.resume_lesson_package
    failed = False

    def fail_second(run_id: str, decision: str, *, repo_root: Path):
        nonlocal failed
        if run_id.endswith("two") and not failed:
            failed = True
            raise RuntimeError("injected acceptance failure")
        return original(run_id, decision, repo_root=repo_root)

    monkeypatch.setattr(runner_module, "resume_lesson_package", fail_second)
    with pytest.raises(LessonPromotionError) as error:
        promote_lesson_batch(repo_root=tmp_path, source_batch_path=batch_path.relative_to(tmp_path), auto_approve=True)
    result = error.value.result
    assert result is not None
    assert result.status == "incomplete"
    assert result.distribution_status == "not_attempted"
    assert result.promoted == 1
    assert result.outcomes[0].approval_id


def test_promote_lessons_cli_given_partial_promotion_error_expect_nonzero_serialized_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from lesson_builder.cli import main

    partial = LessonPromotionResult(
        status="incomplete",
        batch_id="b",
        source_batch_path="batch.yaml",
        source_root="content/lessons",
        distribution_root="dist/lessons",
        approval_path="content/approvals/lessons/a/x",
        total=2,
        promoted=1,
        unchanged=0,
        catalog_ids=["a", "b"],
        source_hashes={"a": "sha256:" + "1" * 64},
        export_hashes={},
        distribution_status="not_attempted",
        outcomes=[
            {"catalog_id": "a", "approval_path": "p", "approval_id": "i", "status": "promoted"},
            {"catalog_id": "b", "status": "failed"},
        ],
    )

    def fail(**kwargs: object) -> LessonPromotionResult:
        raise LessonPromotionError("later lesson failed", result=partial)

    import lesson_builder.workflow.lesson_generation as lesson_generation

    monkeypatch.setattr(lesson_generation, "promote_lesson_batch", fail)
    exit_code = main(
        ["promote-lessons", "--source-batch", "batch.yaml", "--auto-approve", "--repo-root", str(tmp_path)]
    )
    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"status": "incomplete"' in output
    assert '"promoted": 1' in output
    assert '"approval_id": "i"' in output


def _write_batch(
    tmp_path: Path,
    *,
    status: str = "completed",
    result_status: str = "parked",
    human_gate: str = "pending",
    with_stage_attestations: bool = True,
) -> Path:
    source = tmp_path / "store" / "scratch" / "source-package"
    source.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, source / name)
    if with_stage_attestations:
        write_passing_stage_attestations(source)
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id="question_word_order",
        catalog_kind="grammar",
        title="Question word order",
        learner_outcome="Form direct Norwegian questions.",
        cefr_level="A1",
        cefr_source="catalog",
    )
    plan_path = tmp_path / "content" / "curriculum" / "plan.yaml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.5-catalog-plan",
                "run_id": "promotion-plan",
                "source_catalog": "content/catalog/approved/catalog.yaml",
                "source_snapshot": "test",
                "summary": {"approved_entries": 1},
                "slots": [slot.model_dump(mode="json")],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    slot_plan_hash = "sha256:" + hashlib.sha256(render_lesson_plan(slot).encode()).hexdigest()
    result = LessonResult(
        catalog_id="question_word_order",
        run_id="promotion-run-question-word-order",
        status=result_status,  # type: ignore[arg-type]
        human_gate=human_gate,  # type: ignore[arg-type]
        generated_source_dir="store/scratch/source-package",
        output_root="store/scratch/source-package",
        slot_plan_hash=slot_plan_hash,
    )
    batch = LessonBatchResult(
        status=status,  # type: ignore[arg-type]
        batch_id="promotion-batch",
        plan_path="content/curriculum/plan.yaml",
        job="author",
        max_workers=1,
        total=1,
        parked=1 if result_status == "parked" else 0,
        failed=1 if result_status == "failed" else 0,
        human_gates_pending=1 if result_status == "parked" else 0,
        results=[result],
    )
    batch_path = tmp_path / "store" / "scratch" / "batch.yaml"
    batch_path.write_text(yaml.safe_dump(batch.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    if result_status == "parked" and with_stage_attestations:
        run_lesson_package(
            repo_root=tmp_path,
            run_id=result.run_id,
            fixture_source=source,
            deps=_promotion_deps(),
            curriculum_slot_sha256=slot_plan_hash,
            catalog_id=result.catalog_id,
        )
    return batch_path


def _write_two_lesson_batch(tmp_path: Path) -> Path:
    """Create two real parked LangGraph checkpoints for partial-result tests."""
    batch_path = _write_batch(tmp_path)
    second_source = tmp_path / "store" / "scratch" / "source-package-two"
    second_source.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        content = (K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_text(encoding="utf-8")
        (second_source / name).write_text(
            content.replace("question_word_order", "question_word_order_two").replace(
                "question-word-order", "question-word-order-two"
            ),
            encoding="utf-8",
        )
    write_passing_stage_attestations(second_source)
    plan_path = tmp_path / "content" / "curriculum" / "plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    second_slot = CurriculumPlanSlot(
        sequence_index=1,
        catalog_id="question_word_order_two",
        catalog_kind="grammar",
        title="Question word order two",
        learner_outcome="Form direct Norwegian questions.",
        cefr_level="A1",
        cefr_source="catalog",
    )
    plan["slots"].append(second_slot.model_dump(mode="json"))
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    batch_payload = yaml.safe_load(batch_path.read_text(encoding="utf-8"))
    second_hash = "sha256:" + hashlib.sha256(render_lesson_plan(second_slot).encode()).hexdigest()
    batch_payload.update({"total": 2, "parked": 2, "human_gates_pending": 2})
    batch_payload["results"].append(
        {
            **batch_payload["results"][0],
            "catalog_id": "question_word_order_two",
            "run_id": "promotion-run-question-word-order-two",
            "generated_source_dir": "store/scratch/source-package-two",
            "output_root": "store/scratch/source-package-two",
            "slot_plan_hash": second_hash,
        }
    )
    batch_path.write_text(yaml.safe_dump(batch_payload, sort_keys=False), encoding="utf-8")
    run_lesson_package(
        repo_root=tmp_path,
        run_id="promotion-run-question-word-order-two",
        fixture_source=second_source,
        deps=_promotion_deps(),
        curriculum_slot_sha256=second_hash,
        catalog_id="question_word_order_two",
    )
    return batch_path


def _promotion_deps() -> LessonGenerationDeps:
    """Return graph deps whose compile gate uses an explicit empty terminology policy."""
    return LessonGenerationDeps(
        copier=default_proposal_source_copier,
        compiler=partial(default_export_compiler, terminology_bans=TerminologyBans()),
    )
