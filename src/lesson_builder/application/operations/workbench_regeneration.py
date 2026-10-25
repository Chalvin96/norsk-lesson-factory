"""Entry points: `capture_regeneration`, `generate_regeneration`, `generate_regeneration_item`, `assemble_regeneration`, `check_regeneration`, and `review_regeneration` for workflow nodes."""

from __future__ import annotations

import json
import shutil
from _thread import RLock
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from typing import Literal

import yaml

from lesson_builder.application.operations.workbench_chat import build_chat_prompt
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseStale
from lesson_builder.application.operations.workbench_exercise_alignment import restore_pin
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_proposals import check_proposal
from lesson_builder.application.operations.workbench_proposals import create_proposal
from lesson_builder.application.operations.workbench_proposals import proposal_files
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPin
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import ProposedEdit
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.application.operations.workbench_review import select_review_blocks
from lesson_builder.application.operations.workbench_settings_operations import workbench_runner_for_role
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_transaction import contained_workbench_path

K_WORKBENCH_REGENERATION_NON_TEXT_FIELDS = frozenset(
    {
        "answer_index",
        "answer_order",
        "block_index",
        "bloom",
        "bucket_id",
        "correct",
        "error_token_id",
        "fixed",
        "handle",
        "id",
        "is_correct",
        "item_id",
        "kind",
        "left_id",
        "max_words",
        "min_words",
        "objective",
        "op",
        "option_id",
        "pairs",
        "response_language",
        "right_id",
        "section_id",
        "token_id",
    }
)


def capture_regeneration(
    root: Path,
    lesson_id: str,
    key: str | None,
    exercise_keys: list[str] | None = None,
    *,
    mode: Literal["scope", "exercises"] = "scope",
) -> dict[str, Any]:
    """Capture exact keyed scope and lesson context for restart-safe generation."""
    source = read_workbench_source(root, lesson_id)
    scope, selected_keys = _capture_regeneration_selection(source, key, mode, exercise_keys)
    available_exercise_keys = {block.key for block in source.blocks if block.kind == "exercise"}
    selected_exercise_keys = [item for item in selected_keys if item in available_exercise_keys]
    exercise_context = _capture_exercise_context(
        root, lesson_id, source, selected_exercise_keys, require_stale=mode == "exercises"
    )
    view = build_workbench_preview(resolve_lesson_package(root, lesson_id))
    prompt = build_chat_prompt(
        source,
        view,
        "Regenerate the selected scope. Keep its keys and authored order.",
        [],
        [],
        ProposalLog(),
        {},
        allow_regenerate=False,
    )
    if exercise_context:
        prompt += (
            "\nFor every changed exercise, include an English change_summary and answer_impact. "
            "Translate every changed exercise text string in translations; a string already in English "
            "repeats itself as its English translation."
        )
    return {
        "lesson_id": lesson_id,
        "key": key,
        "mode": mode,
        "scope": scope,
        "files": source.files,
        "selected_keys": selected_keys,
        "exercise_keys": selected_keys if mode == "exercises" else [],
        "exercise_context": exercise_context,
        "prompt": prompt,
        "steps": [],
    }


def generate_regeneration(root: Path, captured: dict[str, Any], *, repair: bool = False) -> Proposal:
    """Use workbench_author for one scoped proposal or one repair against original source."""
    prompt = captured["prompt"] + "\nReturn edits ONLY for these keys:\n" + json.dumps(captured["selected_keys"])
    if repair:
        prompt += "\nRepair once against ORIGINAL source. Candidate and check errors:\n" + json.dumps(
            captured["proposal"]
        )
    response = workbench_runner_for_role(root, "generation").structured(ChatResponse).invoke(prompt)
    allowed = set(captured["selected_keys"])
    if any(edit.key not in allowed for edit in response.edits):
        raise ValueError("Regeneration returned edits outside the selected scope")
    source = _captured_regeneration_source(captured["files"])
    original_blocks = {block.key: block for block in source.blocks}
    exercise_context = captured.get("exercise_context", {})
    for edit in response.edits:
        if edit.key in exercise_context:
            _require_exercise_decision_support(original_blocks[edit.key].source, edit)
    proposal, _ = create_proposal(captured["lesson_id"], source, response, origin="regenerate")
    if proposal is None:
        raise ValueError("Regeneration returned no changed source")
    _apply_captured_exercise_context(proposal, exercise_context)
    return proposal


