"""Entry points: `validate_new_lesson` and `create_workbench_lesson` for guarded curriculum jobs."""

from __future__ import annotations

import json
import shutil
from _thread import RLock
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from lesson_builder.application.operations.compile_lesson_package import collect_lesson_package
from lesson_builder.application.operations.lesson_package_paths import resolve_contained_lesson_package
from lesson_builder.application.operations.load_characters import load_optional_character_registry
from lesson_builder.application.operations.load_exercises import load_exercise_items
from lesson_builder.application.operations.load_terminology import load_terminology_bans
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_author_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_rich_draft_prompt
from lesson_builder.application.operations.workbench_curriculum import check_curriculum
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_new_lesson_records import AuthoredLessonResponse
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_settings_operations import workbench_runner_for_role
from lesson_builder.application.settings import K_APPLICATION_NEW_LESSON_FAMILY_PLACEHOLDER
from lesson_builder.application.settings import K_APPLICATION_NEW_LESSON_PACKAGE_VERSION
from lesson_builder.application.settings import K_APPLICATION_NEW_LESSON_SIZE_TARGETS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_DRAFT_REPAIRS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_FILES
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from lesson_builder.domain.curriculum.models import CatalogTeachingPoint
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.domain.curriculum.services.plan_policy import hash_catalog_content
from lesson_builder.domain.distribution.services.distribution_service import DistributionService
from lesson_builder.domain.lesson.models.characters import CharacterRegistry
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.formats.settings import K_FORMATS_EXERCISE
from lesson_builder.formats.workbench_curriculum import insert_yaml_sequence_item
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_scalars
from lesson_builder.formats.workbench_source import normalize_source
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import require_workbench_commit


