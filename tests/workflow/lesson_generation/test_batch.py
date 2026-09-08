"""Behavior tests for `generate_lessons` proposal compilation routing."""

import shutil
import threading
from pathlib import Path

import pytest
import yaml

from lesson_builder.domain.curriculum.models import CatalogTeachingPoint
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.workflow.lesson_generation import batch
from lesson_builder.workflow.lesson_generation.batch_artifacts import load_batch
from lesson_builder.workflow.lesson_generation.batch_plan import build_slot_plan_hash
from lesson_builder.workflow.lesson_generation.dependencies import default_proposal_source_copier
from lesson_builder.workflow.lesson_generation.models import LessonResult
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import write_passing_stage_attestations


def _write_generated_source(path: Path) -> None:
    """Create one fully reviewed generated-source fixture for batch tests."""
    path.mkdir(parents=True, exist_ok=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        shutil.copy2(K_CATALOG_PACKAGE_FIXTURE_ROOT / name, path / name)
    write_passing_stage_attestations(path)


def _catalog_plan(
    tmp_path: Path,
    catalog_ids: list[str],
    *,
    schema_version: str = "0.2-catalog-plan",
) -> Path:
    plan = CurriculumPlan(
        schema_version=schema_version,
        run_id="plan-test",
        source_catalog="catalog.yaml",
        source_snapshot="test",
        summary={"approved_entries": len(catalog_ids), "provisional_cefr_entries": 0},
        slots=[
            CurriculumPlanSlot(
                sequence_index=index,
                catalog_id=catalog_id,
                catalog_kind="grammar",
                title=f"Lesson {catalog_id}",
                learner_outcome="Teach one pattern.",
                cefr_level="A1",
                cefr_source="catalog",
            )
            for index, catalog_id in enumerate(catalog_ids)
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return plan_path


def test_generate_catalog_lessons_given_committed_plan_schema_expect_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _catalog_plan(
        tmp_path,
        ["lesson_one"],
        schema_version="0.5-catalog-plan",
    )
    failed_result = LessonResult(
        catalog_id="lesson_one",
        run_id="batch-lesson_one",
        status="failed",
        human_gate="not_reached",
        output_root="batch-lesson_one",
        error="smoke failure",
    )
    monkeypatch.setattr(batch, "_generate_one", lambda *_args: failed_result)

    result = batch.generate_lessons(
        repo_root=tmp_path,
        plan_path=Path("plan.yaml"),
        batch_id="batch-schema-05",
        max_workers=1,
    )

    assert result.total == 1
    assert result.failed == 1


def test_generate_catalog_lessons_given_generated_proposal_expect_proposal_copier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated packages must not be revalidated as already-approved source."""
    plan = CurriculumPlan(
        schema_version="0.2-catalog-plan",
        run_id="plan-test",
        source_catalog="catalog.yaml",
        source_snapshot="test",
        summary={"approved_entries": 1, "provisional_cefr_entries": 0},
        slots=[
            CurriculumPlanSlot(
                sequence_index=0,
                catalog_id="lesson_one",
                catalog_kind="grammar",
                title="Lesson one",
                learner_outcome="Teach one pattern.",
                cefr_level="A1",
                cefr_source="catalog",
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    generated_source = tmp_path / "generated"
    _write_generated_source(generated_source)
    generated_result = LessonResult(
        catalog_id="lesson_one",
        run_id="batch-lesson_one",
        status="parked",
        human_gate="pending",
        generated_source_dir="generated",
        output_root="generated",
    )
    monkeypatch.setattr(batch, "_generate_one", lambda *_args: generated_result)
    copiers: list[object] = []

    def fake_run_catalog_package(*, deps, **_kwargs):
        copiers.append(deps.copier)
        return {
            "exercise_diagnostics": {
                "status": "needs_human",
                "findings": ["phase_gap:controlled"],
            }
        }

    monkeypatch.setattr(batch, "run_lesson_package", fake_run_catalog_package)

    result = batch.generate_lessons(
        repo_root=tmp_path,
        plan_path=Path("plan.yaml"),
        batch_id="batch-test",
        max_workers=1,
    )

    assert result.parked == 1
    assert copiers == [default_proposal_source_copier]


def test_generate_one_given_grammar_slot_expect_checkpointed_lesson_generation_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id="adjective_agreement",
        catalog_kind="grammar",
        title="Adjective agreement",
        learner_outcome="Choose the adjective form that fits the noun.",
        cefr_level="A1",
        cefr_source="catalog",
    )
    calls: list[str] = []
    metadata: dict[str, object] = {}

    def fake_run_catalog_package(**kwargs: object) -> dict[str, str]:
        calls.append("graph")
        repo_root = kwargs["repo_root"]
        run_id = kwargs["run_id"]
        assert isinstance(repo_root, Path)
        assert isinstance(run_id, str)
        metadata.update(
            curriculum_slot_sha256=kwargs["curriculum_slot_sha256"],
            catalog_id=kwargs["catalog_id"],
        )
        source_dir = repo_root / "store" / "scratch" / "catalog_generation" / run_id / "generated"
        source_dir.mkdir(parents=True)
        receipt_path = source_dir.parent / "llm_receipt.json"
        receipt_path.write_text("{}\n", encoding="utf-8")
        return {"generated_source_dir": str(source_dir)}

    monkeypatch.setattr(batch, "run_lesson_package", fake_run_catalog_package)

    result = batch._generate_one(tmp_path, "grammar-batch", slot, "author")

    assert result.status == "generated"
    assert calls == ["graph"]
    assert metadata == {"curriculum_slot_sha256": result.slot_plan_hash, "catalog_id": slot.catalog_id}
    assert result.generated_source_dir is not None


def test_generate_one_given_content_remediation_expect_human_remediation_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id="needs_content_review",
        catalog_kind="grammar",
        title="Needs content review",
        learner_outcome="Teach one approved pattern.",
        cefr_level="A1",
        cefr_source="catalog",
    )

    def blocked_run_catalog_package(**kwargs: object) -> dict[str, str]:
        repo_root = kwargs["repo_root"]
        run_id = kwargs["run_id"]
        assert isinstance(repo_root, Path)
        assert isinstance(run_id, str)
        (repo_root / "store" / "scratch" / "catalog_generation" / run_id / "generated").mkdir(parents=True)
        raise batch.ContentRemediationRequired("unresolved lesson finding", stage="lesson_review")

    monkeypatch.setattr(batch, "run_lesson_package", blocked_run_catalog_package)

    result = batch._generate_one(tmp_path, "remediation-batch", slot, "author")

    assert result.status == "needs_human_remediation"
    assert result.human_gate == "not_reached"
    assert result.generated_source_dir is not None
    assert result.error is not None
    assert "lesson_review" in result.error


@pytest.mark.parametrize(
    "catalog_kind",
    ["phraseology", "communicative", "pronunciation", "writing"],
)
def test_generate_one_given_scheduled_catalog_kind_expect_checkpointed_lesson_generation_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_kind: str
) -> None:
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id=f"sample_{catalog_kind}",
        catalog_kind=catalog_kind,
        title=f"Sample {catalog_kind}",
        learner_outcome="Teach the approved learner outcome.",
        cefr_level="A1",
        cefr_source="catalog",
    )
    calls: list[str] = []

    def fake_run_catalog_package(**kwargs: object) -> dict[str, str]:
        calls.append("graph")
        repo_root = kwargs["repo_root"]
        run_id = kwargs["run_id"]
        assert isinstance(repo_root, Path)
        assert isinstance(run_id, str)
        source_dir = repo_root / "store" / "scratch" / "catalog_generation" / run_id / "generated"
        source_dir.mkdir(parents=True)
        receipt_path = source_dir.parent / "llm_receipt.json"
        receipt_path.write_text("{}\n", encoding="utf-8")
        return {"generated_source_dir": str(source_dir)}

    monkeypatch.setattr(batch, "run_lesson_package", fake_run_catalog_package)

    result = batch._generate_one(tmp_path, f"{catalog_kind}-batch", slot, "author")

    assert result.status == "generated"
    assert calls == ["graph"]


def test_recompile_catalog_lessons_given_unattested_source_expect_failed_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A compiler fix must replay source packages without another model call."""
    plan = CurriculumPlan(
        schema_version="0.1-catalog-plan",
        run_id="plan-test",
        source_catalog="catalog.yaml",
        source_snapshot="test",
        summary={"approved_entries": 1, "provisional_cefr_entries": 0},
        slots=[
            CurriculumPlanSlot(
                sequence_index=0,
                catalog_id="lesson_one",
                catalog_kind="grammar",
                title="Lesson one",
                learner_outcome="Teach one pattern.",
                cefr_level="A1",
                cefr_source="catalog",
            )
        ],
    )
    plan_path = tmp_path / "plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    source_dir = tmp_path / "generated"
    source_dir.mkdir()
    (source_dir / "plan.md").write_text("---\ntitle: Old plan title\n---\n", encoding="utf-8")
    (source_dir / "lesson.md").write_text("---\ntitle: Old title\n---\n", encoding="utf-8")
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "completed",
                "batch_id": "source-batch",
                "plan_path": "plan.yaml",
                "job": "author",
                "max_workers": 6,
                "total": 1,
                "parked": 0,
                "failed": 1,
                "human_gates_pending": 0,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-batch-lesson_one",
                        "status": "failed",
                        "human_gate": "not_reached",
                        "generated_source_dir": "generated",
                        "output_root": "source-batch-lesson_one",
                        "error": "compiler bug",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    copiers: list[object] = []

    def fake_run_catalog_package(*, deps, **_kwargs):
        copiers.append(deps.copier)
        return {
            "exercise_diagnostics": {
                "status": "needs_human",
                "findings": ["phase_gap:controlled"],
            }
        }

    monkeypatch.setattr(batch, "run_lesson_package", fake_run_catalog_package)

    result = batch.recompile_lessons(
        repo_root=tmp_path,
        plan_path=Path("plan.yaml"),
        source_batch_path=Path("source-batch.yaml"),
        batch_id="replay-batch",
    )

    assert result.parked == 0
    assert result.failed == 1
    assert result.results[0].error is not None
    assert "fresh stage evidence" in result.results[0].error
    assert copiers == []
    assert (tmp_path / "store/scratch/catalog_generation/batch-replay-batch/batch.yaml").is_file()
    assert result.results[0].generated_source_dir is None


def test_generate_catalog_lessons_given_interrupt_expect_partial_checkpoint_and_queued_cancellation(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one", "lesson_two"])
    cancellations: list[str] = []

    def fake_generate(root, batch_id, slot, job):
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=f"{batch_id}-{slot.catalog_id}",
            status="failed",
            human_gate="not_reached",
            output_root=f"store/scratch/catalog_generation/{slot.catalog_id}",
            error="model stopped",
        )

    def interrupt_after_one(futures):
        yield next(iter(futures))
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_generate_one", fake_generate)
    monkeypatch.setattr(batch, "as_completed", interrupt_after_one)
    monkeypatch.setattr(
        batch,
        "cancel_active_clients",
        lambda: cancellations.append("cancelled"),
    )

    with pytest.raises(KeyboardInterrupt):
        batch.generate_lessons(
            repo_root=tmp_path,
            plan_path=plan_path.relative_to(tmp_path),
            batch_id="interrupted-batch",
            max_workers=1,
        )

    checkpoint = tmp_path / "store/scratch/catalog_generation/batch-interrupted-batch/batch.yaml"
    payload = yaml.safe_load(checkpoint.read_text(encoding="utf-8"))
    assert payload["status"] == "interrupted"
    assert {item["status"] for item in payload["results"]} == {"failed", "pending"}
    assert cancellations == ["cancelled"]


def test_resume_catalog_lessons_given_interrupt_expect_active_opencode_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one"])
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "interrupted",
                "batch_id": "source-batch",
                "plan_path": str(plan_path.relative_to(tmp_path)),
                "job": "author",
                "max_workers": 1,
                "total": 1,
                "parked": 0,
                "failed": 1,
                "human_gates_pending": 0,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-one",
                        "status": "failed",
                        "human_gate": "not_reached",
                        "generated_source_dir": "generated-one",
                        "output_root": "generated-one",
                        "slot_plan_hash": build_slot_plan_hash(
                            CurriculumPlan.model_validate(yaml.safe_load(plan_path.read_text(encoding="utf-8"))).slots[
                                0
                            ]
                        ),
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cancellations: list[str] = []

    def fake_generate(root, batch_id, slot, job):
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=f"{batch_id}-{slot.catalog_id}",
            status="failed",
            human_gate="not_reached",
            output_root=f"store/scratch/catalog_generation/{slot.catalog_id}",
            error="model stopped",
        )

    def interrupt_before_result(_futures):
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_generate_one", fake_generate)
    monkeypatch.setattr(batch, "as_completed", interrupt_before_result)
    monkeypatch.setattr(
        batch,
        "cancel_active_clients",
        lambda: cancellations.append("cancelled"),
    )

    with pytest.raises(KeyboardInterrupt):
        batch.resume_lessons(
            repo_root=tmp_path,
            source_batch_path=source_batch.relative_to(tmp_path),
            batch_id="interrupted-resume",
        )

    checkpoint = tmp_path / "store/scratch/catalog_generation/batch-interrupted-resume/batch.yaml"
    payload = yaml.safe_load(checkpoint.read_text(encoding="utf-8"))
    assert payload["status"] == "interrupted"
    assert cancellations == ["cancelled"]


def test_generate_catalog_lessons_given_checkpoint_failure_expect_workers_stop_before_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one", "lesson_two"])
    second_started = threading.Event()
    cancellation_requested = threading.Event()
    second_finished = threading.Event()
    reset_calls: list[str] = []
    checkpoint_calls = 0
    original_write_running = batch._write_running_batch

    def fake_generate(root, batch_id, slot, job):
        if slot.catalog_id == "lesson_two":
            second_started.set()
            cancellation_requested.wait(timeout=5)
            second_finished.set()
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=f"{batch_id}-{slot.catalog_id}",
            status="failed",
            human_gate="not_reached",
            output_root=f"store/scratch/catalog_generation/{slot.catalog_id}",
            error="model stopped",
        )

    def fail_checkpoint(*args, **kwargs):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            assert second_started.wait(timeout=5)
            raise OSError("checkpoint failed")
        return original_write_running(*args, **kwargs)

    monkeypatch.setattr(batch, "_generate_one", fake_generate)
    monkeypatch.setattr(batch, "_write_running_batch", fail_checkpoint)
    monkeypatch.setattr(batch, "cancel_active_clients", cancellation_requested.set)
    monkeypatch.setattr(batch, "reset_client_cancellation", lambda: reset_calls.append("reset"))

    with pytest.raises(OSError, match="checkpoint failed"):
        batch.generate_lessons(
            repo_root=tmp_path,
            plan_path=plan_path.relative_to(tmp_path),
            batch_id="checkpoint-failure-batch",
            max_workers=2,
        )

    checkpoint = tmp_path / "store/scratch/catalog_generation/batch-checkpoint-failure-batch/batch.yaml"
    saved = load_batch(checkpoint)
    assert saved.status == "running"
    assert saved.total == 2
    assert cancellation_requested.is_set()
    assert second_finished.is_set()
    assert reset_calls == ["reset"]


def test_resume_catalog_lessons_given_partial_batch_expect_completed_owner_not_regenerated(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one", "lesson_two"])
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "interrupted",
                "batch_id": "source-batch",
                "plan_path": "plan.yaml",
                "job": "author",
                "max_workers": 1,
                "total": 2,
                "parked": 1,
                "failed": 1,
                "human_gates_pending": 1,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-one",
                        "status": "parked",
                        "human_gate": "pending",
                        "generated_source_dir": "generated-one",
                        "output_root": "generated-one",
                        "slot_plan_hash": build_slot_plan_hash(
                            CurriculumPlan.model_validate(yaml.safe_load(plan_path.read_text(encoding="utf-8"))).slots[
                                0
                            ]
                        ),
                    },
                    {
                        "catalog_id": "lesson_two",
                        "run_id": "source-two",
                        "status": "failed",
                        "human_gate": "not_reached",
                        "generated_source_dir": None,
                        "output_root": "source-two",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    generated_one = tmp_path / "generated-one"
    _write_generated_source(generated_one)
    generated_dir = tmp_path / "generated-two"
    _write_generated_source(generated_dir)
    calls: list[str] = []

    def fake_generate(root, batch_id, slot, job):
        calls.append(slot.catalog_id)
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=f"{batch_id}-{slot.catalog_id}",
            status="generated",
            human_gate="not_reached",
            generated_source_dir="generated-two",
            output_root="generated-two",
        )

    monkeypatch.setattr(batch, "_generate_one", fake_generate)
    monkeypatch.setattr(batch, "run_lesson_package", lambda **_kwargs: {})

    result = batch.resume_lessons(
        repo_root=tmp_path,
        source_batch_path=source_batch.relative_to(tmp_path),
        batch_id="resumed-batch",
    )

    assert calls == ["lesson_two"]
    assert result.status == "completed"
    assert result.parked == 2


def test_resume_catalog_lessons_given_remediation_source_expect_review_retry_without_regeneration(
    tmp_path: Path, monkeypatch
) -> None:
    _catalog_plan(tmp_path, ["lesson_one"])
    source_dir = tmp_path / "source-one" / "generated"
    _write_generated_source(source_dir)
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "completed",
                "batch_id": "source-batch",
                "plan_path": "plan.yaml",
                "job": "author",
                "max_workers": 1,
                "total": 1,
                "parked": 0,
                "failed": 0,
                "human_gates_pending": 0,
                "remediation_pending": 1,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-one",
                        "status": "needs_human_remediation",
                        "human_gate": "not_reached",
                        "generated_source_dir": "source-one/generated",
                        "output_root": "source-one",
                        "error": "reviewer_unavailable",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str | None, str | None]] = []

    def fake_generate(
        root,
        batch_id,
        slot,
        job,
        *,
        run_id=None,
        output_root=None,
    ):
        calls.append((slot.catalog_id, run_id, str(output_root) if output_root else None))
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=run_id or f"{batch_id}-{slot.catalog_id}",
            status="generated",
            human_gate="not_reached",
            generated_source_dir="source-one/generated",
            output_root="source-one",
        )

    monkeypatch.setattr(batch, "_generate_one", fake_generate)
    monkeypatch.setattr(batch, "run_lesson_package", lambda **_kwargs: {})

    result = batch.resume_lessons(
        repo_root=tmp_path,
        source_batch_path=source_batch.relative_to(tmp_path),
        batch_id="remediation-resume",
    )

    assert calls == [("lesson_one", "source-one", str(tmp_path / "source-one"))]
    assert result.status == "completed"
    assert result.parked == 1