def generate_regeneration_item(root: Path, captured: dict[str, Any], key: str, *, repair: bool = False) -> ProposedEdit:
    """Generate exactly one selected exercise while retaining the sibling ledger as context."""
    if key not in captured["exercise_keys"]:
        raise ValueError("Regeneration item is outside the captured exercise scope")
    source = _captured_regeneration_source(captured["files"])
    blocks = {block.key: block for block in source.blocks}
    block = blocks.get(key)
    if block is None or block.kind != "exercise":
        raise ValueError("Regeneration item is not an exercise in the captured source")
    siblings = [
        {"key": item.key, "source": item.source}
        for item in source.blocks
        if item.kind == "exercise" and item.key != key
    ]
    exercise_context = captured["exercise_context"][key]
    prompt = (
        captured["prompt"]
        + "\nRegenerate exactly this stale exercise. Make the smallest necessary change. "
        + "Keep every field that is not affected by the section change. Preserve wording, order, ids, "
        + "options, answers, distractors and explanations unless the change requires editing them. "
        + "If the exercise's purpose and answers are unaffected, return its current YAML unchanged."
        + f"\nEXERCISE ITEM KEY: {key}\nCURRENT EXERCISE YAML:\n{block.source}"
        + "\nEXACT BASELINE VS CURRENT SECTION DIFF (do not infer a broader change):\n"
        + json.dumps(exercise_context["changed_sections"], ensure_ascii=False, indent=2)
        + "\nSIBLING EXERCISE LEDGER:\n"
        + json.dumps(siblings, ensure_ascii=False)
        + "\nReturn English decision support: change_summary explains what changed and why; translations "
        + "lists every changed exercise text string (every string field except ids, numbers, and structural "
        + "fields) as its source with a non-empty english translation, repeating a string that is already "
        + "English as its own translation; answer_impact lists any changed answers, distractors or "
        + "explanations, or exactly 'none'; uncertainty is optional."
    )
    if repair:
        prompt += "\nRepair once against ORIGINAL source. Candidate and whole-set check errors:\n" + json.dumps(
            captured["proposal"], ensure_ascii=False
        )
    edit = _invoke_regeneration_item(root, prompt, block.source, key)
    _require_exercise_decision_support(block.source, edit)
    return edit


def assemble_regeneration(captured: dict[str, Any], edits: list[ProposedEdit]) -> Proposal | None:
    """Combine changed exercise results into one proposal, or return no proposal when all stay valid."""
    if [edit.key for edit in edits] != captured["exercise_keys"]:
        raise ValueError("Regeneration did not return every selected exercise in order")
    source = _captured_regeneration_source(captured["files"])
    original_blocks = {block.key: block for block in source.blocks}
    changed_edits = [edit for edit in edits if edit.after_source != original_blocks[edit.key].source]
    if not changed_edits:
        return None
    proposal, _reply = create_proposal(
        captured["lesson_id"],
        source,
        ChatResponse(reply="Regenerated stale exercises with English decision support", edits=changed_edits),
        origin="regenerate",
    )
    expected_keys = [edit.key for edit in changed_edits]
    if proposal is None or [block.key for block in proposal.blocks] != expected_keys:
        raise ValueError("Regeneration returned no changed source for one or more exercises")
    context = captured["exercise_context"]
    for block in proposal.blocks:
        block.pin = restore_pin(block.key, context[block.key])
    return proposal


