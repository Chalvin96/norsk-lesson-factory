"""Entry point: batch plan and aggregate artifact behavior tests."""

import threading
from pathlib import Path

import pytest

from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.workflow.lesson_generation.batch_artifacts import build_slot_run_id
from lesson_builder.workflow.lesson_generation.batch_artifacts import load_batch
from lesson_builder.workflow.lesson_generation.batch_artifacts import write_batch_result
from lesson_builder.workflow.lesson_generation.batch_plan import build_slot_plan_hash
from lesson_builder.workflow.lesson_generation.batch_plan import render_plan
from lesson_builder.workflow.lesson_generation.batch_plan import select_catalog_ids
from lesson_builder.workflow.lesson_generation.models import LessonBatchResult
from lesson_builder.workflow.lesson_generation.models import LessonResult


def test_render_plan_given_approved_slot_expect_author_brief_with_scope_and_objective() -> None:
    rendered = render_plan(_slot())

    assert "catalog_id: grammar_one" in rendered
    assert "scope: required" in rendered
    assert "Teach this approved catalog outcome: Use one pattern in context." in rendered


def test_select_catalog_ids_given_unknown_id_expect_actionable_error() -> None:
    plan = CurriculumPlan(
        schema_version="0.5-catalog-plan",
        run_id="plan-test",
        source_catalog="catalog.yaml",
        source_snapshot="test",
        summary={"approved_entries": 1, "provisional_cefr_entries": 0},
        slots=[_slot()],
    )

    with pytest.raises(ValueError, match="not present in plan"):
        select_catalog_ids(plan, ("missing",))


def test_write_batch_result_given_typed_result_expect_round_trip_from_scratch_yaml(tmp_path: Path) -> None:
    result = LessonBatchResult(
        status="completed",
        batch_id="batch-one",
        plan_path="plan.yaml",
        job="author",
        max_workers=1,
        total=1,
        parked=0,
        failed=1,
        human_gates_pending=0,
        results=[
            LessonResult(
                catalog_id="grammar_one",
                run_id=build_slot_run_id("batch-one", "grammar_one"),
                status="failed",
                human_gate="not_reached",
                output_root="scratch",
                error="test failure",
            )
        ],
    )

    write_batch_result(tmp_path / "batch", result)

    assert load_batch(tmp_path / "batch" / "batch.yaml") == result


def test_write_batch_result_given_replace_failure_expect_existing_batch_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "batch"
    batch_path = output_root / "batch.yaml"
    original = _batch_result(job="original")
    write_batch_result(output_root, original)
    result = LessonBatchResult(
        status="completed",
        batch_id="batch-one",
        plan_path="plan.yaml",
        job="author",
        max_workers=1,
        total=0,
        parked=0,
        failed=0,
        human_gates_pending=0,
        results=[],
    )

    def fail_replace(_source: Path, _target: Path) -> Path:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_batch_result(output_root, result)

    assert load_batch(batch_path) == original


def test_write_batch_result_given_concurrent_writers_expect_serialized_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root = tmp_path / "batch"
    first_result = _batch_result(job="first")
    second_result = _batch_result(job="second")
    first_replace_started = threading.Event()
    release_first_replace = threading.Event()
    second_replace_started = threading.Event()
    second_thread_started = threading.Event()
    replace_count = 0
    replace_guard = threading.Lock()
    original_replace = Path.replace

    def controlled_replace(source: Path, target: Path) -> Path:
        nonlocal replace_count
        if target.name == "batch.yaml":
            with replace_guard:
                replace_count += 1
                current_replace = replace_count
            if current_replace == 1:
                first_replace_started.set()
                if not release_first_replace.wait(timeout=5):
                    raise TimeoutError("first replacement was not released")
            else:
                second_replace_started.set()
        return original_replace(source, target)

    errors: list[BaseException] = []

    def persist(result: LessonBatchResult, started: threading.Event) -> None:
        started.set()
        try:
            write_batch_result(output_root, result)
        except BaseException as exc:  # noqa: BLE001 - capture thread failures for the test assertion
            errors.append(exc)

    monkeypatch.setattr(Path, "replace", controlled_replace)
    first_thread = threading.Thread(target=persist, args=(first_result, threading.Event()))
    second_thread = threading.Thread(target=persist, args=(second_result, second_thread_started))
    first_thread.start()
    try:
        assert first_replace_started.wait(timeout=5)
        second_thread.start()
        assert second_thread_started.wait(timeout=5)
        assert not second_replace_started.wait(timeout=0.5)
    finally:
        release_first_replace.set()
        first_thread.join(timeout=5)
        second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert load_batch(output_root / "batch.yaml") in (first_result, second_result)


def test_build_slot_run_id_given_known_inputs_expect_compatible_identifier() -> None:
    assert build_slot_run_id("batch-one", "grammar_one") == "batch-one-grammar_one-c13a6cb2df"


def test_build_slot_plan_hash_given_known_slot_expect_compatible_hash() -> None:
    assert build_slot_plan_hash(_slot()) == ("sha256:b24f76c0bd02a361a14f6ebfeee9be6d75a950c2898eea5bf51f43f45ddddce9")


def _slot(catalog_id: str = "grammar_one") -> CurriculumPlanSlot:
    return CurriculumPlanSlot(
        sequence_index=0,
        catalog_id=catalog_id,
        catalog_kind="grammar",
        title="Grammar one",
        learner_outcome="Use one pattern in context.",
        cefr_level="A1",
        cefr_source="catalog",
    )


def _batch_result(*, job: str) -> LessonBatchResult:
    return LessonBatchResult(
        status="completed",
        batch_id="batch-one",
        plan_path="plan.yaml",
        job=job,
        max_workers=1,
        total=0,
        parked=0,
        failed=0,
        human_gates_pending=0,
        results=[],
    )