def test_resume_catalog_lessons_given_filtered_source_batch_expect_only_source_owners(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one", "lesson_two", "lesson_three"])
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "completed",
                "batch_id": "source-batch",
                "plan_path": str(plan_path.relative_to(tmp_path)),
                "job": "author",
                "max_workers": 1,
                "total": 2,
                "parked": 0,
                "failed": 2,
                "human_gates_pending": 0,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-one",
                        "status": "failed",
                        "human_gate": "not_reached",
                        "generated_source_dir": None,
                        "output_root": "source-one",
                    },
                    {
                        "catalog_id": "lesson_three",
                        "run_id": "source-three",
                        "status": "failed",
                        "human_gate": "not_reached",
                        "generated_source_dir": None,
                        "output_root": "source-three",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_generate(root, batch_id, slot, job):
        calls.append(slot.catalog_id)
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=f"{batch_id}-{slot.catalog_id}",
            status="failed",
            human_gate="not_reached",
            output_root=f"store/scratch/catalog_generation/{slot.catalog_id}",
            error="model stopped",
        )

    monkeypatch.setattr(batch, "_generate_one", fake_generate)

    result = batch.resume_lessons(
        repo_root=tmp_path,
        source_batch_path=source_batch.relative_to(tmp_path),
        batch_id="filtered-resume",
    )

    assert calls == ["lesson_one", "lesson_three"]
    assert [item.catalog_id for item in result.results] == ["lesson_one", "lesson_three"]