def check_regeneration(root: Path, captured: dict[str, Any], proposal: Proposal) -> ProposalCheck:
    """Run the real compiler/audit over the captured candidate."""
    return check_proposal(root, captured["lesson_id"], _captured_regeneration_source(captured["files"]), proposal)


def review_regeneration(
    root: Path,
    captured: dict[str, Any],
    proposal: Proposal,
    *,
    cancellation: CancellationToken,
) -> dict[str, object]:
    """Review the generated candidate using WP2 review contracts in a disposable workspace."""
    if proposal.check.status == "failed":
        return {"summary": "AI review unavailable because candidate check failed.", "findings": []}
    paths = WorkspacePaths(root)
    with TemporaryDirectory(prefix="workbench-regenerate-review-") as temporary:
        candidate_root = Path(temporary)
        package = candidate_root / "content" / "lessons" / captured["lesson_id"]
        package.mkdir(parents=True)
        for name, text in proposal_files(_captured_regeneration_source(captured["files"]), proposal.blocks).items():
            (package / name).write_text(text)
        for target in (
            root / "config.yaml",
            paths.workbench_settings,
            root / ".opencode" / "agents",
            paths.authoring_root,
            paths.terminology_registry.parent,
        ):
            contained_workbench_path(root, target)
            destination = candidate_root / target.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, destination)
            elif target.is_file():
                shutil.copy2(target, destination)
        return review_workbench_lesson(
            candidate_root,
            captured["lesson_id"],
            captured["scope"],
            None if captured["scope"] == "lesson" else captured["key"],
            RLock(),
            cancellation=cancellation,
        )


def _captured_regeneration_source(files: dict[str, str]) -> WorkbenchSource:
    """Rebuild source spans from captured files rather than reading newer edits."""
    blocks = tuple(block for block in split_lesson_source(files["lesson.md"]).blocks if block.kind != "exercise")
    blocks += split_exercise_source(files["exercises.yaml"])
    return WorkbenchSource(files=files, blocks=blocks, exists=True, started_from=None, dirty=True, last_commit=None)


def _invoke_regeneration_item(root: Path, prompt: str, before_source: str, key: str) -> ProposedEdit:
    """Return one edit whose every changed exercise text string carries an English translation."""
    runner = workbench_runner_for_role(root, "generation").structured(ChatResponse)
    for attempt in range(2):
        response = runner.invoke(prompt)
        if len(response.edits) != 1 or response.edits[0].key != key:
            raise ValueError("Regeneration must return exactly the selected exercise")
        edit = response.edits[0]
        missing = _missing_changed_text_translations(before_source, edit)
        if not missing:
            return edit
        if attempt == 1:
            raise ValueError("Regenerated exercise is missing English translations for: " + ", ".join(missing))
        prompt += (
            "\nYour previous response omitted English translations for these changed exercise strings. "
            "Return a corrected ChatResponse. Preserve the candidate source, change summary, and answer impact; "
            "add one translation entry for every exact string below with norwegian set to that string and a "
            "non-empty english translation, repeating a string that is already English as its own translation."
            "\nMISSING STRINGS:\n"
            + json.dumps(missing, ensure_ascii=False)
            + "\nPREVIOUS RESPONSE:\n"
            + json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
        )
    raise RuntimeError("Regeneration response loop exited unexpectedly")


def _missing_changed_text_translations(before_source: str, edit: ProposedEdit) -> list[str]:
    """List regenerated text values whose changed leaf lacks an exact, non-empty English translation."""
    before_values = _exercise_text_values(before_source)
    after_values = _exercise_text_values(edit.after_source)
    changed = {value for path, value in after_values.items() if before_values.get(path) != value}
    translated = {item.norwegian for item in edit.translations if item.english.strip()}
    return sorted(changed - translated)