class NewLessonRequest(BaseModel):
    """Creation fields with an explicit insertion anchor; null means the beginning."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    learner_outcome: str = Field(min_length=1)
    cefr_level: str = Field(pattern=r"^(A1|A2|B1|B2)$")
    slug: str = Field(min_length=1)
    after_lesson_id: str | None = None


class NewLessonFailure(ValueError):
    """Generation failed after two repairs, with the last deterministic errors."""

    def __init__(self, check: ProposalCheck) -> None:
        lines = check.errors[0].splitlines() if check.errors else ["The draft did not pass validation."]
        reason = lines[0]
        match lines:
            case [header, location, message, *_] if "validation error" in header:
                reason = f"{location}: {message.strip().split(' [type=')[0]}"
        super().__init__(
            f"Lesson generation failed after two repairs: {reason[:240]}. No curriculum changes or draft were saved."
        )
        self.check = check


def validate_new_lesson(root: Path, request: NewLessonRequest) -> CurriculumPlanSlot:
    """Validate slug uniqueness, contained writable targets, anchor and available exemplar."""
    validate_slug(request.slug)
    paths = WorkspacePaths(root)
    for target in (paths.curriculum_plan, paths.catalog_file, paths.drafts_root, paths.lessons_root):
        if any(parent.is_symlink() for parent in (target, *target.parents)):
            raise ValueError("new lesson path escapes workspace")
    expected_paths = tuple(path.relative_to(root).as_posix() for path in (paths.curriculum_plan, paths.catalog_file))
    expected_paths += tuple(
        (paths.drafts_root / request.slug / name).relative_to(root).as_posix()
        for name in (*K_APPLICATION_WORKBENCH_FILES, "draft.yaml")
    )
    require_workbench_commit(root, expected_paths)
    plan = read_curriculum(root)
    catalog = yaml.safe_load(paths.catalog_file.read_bytes())
    if (
        request.slug in {slot.catalog_id for slot in plan.slots}
        or any(entry["id"] == request.slug for entry in catalog["entries"])
        or (paths.drafts_root / request.slug).exists()
        or (paths.lessons_root / request.slug).exists()
        or (paths.drafts_root / request.slug).is_symlink()
        or (paths.lessons_root / request.slug).is_symlink()
    ):
        raise ValueError("lesson slug already exists")
    if request.after_lesson_id is not None and request.after_lesson_id not in {slot.catalog_id for slot in plan.slots}:
        raise ValueError("insertion entry not found")
    exemplar = _select_exemplar(root, plan, request)
    ordered = sorted(plan.slots, key=lambda slot: slot.sequence_index)
    position = next((index + 1 for index, slot in enumerate(ordered) if slot.catalog_id == request.after_lesson_id), 0)
    return CurriculumPlanSlot(
        sequence_index=position,
        catalog_id=request.slug,
        catalog_kind=exemplar.catalog_kind,
        family_id="",
        provisional_flags=[K_APPLICATION_NEW_LESSON_FAMILY_PLACEHOLDER],
        title=request.title,
        learner_outcome=request.learner_outcome,
        cefr_level=request.cefr_level,
        cefr_source="planner_default",
        cefr_tags=[request.cefr_level],
    )


def create_workbench_lesson(
    root: Path, request: NewLessonRequest, lock: RLock, progress: Callable[[str], None]
) -> dict[str, object]:
    """Generate and check in isolation, then commit curriculum, catalog and complete draft together."""
    progress("adding to curriculum")
    with lock:
        slot = validate_new_lesson(root, request)
        paths = WorkspacePaths(root)
        captured = {path: path.read_bytes() for path in (paths.curriculum_plan, paths.catalog_file)}
        exemplar_slot = _select_exemplar(root, read_curriculum(root), request)
        exemplar = resolve_contained_lesson_package(paths.lessons_root, paths.lessons_root / exemplar_slot.catalog_id)
        files = {name: (exemplar / name).read_text(encoding="utf-8") for name in K_APPLICATION_WORKBENCH_FILES}
        prompt = _build_author_prompt(slot, files)
        characters = load_optional_character_registry(root, search_parents=False)
        if characters is not None:
            prompt += (
                "\nRegistered characters (use only these character_id values, or omit character_id for new speakers):\n"
                + json.dumps(asdict(characters), ensure_ascii=False)
            )
        terminology_bans = (
            load_terminology_bans(paths.terminology_registry) if paths.terminology_registry.exists() else None
        )
    if terminology_bans is not None:
        prompt += "\nAuthoring terminology bans:\n" + terminology_bans.model_dump_json()
    progress("drafting lesson")
    runner = workbench_runner_for_role(root, "generation")
    instruction = build_structured_json_prompt(prompt, AuthoredLessonResponse)
    check = ProposalCheck(status="failed", errors=["No author response"])
    with TemporaryDirectory(prefix="workbench-author-") as temporary:
        candidate = Path(temporary) / request.slug
        candidate.mkdir()
        for attempt in range(K_APPLICATION_WORKBENCH_DRAFT_REPAIRS + 1):
            raw = runner.invoke(instruction)
            try:
                authored = AuthoredLessonResponse.model_validate_json(raw)
                slot = slot.model_copy(
                    update={
                        "teaching_points": [
                            CatalogTeachingPoint.model_validate(point.model_dump())
                            for point in authored.plan.teaching_points
                        ]
                    }
                )
                for name, text in _authored_files(authored, slot).items():
                    (candidate / name).write_text(text, encoding="utf-8")
                progress("checking")
                check = _check_candidate(candidate, slot, terminology_bans, characters)
            except (TypeError, ValueError, yaml.YAMLError) as exc:
                check = ProposalCheck(status="failed", errors=[str(exc)])
            if check.status == "passed":
                break
            if attempt < K_APPLICATION_WORKBENCH_DRAFT_REPAIRS:
                progress(f"repairing ({attempt + 1}/{K_APPLICATION_WORKBENCH_DRAFT_REPAIRS})")
                instruction = (
                    build_structured_json_prompt(prompt, AuthoredLessonResponse)
                    + "\nIf teaching points need repair, expand this provisional outcome-copy repair fallback "
                    "into 2–5 concrete teaching points; never return it verbatim:\n"
                    + json.dumps(
                        [
                            {
                                "id": f"{slot.catalog_id}:learner_outcome",
                                "source_owner": slot.catalog_id,
                                "statement": slot.learner_outcome,
                            }
                        ]
                    )
                    + "\nRepair the COMPLETE package using the exact errors below. Last candidate:\n"
                    + raw
                    + "\nCheck errors:\n"
                    + check.model_dump_json()
                )
        if check.status == "failed":
            raise NewLessonFailure(check)
        catalog_text, plan_text = _add_curriculum_records(captured, paths, request, slot)
        with lock:
            commit = _commit_new_lesson(root, request, captured, catalog_text, plan_text, candidate, slot)
            findings = check_curriculum(root)
        progress("done")
        return {
            "lesson_id": request.slug,
            "commit": commit,
            "check": check.model_dump(mode="json"),
            "findings": [vars(finding) for finding in findings]
            + collect_lesson_package(candidate, terminology_bans=terminology_bans).audit.model_dump(mode="json")[
                "findings"
            ],
        }


def _commit_new_lesson(
    root: Path,
    request: NewLessonRequest,
    captured: dict[Path, bytes],
    catalog_text: str,
    plan_text: str,
    candidate: Path,
    generated_slot: CurriculumPlanSlot,
) -> str:
    paths = WorkspacePaths(root)
    current = {path: path.read_bytes() for path in captured}
    if current != captured:
        if not _has_only_curriculum_insertions(captured, current, paths):
            raise ValueError(
                "Curriculum changed during generation. No files were written; retry with the current curriculum."
            )
        slot = validate_new_lesson(root, request).model_copy(update={"teaching_points": generated_slot.teaching_points})
        catalog_text, plan_text = _add_curriculum_records(current, paths, request, slot)
        captured = current
    else:
        validate_new_lesson(root, request)
    write_paths = tuple(path.relative_to(root).as_posix() for path in captured)
    draft = paths.drafts_root / request.slug
    try:
        write_text_atomically(paths.curriculum_plan, plan_text)
        write_text_atomically(paths.catalog_file, catalog_text)
        draft.mkdir(parents=True)
        for name in K_APPLICATION_WORKBENCH_FILES:
            shutil.copyfile(candidate / name, draft / name)
        write_text_atomically(
            draft / "draft.yaml",
            yaml.safe_dump(
                {
                    "lesson_id": request.slug,
                    "started_from": None,
                    "started_from_sha256": None,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                sort_keys=False,
            ),
        )
        write_paths += tuple(
            (draft / name).relative_to(root).as_posix() for name in (*K_APPLICATION_WORKBENCH_FILES, "draft.yaml")
        )
        commit = commit_draft(root, request.slug, write_paths, message=f"workbench: new lesson {request.slug}")
    except Exception:
        for path, before in captured.items():
            write_text_atomically(path, before.decode("utf-8"))
        if draft.exists():
            shutil.rmtree(draft)
        raise
    return commit


def _has_only_curriculum_insertions(
    before: dict[Path, bytes], current: dict[Path, bytes], paths: WorkspacePaths
) -> bool:
    has_additions = False
    for path, collection, identity, counters in (
        (paths.curriculum_plan, "slots", "catalog_id", ("approved_entries",)),
        (paths.catalog_file, "entries", "id", ("resulting_owners", "catalog_kind_counts")),
    ):
        try:
            old = yaml.safe_load(before[path])
            new = yaml.safe_load(current[path])
        except yaml.YAMLError:
            return False
        old_items = old.pop(collection)
        new_items = new.pop(collection)
        if not _has_preserved_curriculum_items(old_items, new_items, identity, collection == "slots"):
            return False
        old_metadata = _curriculum_metadata_without_derived_fields(old, collection, counters)
        new_metadata = _curriculum_metadata_without_derived_fields(new, collection, counters)
        if old_metadata != new_metadata:
            return False
        has_additions |= len(new_items) > len(old_items)
    return has_additions


def _has_preserved_curriculum_items(
    before: list[dict[str, Any]], current: list[dict[str, Any]], identity: str, is_plan: bool
) -> bool:
    old_ids = {item[identity] for item in before}
    new_ids = {item[identity] for item in current}
    if not old_ids <= new_ids or len(new_ids) != len(current):
        return False
    surviving = [item for item in current if item[identity] in old_ids]
    if is_plan:
        if not _has_preserved_slot_order(before, surviving, current):
            return False
        before = [item.copy() for item in before]
        surviving = [item.copy() for item in surviving]
        for item in before + surviving:
            item.pop("sequence_index", None)
    return before == surviving


def _has_preserved_slot_order(
    before: list[dict[str, Any]], surviving: list[dict[str, Any]], current: list[dict[str, Any]]
) -> bool:
    old_order = [item["catalog_id"] for item in sorted(before, key=lambda item: item["sequence_index"])]
    new_order = [item["catalog_id"] for item in sorted(surviving, key=lambda item: item["sequence_index"])]
    indices = [item["sequence_index"] for item in current]
    return old_order == new_order and len(set(indices)) == len(indices)


def _curriculum_metadata_without_derived_fields(
    document: dict[str, Any], collection: str, counters: tuple[str, ...]
) -> dict[str, Any]:
    document = document.copy()
    if "summary" in document:
        document["summary"] = document["summary"].copy()
    if collection == "slots":
        document.pop("source_catalog_hash", None)
    for counter in counters:
        document.get("summary", {}).pop(counter, None)
    return document


def _select_exemplar(root: Path, plan: CurriculumPlan, request: NewLessonRequest) -> CurriculumPlanSlot:
    candidates = [
        slot
        for slot in plan.slots
        if slot.cefr_level == request.cefr_level
        and all(
            (WorkspacePaths(root).lessons_root / slot.catalog_id / name).is_file()
            for name in K_APPLICATION_WORKBENCH_FILES
        )
    ]
    if not candidates:
        raise ValueError(f"No complete existing {request.cefr_level} lesson is available as a format exemplar")
    anchor = next((slot for slot in plan.slots if slot.catalog_id == request.after_lesson_id), None)
    return next((slot for slot in candidates if anchor and slot.catalog_kind == anchor.catalog_kind), candidates[0])


def _add_curriculum_records(
    captured: dict[Path, bytes], paths: WorkspacePaths, request: NewLessonRequest, slot: CurriculumPlanSlot
) -> tuple[str, str]:
    catalog_entry = {
        "id": slot.catalog_id,
        "status": "approved",
        "catalog_kind": slot.catalog_kind,
        "family_id": slot.family_id,
        "title": slot.title,
        "learner_outcome": slot.learner_outcome,
        "cefr_tags": slot.cefr_tags,
        "cefr_source": "planner_default",
        "relationship": "new",
        "source_owners": [],
        "teaching_points": [point.model_dump(mode="json") for point in slot.teaching_points],
        "prerequisites": [],
        "helpful_prerequisites": [],
    }
    catalog = captured[paths.catalog_file].decode("utf-8")
    document = yaml.safe_load(catalog)
    updates: dict[tuple[str | int, ...], str | int] = {}
    summary = document.get("summary", {})
    if "resulting_owners" in summary:
        updates[("summary", "resulting_owners")] = summary["resulting_owners"] + 1
    counts = summary.get("catalog_kind_counts", {})
    if slot.catalog_kind in counts:
        updates[("summary", "catalog_kind_counts", slot.catalog_kind)] = counts[slot.catalog_kind] + 1
    catalog = rewrite_yaml_scalars(catalog, updates)
    catalog = insert_yaml_sequence_item(catalog, "entries", catalog_entry, len(document["entries"]))
    plan = captured[paths.curriculum_plan].decode("utf-8")
    document = yaml.safe_load(plan)
    updates = {
        ("slots", index, "sequence_index"): item["sequence_index"] + 1
        for index, item in enumerate(document["slots"])
        if item["sequence_index"] >= slot.sequence_index
    }
    updates[("source_catalog_hash",)] = hash_catalog_content(yaml.safe_load(catalog))
    if "approved_entries" in document.get("summary", {}):
        updates[("summary", "approved_entries")] = len(document["slots"]) + 1
    plan = rewrite_yaml_scalars(plan, updates)
    position = next(
        (index for index, item in enumerate(document["slots"]) if item["sequence_index"] == slot.sequence_index),
        len(document["slots"]),
    )
    plan = insert_yaml_sequence_item(plan, "slots", slot.model_dump(mode="json"), position)
    CurriculumPlan.model_validate(yaml.safe_load(plan))
    return catalog, plan


def _build_author_prompt(slot: CurriculumPlanSlot, exemplar: dict[str, str]) -> str:
    contract = slot.model_dump_json()
    draft_rules = build_rich_draft_prompt(
        contract,
        plan_kind=slot.catalog_kind,
        size_guidance=(
            f"For {slot.cefr_level}, aim for {K_APPLICATION_NEW_LESSON_SIZE_TARGETS[slot.cefr_level]}. "
            "These are canonical per-level interquartile ranges, not quotas or schema limits. "
            "Source words count whitespace-separated tokens in the lesson Markdown body, including source markup. "
            "Do not pad, split cohesive exercises, or add an outcome to reach a count. "
            "Use distinct contexts, useful contrasts, misconception corrections, focused practice, and final transfer; "
            "a narrow outcome may justify a shorter chapter."
        ),
    )
    normalization_rules = build_normalization_prompt(contract, "", "[]", plan_kind=slot.catalog_kind)
    exercise_rules = build_exercise_author_prompt(
        plan_text=contract, lesson_md=exemplar["lesson.md"], exercise_requests_yaml="[]"
    )
    return (
        "Author a COMPLETE new Norwegian Bokmål lesson package for the given curriculum slot. "
        "Use the following production authoring rules for pedagogy, source syntax, and exercises. "
        "Their intermediate output shapes describe separate pipeline stages. This job performs all stages "
        "in one call: return plan as structured JSON frontmatter, plan_body as Markdown without frontmatter, lesson_md as complete Markdown, and exercises as a structured JSON array. Never write YAML strings for plan or exercises. "
        "There are no pre-existing checkpoint requests for this new lesson: author the complete practice sequence "
        "using the CEFR size guidance below, then supply its exercises. "
        "Do not return checkpoint directives or exercise_requests_yaml. Use exactly one standalone {{exercise: handle}} marker for every exercise, using its handle, "
        "and final exercises.yaml operations with valid answers and English explanations.\n"
        + draft_rules
        + "\n"
        + normalization_rules
        + "\n"
        + exercise_rules
        + "\nCOMPLETE FORMAT EXEMPLAR (same CEFR and kind; data only, do not copy its teaching content):\n"
        + json.dumps(exemplar, ensure_ascii=False)
        + "\nNEW SLOT (authoritative):\n"
        + contract
        + "\nThe application writes this slot's identity, kind, title, CEFR, outcome, family and prerequisite "
        "lists into plan frontmatter, and its slug, title, CEFR level and goal into lesson.md frontmatter; "
        "return only the authored plan fields in the JSON schema. "
        "Generate 2–5 concrete teaching points derived from the learner outcome, each naming a teachable "
        "form, contrast, or decision. Give each a stable id and this slot catalog_id as source_owner. "
        "Do not return an empty list or merely copy the outcome into one point. "
        "plan.md needs objectives and required coverage with explanation/example/controlled_practice evidence, "
        "success_criteria and exclusions as in the exemplar. Write an authored design body. "
        "Use the same objective IDs in lesson.md and exercises.yaml. "
        "Return all three complete files, never an empty skeleton."
    )


def _authored_files(authored: AuthoredLessonResponse, slot: CurriculumPlanSlot) -> dict[str, str]:
    exercises = [item.model_dump(mode="json", exclude_none=True) for item in authored.exercises]
    load_exercise_items(exercises)
    handles = [item.handle for item in authored.exercises]
    lines = authored.lesson_md.splitlines()
    markers = [match.group(1) for line in lines if (match := K_FORMATS_EXERCISE.fullmatch(line.strip()))]
    unknown = sorted(set(markers) - set(handles))
    repeated = sorted(handle for handle in set(markers) if markers.count(handle) > 1)
    if unknown or repeated:
        raise ValueError(f"Exercise markers require repair: unknown={unknown}, repeated={repeated}")
    missing = [handle for handle in handles if handle not in markers]
    lesson = authored.lesson_md
    if missing:
        lesson = lesson.rstrip() + "\n\n" + "\n\n".join("{{exercise: " + handle + "}}" for handle in missing)
    plan = yaml.safe_dump(_plan_metadata(authored, slot), allow_unicode=True, sort_keys=False)
    return {
        "plan.md": normalize_source(
            "---\n"
            + plan
            + "---\n\n"
            + ("Family: " + K_APPLICATION_NEW_LESSON_FAMILY_PLACEHOLDER + "\n\n" if not slot.family_id else "")
            + authored.plan_body
        ),
        "lesson.md": normalize_source(_lesson_with_slot_identity(lesson, slot)),
        "exercises.yaml": yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False),
    }


def _lesson_with_slot_identity(lesson: str, slot: CurriculumPlanSlot) -> str:
    """Write the lesson header's identity from the curriculum slot, keeping the authored header fields."""
    header, body = parse_source_frontmatter(lesson)
    identity = {
        "type": "Lesson",
        "slug": slot.catalog_id,
        "title": slot.title,
        "cefr_level": slot.cefr_level,
        "goal": slot.learner_outcome,
    }
    dumped = yaml.safe_dump(identity | header | identity, allow_unicode=True, sort_keys=False)
    return f"---\n{dumped}---{body}"


