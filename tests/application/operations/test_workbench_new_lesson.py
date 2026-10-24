"""Entry point: isolated new lesson generation and rollback behavior tests."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.compile_lesson_package import compile_lesson_package
from lesson_builder.application.operations.workbench_curriculum import check_curriculum
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_lessons import list_workbench_lessons
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_new_lesson import NewLessonFailure
from lesson_builder.application.operations.workbench_new_lesson import NewLessonRequest
from lesson_builder.application.operations.workbench_new_lesson import create_workbench_lesson
from lesson_builder.application.operations.workbench_new_lesson import validate_new_lesson
from lesson_builder.application.operations.workbench_new_lesson_records import AuthoredLessonResponse
from lesson_builder.domain.curriculum.services.plan_policy import hash_catalog_content
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_scalars
from tests.application.fakes import FakeLlmClient
from tests.workspace.builders import build_workbench_author_response
from tests.workspace.builders import build_workbench_curriculum_repository


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return build_workbench_curriculum_repository(tmp_path)


def test_new_lesson_given_provider_setup_expect_drafting_stage_before_setup(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []
    setup_steps: list[str] = []
    client = FakeLlmClient.responding(build_workbench_author_response(workspace))

    def create_client(*args: object, **kwargs: object) -> FakeLlmClient:
        setup_steps.append(steps[-1])
        return client

    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", create_client)
    create_workbench_lesson(workspace, _request(), threading.RLock(), steps.append)
    assert setup_steps == ["drafting lesson"]


@pytest.mark.parametrize("repair", [False, True])
def test_new_lesson_given_valid_or_repaired_response_expect_compiling_draft_only_and_one_commit(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, repair: bool
) -> None:
    response = build_workbench_author_response(workspace)
    responses = iter(["invalid", response] if repair else [response])
    client = FakeLlmClient.from_fn(lambda prompt, model: next(responses))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    steps: list[str] = []
    result = create_workbench_lesson(workspace, _request(), threading.RLock(), steps.append)
    assert result["check"] == {"status": "passed", "errors": []}
    assert len(client.calls) == (2 if repair else 1)
    assert "repairing (1/2)" in steps if repair else "repairing (1/2)" not in steps
    assert steps[-1] == "done"
    assert resolve_lesson_package(workspace, "new_lesson").source == "draft"
    assert read_workbench_source(workspace, "new_lesson").started_from is None
    assert not (workspace / "content" / "lessons" / "new_lesson").exists()
    assert [lesson.lesson_id for lesson in list_workbench_lessons(workspace)] == [
        "greet_and_introduce_yourself",
        "new_lesson",
        "second",
    ]
    assert check_curriculum(workspace) == ()
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "2"
    assert set(_git(workspace, "show", "--format=", "--name-only", "HEAD").split()) == {
        "content/curriculum/plan.yaml",
        "content/catalog/approved/catalog.yaml",
        "drafts/new_lesson/plan.md",
        "drafts/new_lesson/lesson.md",
        "drafts/new_lesson/exercises.yaml",
        "drafts/new_lesson/draft.yaml",
    }
    assert _git(workspace, "status", "--porcelain") == ""


def test_new_lesson_given_three_failures_expect_unchanged_curriculum_and_no_partial_draft(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {
        name: (workspace / name).read_bytes()
        for name in ("content/curriculum/plan.yaml", "content/catalog/approved/catalog.yaml")
    }
    client = FakeLlmClient.responding("invalid")
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with pytest.raises(NewLessonFailure) as error:
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert error.value.check.errors
    assert len(client.calls) == 3
    assert all((workspace / name).read_bytes() == data for name, data in before.items())
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "1"
    assert "new_lesson" not in {slot.catalog_id for slot in read_curriculum(workspace).slots}
    assert not (workspace / "drafts" / "new_lesson").exists()
    assert _git(workspace, "status", "--porcelain") == ""


def test_new_lesson_given_changed_curriculum_during_call_expect_no_overwrite_or_partial_draft(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = build_workbench_author_response(workspace)
    plan = workspace / "content" / "curriculum" / "plan.yaml"

    def respond(prompt: str, model: str | None) -> str:
        plan.write_text(plan.read_text() + "# concurrent edit\n")
        return response

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with pytest.raises(ValueError, match="changed during generation"):
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert plan.read_text().endswith("# concurrent edit\n")
    assert not (workspace / "drafts").exists()


@pytest.mark.parametrize("changed_file", ["curriculum/plan.yaml", "catalog/approved/catalog.yaml"])
def test_new_lesson_given_unparseable_concurrent_yaml_expect_curriculum_conflict_and_no_writes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, changed_file: str
) -> None:
    response = build_workbench_author_response(workspace)
    changed = workspace / "content" / changed_file
    paths = [workspace / "content" / name for name in ("curriculum/plan.yaml", "catalog/approved/catalog.yaml")]
    saved: dict[Path, bytes] = {}

    def respond(prompt: str, model: str | None) -> str:
        changed.write_text("entries: [\n")
        saved.update({path: path.read_bytes() for path in paths})
        return response

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with pytest.raises(ValueError, match="Curriculum changed during generation"):
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert all(path.read_bytes() == value for path, value in saved.items())
    assert not (workspace / "drafts").exists()
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "1"


def test_new_lesson_given_copied_exemplar_identity_expect_slot_identity_without_repairs(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"].update(prerequisites=["unknown_dependency"], kind="phraseology", title="Exemplar title")
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    result = create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert result["check"] == {"status": "passed", "errors": []}
    assert len(client.calls) == 1
    plan = yaml.safe_load((workspace / "drafts/new_lesson/plan.md").read_text().split("---")[1])
    assert (plan["prerequisites"], plan["kind"], plan["title"]) == ([], "communicative", "New title")


def test_new_lesson_given_plan_without_slot_identity_expect_identity_written_from_slot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    identity = ("lesson_id", "catalog_id", "kind", "title", "cefr_level", "learner_outcome", "approved", "cefr_tags")
    for key in identity:
        response["plan"].pop(key)
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    result = create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert result["check"] == {"status": "passed", "errors": []}
    assert len(client.calls) == 1
    plan = yaml.safe_load((workspace / "drafts/new_lesson/plan.md").read_text().split("---")[1])
    assert {key: plan[key] for key in identity} == {
        "lesson_id": "new_lesson",
        "catalog_id": "new_lesson",
        "kind": "communicative",
        "title": "New title",
        "cefr_level": "A1",
        "learner_outcome": "New outcome.",
        "approved": True,
        "cefr_tags": ["A1"],
    }


def test_new_lesson_given_lesson_header_disagreeing_with_slot_expect_header_written_from_slot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    header, body = parse_source_frontmatter(response["lesson_md"])
    header |= {"slug": "wrong", "title": "Wrong title", "cefr_level": "B2"}
    header.pop("goal")
    response["lesson_md"] = "---\n" + yaml.safe_dump(header, sort_keys=False) + "---" + body
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    result = create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert result["check"] == {"status": "passed", "errors": []}
    assert len(client.calls) == 1
    written, _ = parse_source_frontmatter((workspace / "drafts/new_lesson/lesson.md").read_text())
    assert {key: written[key] for key in ("slug", "title", "cefr_level", "goal")} == {
        "slug": "new_lesson",
        "title": "New title",
        "cefr_level": "A1",
        "goal": "New outcome.",
    }


@pytest.mark.parametrize("missing_marker", [False, True])
def test_new_lesson_given_colon_strings_and_missing_marker_expect_safe_yaml_and_complete_marker_bijection(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, missing_marker: bool
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"]["success_criteria"] = ["Use the -t-formen: kaldt, varmt, fint."]
    response["exercises"][0]["explanation_md"] = "Use the -t-formen: kaldt, varmt, fint."
    handle = response["exercises"][0]["handle"]
    if missing_marker:
        response["lesson_md"] = response["lesson_md"].replace("{{exercise: " + handle + "}}", "")
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    package = workspace / "drafts" / "new_lesson"
    compiled = compile_lesson_package(package)
    assert compiled.lesson is not None
    assert len(client.calls) == 1
    assert (package / "lesson.md").read_text().count("{{exercise: " + handle + "}}") == 1
    assert (
        yaml.safe_load((package / "exercises.yaml").read_text())[0]["explanation_md"]
        == response["exercises"][0]["explanation_md"]
    )


def test_new_lesson_given_two_invalid_candidates_expect_second_repair_with_exact_errors_then_one_commit(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    handle = response["exercises"][0]["handle"]
    duplicate = response | {"lesson_md": response["lesson_md"] + "\n{{exercise: " + handle + "}}\n"}
    responses = iter([json.dumps(duplicate), "invalid", json.dumps(response)])
    client = FakeLlmClient.from_fn(lambda prompt, model: next(responses))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    steps: list[str] = []
    create_workbench_lesson(workspace, _request(), threading.RLock(), steps.append)
    assert "repairing (1/2)" in steps and "repairing (2/2)" in steps
    assert "Exercise markers require repair" in client.calls[1][0]
    assert "Invalid JSON" in client.calls[2][0]
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "2"


def test_new_lesson_given_existing_wrapping_and_null_spelling_expect_minimal_curriculum_insertion(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = workspace / "content" / "curriculum" / "plan.yaml"
    catalog = workspace / "content" / "catalog" / "approved" / "catalog.yaml"
    plan.write_text(plan.read_text().replace("owner_scope: null", "owner_scope: null # retained"))
    catalog.write_text(
        catalog.read_text().replace(
            "- id: second",
            "- id: second\n  owner_scope: null # unchanged\n  title: A deliberately wrapped long title\n    that stays on two lines",
        )
    )
    plan.write_text(plan.read_text().replace("summary: {}", "summary:\n  approved_entries: 2"))
    catalog.write_text(
        catalog.read_text().replace(
            "summary: {}", "summary:\n  resulting_owners: 2\n  catalog_kind_counts:\n    communicative: 1"
        )
    )
    before_plan = plan.read_text()
    before_catalog = catalog.read_text()
    client = FakeLlmClient.responding(build_workbench_author_response(workspace))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    expected = rewrite_yaml_scalars(
        before_plan,
        {
            ("slots", 1, "sequence_index"): 2,
            ("summary", "approved_entries"): 3,
            ("source_catalog_hash",): hash_catalog_content(yaml.safe_load(catalog.read_text())),
        },
    )
    text = plan.read_text()
    start = text.index("- sequence_index: 1")
    end = text.index("- sequence_index: 2", start)
    assert text[:start] + text[end:] == expected
    text = catalog.read_text()
    start = text.index("- id: new_lesson")
    end = text.index("schema_version:", start)
    assert text[:start] + text[end:] == rewrite_yaml_scalars(
        before_catalog,
        {
            ("summary", "resulting_owners"): 3,
            ("summary", "catalog_kind_counts", "communicative"): 2,
        },
    )


def test_new_lesson_slot_given_unrelated_exemplar_expect_optional_family_and_no_seed_points(
    workspace: Path,
) -> None:
    slot = validate_new_lesson(workspace, _request())

    assert slot.family_id == ""
    assert slot.provisional_flags == [
        "Unassigned is an approvable family choice. You may select a family later by learner outcome from catalog_families.yaml in the catalog registry."
    ]
    assert slot.teaching_points == []


def test_new_lesson_given_generated_package_expect_consistent_teaching_points_and_family_placeholder(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"]["teaching_points"] = [
        {"id": "new_lesson:greeting", "source_owner": "new_lesson", "statement": "Choose a greeting for the setting."},
        {"id": "new_lesson:name", "source_owner": "new_lesson", "statement": "Introduce yourself using Jeg heter."},
    ]
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)

    slot = next(slot for slot in read_curriculum(workspace).slots if slot.catalog_id == "new_lesson")
    catalog = yaml.safe_load((workspace / "content/catalog/approved/catalog.yaml").read_text())
    entry = next(entry for entry in catalog["entries"] if entry["id"] == "new_lesson")
    plan_text = (workspace / "drafts/new_lesson/plan.md").read_text()
    plan = yaml.safe_load(plan_text.split("---", 2)[1])
    assert entry["teaching_points"] == plan["teaching_points"] == [point.model_dump() for point in slot.teaching_points]
    assert entry["teaching_points"] == response["plan"]["teaching_points"]
    assert entry["family_id"] == plan["family_id"] == slot.family_id == ""
    assert "Unassigned is an approvable family choice" in plan_text
    assert "before approving" not in plan_text
    assert "catalog_families.yaml" in plan_text


@pytest.mark.parametrize(
    ("level", "sections", "words", "exercises"),
    [
        ("A1", "11-14", "2167-2852", "10-16"),
        ("A2", "11-15", "2217-2657", "12-16"),
        ("B1", "10-15", "2350-2874", "10-16"),
        ("B2", "10-15", "2381-3254", "10-19"),
    ],
)
def test_new_lesson_prompt_given_cefr_level_expect_canonical_size_guidance(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, level: str, sections: str, words: str, exercises: str
) -> None:
    plan_path = workspace / "content/curriculum/plan.yaml"
    plan_path.write_text(plan_path.read_text().replace("cefr_level: A1", f"cefr_level: {level}"))
    client = FakeLlmClient.responding("invalid")
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    request = _request().model_copy(update={"cefr_level": level})

    with pytest.raises(NewLessonFailure):
        create_workbench_lesson(workspace, request, threading.RLock(), lambda step: None)

    prompt = client.calls[0][0]
    assert f"{sections} sections" in prompt
    assert f"{words} source words" in prompt
    assert f"{exercises} exercises" in prompt
    assert "without a length or section-count target" not in prompt
    assert "Do not pad" in prompt


@pytest.mark.parametrize(
    "points", [[], [{"id": "new_lesson:outcome", "source_owner": "new_lesson", "statement": "  New outcome.  "}]]
)
def test_new_lesson_given_empty_or_outcome_only_points_expect_repair_and_no_draft(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, points: list[dict[str, str]]
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"]["teaching_points"] = points
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    with pytest.raises(NewLessonFailure) as error:
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)

    assert any("teaching_points" in message for message in error.value.check.errors)
    assert len(client.calls) == 3
    assert "2–5 concrete teaching points" in client.calls[0][0]
    assert "new_lesson:learner_outcome" not in client.calls[0][0]
    assert "new_lesson:learner_outcome" in client.calls[1][0]
    assert "repair fallback" in client.calls[1][0]
    assert not (workspace / "drafts/new_lesson").exists()


@pytest.mark.parametrize("statement", ["New outcome.", "  New outcome.  "])
def test_author_response_given_single_outcome_copy_expect_validation_error(workspace: Path, statement: str) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"]["teaching_points"] = [
        {"id": "new_lesson:learner_outcome", "source_owner": "new_lesson", "statement": statement}
    ]

    with pytest.raises(ValueError, match="teaching_points"):
        AuthoredLessonResponse.model_validate(response)


def _request() -> NewLessonRequest:
    return NewLessonRequest(
        slug="new_lesson",
        title="New title",
        cefr_level="A1",
        learner_outcome="New outcome.",
        after_lesson_id="greet_and_introduce_yourself",
    )


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout


def test_new_lesson_given_server_owned_plan_keys_expect_valid_draft_without_repairs(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"].update(
        sequence_index=900,
        catalog_kind="wrong",
        cefr_source="wrong",
        provisional_flags=["wrong"],
        owner_scope={},
        human_gate="wrong",
        bloom_targets=["wrong"],
        type="Plan",
    )
    for objective in response["plan"]["objectives"]:
        objective["bloom_targets"] = ["wrong"]
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    result = create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert result["check"] == {"status": "passed", "errors": []}
    assert len(client.calls) == 1
    plan = (workspace / "drafts/new_lesson/plan.md").read_text()
    assert not {
        "sequence_index",
        "catalog_kind",
        "cefr_source",
        "provisional_flags",
        "owner_scope",
        "human_gate",
        "bloom_targets",
        "type",
    }.intersection(yaml.safe_load(plan.split("---")[1]))


@pytest.mark.parametrize("invalid", ["unknown_field", "objectives"])
def test_new_lesson_given_invalid_authored_plan_expect_concrete_errors_in_repairs_and_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    response = json.loads(build_workbench_author_response(workspace))
    response["plan"]["type"] = "Plan"
    response["plan"][invalid] = []
    client = FakeLlmClient.responding(json.dumps(response))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with pytest.raises(NewLessonFailure) as error:
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert f"plan.{invalid}" in client.calls[1][0]
    assert f"plan.{invalid}" in str(error.value)
    assert not (workspace / "drafts").exists()


def test_new_lesson_given_concurrent_insertions_expect_generated_points_in_catalog_slots_and_plans(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)
    response = build_workbench_author_response(workspace)

    def respond(prompt: str, model: str | None) -> str:
        barrier.wait(timeout=30)
        return response.replace("new_lesson", "other_lesson") if '"catalog_id":"other_lesson"' in prompt else response

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    lock = threading.RLock()
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [
            pool.submit(create_workbench_lesson, workspace, request, lock, lambda step: None)
            for request in (_request(), _request().model_copy(update={"slug": "other_lesson"}))
        ]
        results = [job.result(timeout=60) for job in jobs]
    assert {result["lesson_id"] for result in results} == {"new_lesson", "other_lesson"}
    assert check_curriculum(workspace) == ()
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "3"
    assert _git(workspace, "status", "--porcelain") == ""
    catalog = yaml.safe_load((workspace / "content/catalog/approved/catalog.yaml").read_text())
    slots = {slot.catalog_id: slot for slot in read_curriculum(workspace).slots}
    entries = {entry["id"]: entry for entry in catalog["entries"]}
    for slug in ("new_lesson", "other_lesson"):
        assert compile_lesson_package(workspace / "drafts" / slug).lesson is not None
        plan = yaml.safe_load((workspace / "drafts" / slug / "plan.md").read_text().split("---", 2)[1])
        expected = json.loads(response.replace("new_lesson", slug))["plan"]["teaching_points"]
        assert expected
        assert entries[slug]["teaching_points"] == plan["teaching_points"] == expected
        assert [point.model_dump() for point in slots[slug].teaching_points] == expected


@pytest.mark.parametrize("conflict", ["reorder", "anchor_removed", "slug_taken", "commit_failure"])
def test_new_lesson_given_intervening_insertion_and_conflict_expect_current_source_preserved(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    from lesson_builder.workspace.workbench_git import WorkbenchGitError

    response = build_workbench_author_response(workspace)
    plan = workspace / "content/curriculum/plan.yaml"
    catalog = workspace / "content/catalog/approved/catalog.yaml"
    lock = threading.RLock()
    saved: dict[Path, bytes] = {}
    nested = False
    saved_slug = "new_lesson" if conflict == "slug_taken" else "other_lesson"

    def respond(prompt: str, model: str | None) -> str:
        nonlocal nested
        if nested:
            return response.replace("new_lesson", saved_slug)
        nested = True
        request = _request().model_copy(update={"slug": saved_slug})
        create_workbench_lesson(workspace, request, lock, lambda step: None)
        document = yaml.safe_load(plan.read_bytes())
        if conflict == "reorder":
            document["slots"][0]["sequence_index"], document["slots"][-1]["sequence_index"] = (
                document["slots"][-1]["sequence_index"],
                document["slots"][0]["sequence_index"],
            )
            plan.write_text(yaml.safe_dump(document, sort_keys=False))
        elif conflict == "anchor_removed":
            document["slots"] = document["slots"][1:]
            plan.write_text(yaml.safe_dump(document, sort_keys=False))
        elif conflict == "commit_failure":

            def fail_commit(*args, **kwargs):
                raise WorkbenchGitError("simulated commit failure")

            monkeypatch.setattr("lesson_builder.application.operations.workbench_new_lesson.commit_draft", fail_commit)
        saved.update({path: path.read_bytes() for path in (plan, catalog)})
        return response

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    expected_error = WorkbenchGitError if conflict == "commit_failure" else ValueError
    message = {
        "commit_failure": "simulated commit failure",
        "slug_taken": "already exists",
    }.get(conflict, "changed during generation")
    with pytest.raises(expected_error, match=message):
        create_workbench_lesson(workspace, _request(), lock, lambda step: None)
    assert all(path.read_bytes() == value for path, value in saved.items())
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "2"
    assert (workspace / "drafts" / saved_slug / "lesson.md").exists()
    if conflict != "slug_taken":
        assert not (workspace / "drafts/new_lesson").exists()


def test_new_lesson_given_intervening_manual_insertion_expect_entry_and_generated_draft_saved(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lesson_builder.application.operations.workbench_curriculum import save_workbench_curriculum_edits
    from lesson_builder.domain.curriculum.models import CurriculumAddEdit

    response = build_workbench_author_response(workspace)

    def respond(prompt: str, model: str | None) -> str:
        edit = CurriculumAddEdit.model_validate(
            {
                "kind": "add",
                "entry": {
                    "catalog_id": "manual_entry",
                    "title": "Manual entry",
                    "learner_outcome": "A manual outcome.",
                },
                "after_lesson_id": "greet_and_introduce_yourself",
            }
        )
        save_workbench_curriculum_edits(workspace, [edit], force=True)
        return response

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)
    assert [slot.catalog_id for slot in read_curriculum(workspace).slots] == [
        "greet_and_introduce_yourself",
        "new_lesson",
        "manual_entry",
        "second",
    ]
    assert compile_lesson_package(workspace / "drafts/new_lesson").lesson is not None
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "3"
    assert _git(workspace, "status", "--porcelain", "--", "content", "drafts") == ""


@pytest.mark.parametrize("invalid", ["one_point", "six_points", "outcome_copy"])
def test_new_lesson_given_invalid_point_scope_expect_repaired_points_saved(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    valid = build_workbench_author_response(workspace)
    response = json.loads(valid)
    points = response["plan"]["teaching_points"]
    if invalid == "one_point":
        response["plan"]["teaching_points"] = points[:1]
    elif invalid == "six_points":
        response["plan"]["teaching_points"] = [
            points[index % 2] | {"id": f"new_lesson:point{index}"} for index in range(6)
        ]
    else:
        response["plan"]["teaching_points"] = [points[0] | {"statement": "  New outcome.  "}]
    responses = iter([json.dumps(response), valid])
    client = FakeLlmClient.from_fn(lambda prompt, model: next(responses))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)

    assert len(client.calls) == 2
    assert "teaching_points" in client.calls[1][0]
    slot = next(slot for slot in read_curriculum(workspace).slots if slot.catalog_id == "new_lesson")
    assert [point.model_dump() for point in slot.teaching_points] == json.loads(valid)["plan"]["teaching_points"]


def test_new_lesson_given_serialized_points_differ_from_generated_scope_expect_repair_and_no_writes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lesson_builder.formats.workbench_source import normalize_source

    def corrupt_points(text: str) -> str:
        return normalize_source(text).replace("Choose a greeting for the setting.", "Use an unrelated teaching scope.")

    client = FakeLlmClient.responding(build_workbench_author_response(workspace))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    monkeypatch.setattr("lesson_builder.application.operations.workbench_new_lesson.normalize_source", corrupt_points)

    with pytest.raises(NewLessonFailure) as error:
        create_workbench_lesson(workspace, _request(), threading.RLock(), lambda step: None)

    assert any("plan.md teaching_points must match" in message for message in error.value.check.errors)
    assert len(client.calls) == 3
    assert not (workspace / "drafts/new_lesson").exists()
    assert _git(workspace, "status", "--porcelain") == ""