def _exercise_text_values(source: str) -> dict[tuple[str | int, ...], str]:
    """Extract free-text YAML scalars by their authored field path."""
    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError:
        return {}
    values: dict[tuple[str | int, ...], str] = {}
    _collect_exercise_text_values(document, (), values)
    return values


def _collect_exercise_text_values(
    value: object, path: tuple[str | int, ...], values: dict[tuple[str | int, ...], str]
) -> None:
    """Walk exercise YAML while excluding identity and control fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            field = str(key)
            if field not in K_WORKBENCH_REGENERATION_NON_TEXT_FIELDS:
                _collect_exercise_text_values(item, path + (field,), values)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_exercise_text_values(item, path + (index,), values)
    elif isinstance(value, str):
        values[path] = value


def _capture_exercise_context(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    exercise_keys: list[str],
    *,
    require_stale: bool,
) -> dict[str, ExerciseAlignmentPin]:
    """Capture current section pins and, when stale, exact diffs for selected exercises."""
    alignment = ExerciseAlignment(root, lesson_id)
    states = {state.exercise_key: state for state in alignment.states(source) if isinstance(state, ExerciseStale)}
    if require_stale and any(key not in states for key in exercise_keys):
        raise ValueError("Only exercises with known stale sections can be regenerated")
    return alignment.capture(source, exercise_keys)


def _require_exercise_decision_support(before_source: str, edit: ProposedEdit) -> None:
    """Require a changed exercise proposal to carry complete English decision support."""
    if edit.after_source == before_source:
        return
    if not edit.change_summary.strip():
        raise ValueError("Regeneration must explain the change in English")
    if not edit.answer_impact.strip():
        raise ValueError("Regeneration must report answer impact or state none")
    missing = _missing_changed_text_translations(before_source, edit)
    if missing:
        raise ValueError("Regenerated exercise is missing English translations for: " + ", ".join(missing))


def _apply_captured_exercise_context(proposal: Proposal, exercise_context: dict[str, ExerciseAlignmentPin]) -> None:
    """Attach captured alignment evidence to every scoped exercise proposal block."""
    for block in proposal.blocks:
        context = exercise_context.get(block.key)
        if context is None:
            continue
        block.pin = restore_pin(block.key, context)


def _require_exercise_keys(keys: list[str] | None, exercise_keys: set[str]) -> list[str]:
    if not keys or len(keys) != len(set(keys)) or any(key not in exercise_keys for key in keys):
        raise ValueError("Exercise regeneration requires distinct keys from the captured lesson")
    return list(keys)


def _capture_regeneration_selection(
    source: WorkbenchSource, key: str | None, mode: Literal["scope", "exercises"], exercise_keys: list[str] | None
) -> tuple[Literal["lesson", "section", "item"], list[str]]:
    """Resolve scope and stable keys for the captured request."""
    if mode == "exercises":
        return _capture_exercise_selection(source, key, exercise_keys)
    return _capture_scoped_selection(source, key)


def _capture_exercise_selection(
    source: WorkbenchSource, key: str | None, exercise_keys: list[str] | None
) -> tuple[Literal["lesson", "section", "item"], list[str]]:
    """Resolve selected exercise keys without using a scope sentinel."""
    if key is not None:
        raise ValueError("Exercise regeneration does not accept a scope key")
    available_keys = {block.key for block in source.blocks if block.kind == "exercise"}
    return "lesson", _require_exercise_keys(exercise_keys, available_keys)


def _capture_scoped_selection(
    source: WorkbenchSource, key: str | None
) -> tuple[Literal["lesson", "section", "item"], list[str]]:
    """Resolve a source key into a lesson, section or item selection."""
    if key is None:
        raise ValueError("Scoped regeneration requires a source key")
    scope: Literal["lesson", "section", "item"] = "lesson"
    if key != "whole":
        scope = "section" if any(block.key == key and block.kind == "heading" for block in source.blocks) else "item"
    selected_key = None if key == "whole" else key
    return scope, [block.key for block in select_review_blocks(source, scope, selected_key)]