def test_resume_catalog_lessons_given_generated_uncompiled_owner_expect_compile_only(
    tmp_path: Path, monkeypatch
) -> None:
    plan_path = _catalog_plan(tmp_path, ["lesson_one"])
    source_dir = tmp_path / "generated-one"
    _write_generated_source(source_dir)
    source_batch = tmp_path / "source-batch.yaml"
    source_batch.write_text(
        yaml.safe_dump(
            {
                "status": "interrupted",
                "batch_id": "source-batch",
                "plan_path": str(plan_path.relative_to(tmp_path)),
                "job": "author",
                "max_workers": 1,
                "total": 1,
                "parked": 0,
                "failed": 0,
                "human_gates_pending": 0,
                "results": [
                    {
                        "catalog_id": "lesson_one",
                        "run_id": "source-one",
                        "status": "generated",
                        "human_gate": "not_reached",
                        "generated_source_dir": "generated-one",
                        "output_root": "source-one",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        batch,
        "_generate_one",
        lambda *_args: pytest.fail("a recorded generated package was regenerated"),
    )
    monkeypatch.setattr(batch, "run_lesson_package", lambda **_kwargs: {})

    result = batch.resume_lessons(
        repo_root=tmp_path,
        source_batch_path=source_batch.relative_to(tmp_path),
        batch_id="compile-resume",
    )

    assert result.parked == 1
    assert result.results[0].status == "parked"


def test_render_plan_given_0_3_slot_expect_preserves_family_full_cefr_teaching_points_and_prereqs() -> None:
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id="grammar_multi",
        catalog_kind="grammar",
        title="Multi-level grammar",
        learner_outcome="Use the target pattern in context.",
        cefr_level="A1",
        cefr_source="catalog",
        family_id="family-word-order",
        cefr_tags=["A1", "A2", "B1"],
        teaching_points=[
            CatalogTeachingPoint(id="tp-vo-finite", statement="Recognise the finite verb position."),
            CatalogTeachingPoint(id="tp-vo-fronting", statement="Keep V2 after fronting."),
        ],
        terminology_ids=["finite-verb", "main-clause"],
        prerequisites=["grammar_base"],
        helpful_prerequisites=["phraseology_intro"],
    )

    rendered = batch._render_plan(slot)

    assert "family_id: family-word-order" in rendered
    assert "cefr_level: A1" in rendered
    assert "cefr_target_basis: earliest_approved_tag" in rendered
    assert "learner_outcome: Use the target pattern in context." in rendered
    assert "cefr_tags:" in rendered
    for tag in ("A1", "A2", "B1"):
        assert tag in rendered
    assert "tp-vo-finite" in rendered
    assert "Keep V2 after fronting." in rendered
    assert "finite-verb" in rendered
    assert "main-clause" in rendered
    assert "grammar_base" in rendered
    assert "phraseology_intro" in rendered
    metadata = yaml.safe_load(rendered.split("---", 2)[1])
    assert [item["statement"] for item in metadata["objectives"]] == [
        "Recognise the finite verb position.",
        "Keep V2 after fronting.",
    ]
    required_units = [unit for unit in metadata["coverage"] if unit["scope"] == "required"]
    assert [unit["claim"] for unit in required_units] == [
        "Recognise the finite verb position.",
        "Keep V2 after fronting.",
    ]
    assert len({unit["objective"] for unit in required_units}) == 2
    assert metadata["terminology_ids"] == ["finite-verb", "main-clause"]


def test_render_plan_given_legacy_slot_without_teaching_points_expect_outcome_fallback() -> None:
    slot = CurriculumPlanSlot(
        sequence_index=0,
        catalog_id="grammar_legacy",
        catalog_kind="grammar",
        title="Legacy grammar",
        learner_outcome="Use the target pattern in context.",
        cefr_level="A1",
        cefr_source="catalog",
    )

    rendered = batch._render_plan(slot)
    metadata = yaml.safe_load(rendered.split("---", 2)[1])

    assert metadata["objectives"] == [
        {
            "id": "obj-grammar-legacy-01",
            "statement": "Use the target pattern in context.",
        }
    ]
    assert metadata["coverage"][0]["claim"] == "Use the target pattern in context."


def test_batch_result_given_distinct_quality_failures_expect_separate_counters() -> None:
    statuses = (
        "content_needs_human",
        "reviewer_unavailable",
        "reviewer_invalid",
        "content_repair_invalid",
    )
    results = [
        LessonResult(
            catalog_id=f"lesson-{index}",
            run_id=f"run-{index}",
            status="parked",
            human_gate="pending",
            output_root=f"run-{index}",
            quality_status=status,
        )
        for index, status in enumerate(statuses, start=1)
    ]

    result = batch._batch_result(
        status="completed",
        batch_id="quality-states",
        plan_path=Path("plan.yaml"),
        job="author",
        max_workers=1,
        results=results,
    )

    assert result.quality_content_needs_human == 1
    assert result.quality_reviewer_unavailable == 1
    assert result.quality_reviewer_invalid == 1
    assert result.quality_content_repair_invalid == 1
    assert result.quality_needs_human == 0