def _plan_metadata(authored: AuthoredLessonResponse, slot: CurriculumPlanSlot) -> dict[str, Any]:
    """Write slot identity from the curriculum and only the authored fields from the provider."""
    fields = authored.plan.model_dump(mode="json", exclude_none=True)
    return {
        "package_version": K_APPLICATION_NEW_LESSON_PACKAGE_VERSION,
        "lesson_id": slot.catalog_id,
        "kind": slot.catalog_kind,
        "title": slot.title,
        "cefr_level": slot.cefr_level,
        "cefr_target_basis": slot.cefr_source,
        "approved": True,
        "catalog_id": slot.catalog_id,
        "learner_outcome": slot.learner_outcome,
        "family_id": slot.family_id,
        "cefr_tags": slot.cefr_tags,
        "teaching_points": fields["teaching_points"],
        "terminology_ids": fields["terminology_ids"],
        "prerequisites": slot.prerequisites,
        "helpful_prerequisites": slot.helpful_prerequisites,
        "objectives": fields["objectives"],
        "coverage": fields["coverage"],
        "success_criteria": fields["success_criteria"],
        "exclusions": fields["exclusions"],
    }


def _check_candidate(
    candidate: Path,
    slot: CurriculumPlanSlot,
    terminology_bans: TerminologyBans | None,
    characters: CharacterRegistry | None,
) -> ProposalCheck:
    compiled = collect_lesson_package(candidate, terminology_bans=terminology_bans)
    errors = list(compiled.errors)
    errors.extend(f"{finding.code}: {finding.explanation}" for finding in compiled.audit.material_findings)
    errors.extend(result.message for result in compiled.quality_results if result.is_blocking)
    plan, _ = parse_source_frontmatter((candidate / "plan.md").read_text(encoding="utf-8"))
    for key, expected in {
        "lesson_id": slot.catalog_id,
        "catalog_id": slot.catalog_id,
        "kind": slot.catalog_kind,
        "title": slot.title,
        "cefr_level": slot.cefr_level,
        "learner_outcome": slot.learner_outcome,
        "family_id": slot.family_id,
        "cefr_tags": slot.cefr_tags,
        "teaching_points": [point.model_dump(mode="json") for point in slot.teaching_points],
        "prerequisites": slot.prerequisites,
        "helpful_prerequisites": slot.helpful_prerequisites,
    }.items():
        if plan.get(key) != expected:
            errors.append(f"plan.md {key} must match the new curriculum slot: {expected}")
    if compiled.lesson is None:
        return ProposalCheck(status="failed", errors=list(dict.fromkeys(errors)))
    if not any(element.element_kind == "exercise" for element in compiled.lesson.elements):
        errors.append("A complete lesson requires authored exercises, not an empty skeleton")
    try:
        DistributionService().create_lesson_packet(
            compiled.lesson,
            kind=slot.catalog_kind,
            character_registry=characters,
            allow_unregistered_characters=characters is None,
        )
    except (TypeError, ValueError) as exc:
        errors.append(f"Lesson packet preview: {exc}")
    for key, expected in {
        "key": slot.catalog_id,
        "title": slot.title,
        "cefr_level": slot.cefr_level,
        "goal": slot.learner_outcome,
    }.items():
        if getattr(compiled.lesson, key) != expected:
            errors.append(f"lesson.md {key} must match the new curriculum slot: {expected}")
    return ProposalCheck(status="failed" if errors else "passed", errors=list(dict.fromkeys(errors)))
