"""Entry point: `export_workbench_schema` writes the workbench API JSON Schema."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from typing import Literal
from typing import cast
from typing import is_typeddict

from pydantic import TypeAdapter

from lesson_builder.application.operations.workbench_audio import WorkbenchAudioPlan
from lesson_builder.application.operations.workbench_curriculum import CurriculumFinding
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignmentState
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateBlocker
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateStage
from lesson_builder.application.operations.workbench_lessons import LessonSource
from lesson_builder.application.operations.workbench_lessons import WorkbenchLesson
from lesson_builder.application.operations.workbench_new_lesson import NewLessonRequest
from lesson_builder.application.operations.workbench_proposals import ProposalDecision
from lesson_builder.application.operations.workbench_records import BlockExplanation
from lesson_builder.application.operations.workbench_records import ContextReference
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentRecord
from lesson_builder.application.operations.workbench_records import ExerciseSectionChange
from lesson_builder.application.operations.workbench_records import ExerciseTranslation
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_records import StoredChatTurn
from lesson_builder.application.operations.workbench_records import WorkbenchAudioClip as WorkbenchAudioPlanClip
from lesson_builder.application.operations.workbench_settings_operations import ReviewScope
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchEffort
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchRole
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_state import ActiveTurnMetadata
from lesson_builder.application.operations.workbench_state import BlockTarget
from lesson_builder.application.operations.workbench_state import ChipTone
from lesson_builder.application.operations.workbench_state import FindingOrigin
from lesson_builder.application.operations.workbench_state import FindingSeverity
from lesson_builder.application.operations.workbench_state import FindingStatus
from lesson_builder.application.operations.workbench_state import ProposalOrigin
from lesson_builder.application.operations.workbench_state import StatusChip
from lesson_builder.application.operations.workbench_state import WorkbenchFinding
from lesson_builder.application.operations.workbench_state import WorkbenchLessonState
from lesson_builder.application.operations.workbench_state import WorkbenchProposal
from lesson_builder.domain.curriculum.models import CurriculumEdit
from lesson_builder.domain.curriculum.models import CurriculumEditPreview
from lesson_builder.formats.workbench_proposal import ProposalText
from lesson_builder.web import workbench_wire

K_SCRIPT_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
K_SCRIPT_SCHEMA_PATH = K_SCRIPT_REPOSITORY_ROOT / "workbench" / "schema" / "workbench-api.schema.json"
K_SCRIPT_SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"
K_SCRIPT_CAMEL_BOUNDARY = re.compile(r"_([a-z0-9])")
K_SCRIPT_SCHEMA_DEFINITION_OVERRIDES = {"WorkbenchAudioPlanClip": "WorkbenchAudioPlanClip"}

type PayloadMode = Literal["input", "raw", "serialize", "model_dump"]
type PayloadSchema = tuple[Any, PayloadMode]
type JsonSchemaValue = str | int | float | bool | None | list[JsonSchemaValue] | dict[str, JsonSchemaValue]

K_SCRIPT_REQUESTS: dict[str, PayloadSchema] = {
    "empty": (workbench_wire.EmptyRequest, "input"),
    "settings.put": (WorkbenchSettings, "input"),
    "settings.reset": (workbench_wire.EmptyRequest, "input"),
    "lesson.block.put": (workbench_wire.WorkbenchBlockWriteRequest, "input"),
    "lesson.file.put": (workbench_wire.FileWriteRequest, "input"),
    "lesson.save": (workbench_wire.EmptyRequest, "input"),
    "curriculum.save": (workbench_wire.CurriculumSaveRequest, "input"),
    "curriculum.preview": (workbench_wire.CurriculumPreviewRequest, "input"),
    "curriculum.lesson.create": (NewLessonRequest, "input"),
    "curriculum.chat": (workbench_wire.CurriculumChatRequest, "input"),
    "lesson.chat": (workbench_wire.LessonChatRequest, "input"),
    "chat.new": (workbench_wire.EmptyRequest, "input"),
    "chat.retry": (workbench_wire.EmptyRequest, "input"),
    "chat.cancel": (workbench_wire.EmptyRequest, "input"),
    "lesson.explain": (workbench_wire.ExplainRequest, "input"),
    "lesson.review": (workbench_wire.ReviewRequest, "input"),
    "lesson.review_uncovered": (workbench_wire.EmptyRequest, "input"),
    "lesson.finding.fix": (workbench_wire.EmptyRequest, "input"),
    "lesson.finding.resolve": (workbench_wire.FindingResolveRequest, "input"),
    "lesson.audio.plan": (workbench_wire.AudioPlanRequest, "input"),
    "lesson.audio.synthesize": (workbench_wire.AudioSynthesizeRequest, "input"),
    "lesson.promote": (workbench_wire.EmptyRequest, "input"),
    "lesson.pull": (workbench_wire.EmptyRequest, "input"),
    "lesson.discard": (workbench_wire.DiscardRequest, "input"),
    "lesson.regenerate": (workbench_wire.WorkbenchRegenerationRequest, "input"),
    "lesson.proposal.accept": (workbench_wire.EmptyRequest, "input"),
    "lesson.proposal.reject": (workbench_wire.EmptyRequest, "input"),
    "lesson.proposal.keep": (workbench_wire.KeepRequest, "input"),
    "lesson.exercise.keep": (workbench_wire.KeepRequest, "input"),
    "job.cancel": (workbench_wire.EmptyRequest, "input"),
}

K_SCRIPT_RESPONSES: dict[str, PayloadSchema] = {
    "curriculum": (workbench_wire.WorkbenchCurriculumPayload, "serialize"),
    "curriculumChecks": (workbench_wire.WorkbenchCurriculumChecks, "raw"),
    "lesson": (workbench_wire.WorkbenchLessonPayload, "serialize"),
    "source": (workbench_wire.WorkbenchSourcePayload, "raw"),
    "settings": (workbench_wire.WorkbenchSettingsResponse, "model_dump"),
    "job": (workbench_wire.WorkbenchJobResponse, "raw"),
    "run": (workbench_wire.WorkbenchRegenerationResponse, "raw"),
    "error": (workbench_wire.WorkbenchErrorResponse, "raw"),
    "write": (workbench_wire.WorkbenchWriteResult, "serialize"),
    "save": (workbench_wire.WorkbenchSaveResult, "serialize"),
    "curriculumSave": (workbench_wire.WorkbenchCurriculumSaveResult, "serialize"),
    "curriculumPreview": (CurriculumEditPreview, "serialize"),
    "lessonAction": (workbench_wire.WorkbenchLessonActionResult, "serialize"),
    "jobTicket": (workbench_wire.WorkbenchJobTicket, "raw"),
    "chatJobTicket": (workbench_wire.WorkbenchChatJobTicket, "raw"),
    "regenerationTicket": (workbench_wire.WorkbenchRegenerationTicket, "raw"),
    "cancelResult": (workbench_wire.WorkbenchBooleanResult, "raw"),
    "resetResult": (workbench_wire.WorkbenchResetResult, "raw"),
    "audioPlan": (workbench_wire.WorkbenchAudioPlanActionResponse, "serialize"),
}

K_SCRIPT_COMPONENTS: dict[str, PayloadSchema] = {
    "WorkbenchLesson": (WorkbenchLesson, "serialize"),
    "WorkbenchLessonState": (WorkbenchLessonState, "serialize"),
    "WorkbenchFinding": (WorkbenchFinding, "serialize"),
    "WorkbenchProposal": (WorkbenchProposal, "serialize"),
    "BlockTarget": (BlockTarget, "serialize"),
    "StatusChip": (StatusChip, "serialize"),
    "WorkbenchStatusChip": (workbench_wire.WorkbenchStatusChip, "raw"),
    "ActiveTurnMetadata": (ActiveTurnMetadata, "serialize"),
    "LessonGateBlocker": (LessonGateBlocker, "serialize"),
    "LessonGateStage": (LessonGateStage, "serialize"),
    "ProposalDecision": (ProposalDecision, "serialize"),
    "ProposalOrigin": (ProposalOrigin, "serialize"),
    "ProposalCheck": (ProposalCheck, "serialize"),
    "StoredChatTurn": (StoredChatTurn, "serialize"),
    "ContextReference": (ContextReference, "serialize"),
    "ContextChipKind": (Literal["selection", "finding", "file"], "raw"),
    "BlockExplanation": (BlockExplanation, "serialize"),
    "ExerciseAlignmentState": (ExerciseAlignmentState, "serialize"),
    "ExerciseAlignmentRecord": (ExerciseAlignmentRecord, "serialize"),
    "ExerciseSectionChange": (ExerciseSectionChange, "serialize"),
    "ExerciseTranslation": (ExerciseTranslation, "serialize"),
    "ProposalText": (ProposalText, "serialize"),
    "CurriculumFinding": (CurriculumFinding, "serialize"),
    "FindingOrigin": (FindingOrigin, "raw"),
    "FindingSeverity": (FindingSeverity, "raw"),
    "FindingStatus": (FindingStatus, "raw"),
    "ChipTone": (ChipTone, "raw"),
    "ReviewScope": (ReviewScope, "raw"),
    "WorkbenchRole": (WorkbenchRole, "raw"),
    "WorkbenchEffort": (WorkbenchEffort, "raw"),
    "LessonSource": (LessonSource, "raw"),
    "WorkbenchSettings": (WorkbenchSettings, "input"),
    "WorkbenchSettingsResponse": (workbench_wire.WorkbenchSettingsResponse, "model_dump"),
    "WorkbenchCurriculumPayload": (workbench_wire.WorkbenchCurriculumPayload, "serialize"),
    "WorkbenchCurriculumEntry": (workbench_wire.WorkbenchCurriculumEntry, "raw"),
    "WorkbenchLessonPayload": (workbench_wire.WorkbenchLessonPayload, "serialize"),
    "WorkbenchSourcePayload": (workbench_wire.WorkbenchSourcePayload, "raw"),
    "WorkbenchPlanSource": (workbench_wire.WorkbenchPlanSource, "raw"),
    "WorkbenchLessonHeader": (workbench_wire.WorkbenchLessonHeader, "raw"),
    "WorkbenchSourceFinding": (workbench_wire.WorkbenchSourceFinding, "raw"),
    "WorkbenchSourceBlock": (workbench_wire.WorkbenchSourceBlock, "raw"),
    "WorkbenchDraftStatus": (workbench_wire.WorkbenchDraftStatus, "raw"),
    "WorkbenchCheckResult": (workbench_wire.WorkbenchCheckResult, "serialize"),
    "WorkbenchWriteResult": (workbench_wire.WorkbenchWriteResult, "serialize"),
    "WorkbenchSaveResult": (workbench_wire.WorkbenchSaveResult, "serialize"),
    "WorkbenchCurriculumSaveResult": (workbench_wire.WorkbenchCurriculumSaveResult, "serialize"),
    "WorkbenchLessonActionResult": (workbench_wire.WorkbenchLessonActionResult, "serialize"),
    "WorkbenchBlockWriteRequest": (workbench_wire.WorkbenchBlockWriteRequest, "input"),
    "WorkbenchRegenerationRequest": (workbench_wire.WorkbenchRegenerationRequest, "input"),
    "WorkbenchAudioFinding": (workbench_wire.WorkbenchAudioFinding, "raw"),
    "NewLessonRequest": (NewLessonRequest, "input"),
    "CurriculumEditRequest": (list[CurriculumEdit], "input"),
    "WorkbenchAudioPlan": (WorkbenchAudioPlan, "serialize"),
    "WorkbenchAudioPlanClip": (WorkbenchAudioPlanClip, "serialize"),
    "WorkbenchAudioClipStatus": (workbench_wire.WorkbenchAudioClipStatus, "raw"),
    "WorkbenchAudioVoice": (workbench_wire.WorkbenchAudioVoice, "raw"),
    "WorkbenchAudioStatus": (workbench_wire.WorkbenchAudioStatus, "raw"),
    "CurriculumEditPreview": (CurriculumEditPreview, "serialize"),
}

K_SCRIPT_TYPED_DICT_NAMES = {
    value.__name__ for value in vars(workbench_wire).values() if isinstance(value, type) and is_typeddict(value)
}


def export_workbench_schema(*, check: bool = False) -> bool:
    """Write the schema catalog or report whether the committed file is current."""
    schema = _build_schema()
    contents = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    if check:
        try:
            committed = K_SCRIPT_SCHEMA_PATH.read_text(encoding="utf-8")
        except OSError:
            return False
        return committed == contents
    K_SCRIPT_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    K_SCRIPT_SCHEMA_PATH.write_text(contents, encoding="utf-8")
    return True


def main() -> int:
    """Run schema generation from the repository command line."""
    parser = argparse.ArgumentParser(description="Export or check the workbench API JSON Schema.")
    parser.add_argument("--check", action="store_true", help="fail if the committed schema is stale")
    arguments = parser.parse_args()
    current = export_workbench_schema(check=arguments.check)
    if not current:
        print(f"Workbench schema is stale: {K_SCRIPT_SCHEMA_PATH}", file=sys.stderr)
        return 1
    return 0


def _build_schema() -> dict[str, object]:
    definitions: dict[str, object] = {}
    request_properties = {
        name: {"$ref": f"#/$defs/{_install_schema(name, payload, definitions)}"}
        for name, payload in K_SCRIPT_REQUESTS.items()
    }
    response_properties = {
        name: {"$ref": f"#/$defs/{_install_schema(name, payload, definitions)}"}
        for name, payload in K_SCRIPT_RESPONSES.items()
    }
    component_properties = {
        name: {"$ref": f"#/$defs/{_install_schema(name, payload, definitions)}"}
        for name, payload in K_SCRIPT_COMPONENTS.items()
    }
    return {
        "$schema": K_SCRIPT_SCHEMA_URI,
        "title": "WorkbenchApiSchemaCatalog",
        "type": "object",
        "properties": {
            "requests": {"type": "object", "properties": request_properties, "additionalProperties": False},
            "responses": {"type": "object", "properties": response_properties, "additionalProperties": False},
            "components": {"type": "object", "properties": component_properties, "additionalProperties": False},
        },
        "required": ["requests", "responses", "components"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def _install_schema(name: str, payload: PayloadSchema, definitions: dict[str, object]) -> str:
    payload_type, mode = payload
    adapter_schema = TypeAdapter(payload_type).json_schema(ref_template="#/$defs/{model}")
    definition_name = K_SCRIPT_SCHEMA_DEFINITION_OVERRIDES.get(
        name, str(adapter_schema.get("title") or _as_type_name(name))
    )
    if name in K_SCRIPT_SCHEMA_DEFINITION_OVERRIDES:
        adapter_schema["title"] = definition_name
    nested_definitions = adapter_schema.pop("$defs", {})
    reference_names = {old_name: f"{definition_name}__{old_name}" for old_name in nested_definitions}
    adapter_schema = cast(dict[str, Any], _rewrite_schema_refs(adapter_schema, reference_names))
    if mode in ("serialize", "model_dump") and not is_typeddict(payload_type):
        _prepare_model_schema(adapter_schema, camelize=mode == "serialize")
    definitions[definition_name] = adapter_schema
    for old_name, nested_schema in nested_definitions.items():
        new_schema = _rewrite_schema_refs(nested_schema, reference_names)
        if mode in ("serialize", "model_dump") and old_name not in K_SCRIPT_TYPED_DICT_NAMES:
            _prepare_model_schema(new_schema, camelize=mode == "serialize")
        definitions[reference_names[old_name]] = new_schema
    return definition_name


def _prepare_model_schema(schema: JsonSchemaValue, *, camelize: bool) -> None:
    if isinstance(schema, dict):
        _prepare_object_schema(schema, camelize=camelize)
    elif isinstance(schema, list):
        _prepare_schema_items(schema, camelize=camelize)


def _prepare_object_schema(schema: dict[str, JsonSchemaValue], *, camelize: bool) -> None:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        renamed = {_convert_to_camel_case(name) if camelize else name: value for name, value in properties.items()}
        schema["properties"] = renamed
        schema["required"] = list(renamed)
    for value in schema.values():
        _prepare_model_schema(value, camelize=camelize)


def _prepare_schema_items(items: list[JsonSchemaValue], *, camelize: bool) -> None:
    for item in items:
        _prepare_model_schema(item, camelize=camelize)


def _rewrite_schema_refs(value: JsonSchemaValue, names: dict[str, str]) -> JsonSchemaValue:
    if isinstance(value, dict):
        result: dict[str, JsonSchemaValue] = {}
        for key, nested in value.items():
            if key == "$ref" and isinstance(nested, str) and nested.startswith("#/$defs/"):
                old_name = nested.removeprefix("#/$defs/")
                result[key] = f"#/$defs/{names.get(old_name, old_name)}"
            else:
                result[key] = _rewrite_schema_refs(nested, names)
        return result
    if isinstance(value, list):
        return [_rewrite_schema_refs(item, names) for item in value]
    return value


def _convert_to_camel_case(name: str) -> str:
    return K_SCRIPT_CAMEL_BOUNDARY.sub(lambda match: match.group(1).upper(), name)


def _as_type_name(name: str) -> str:
    parts = name.replace(".", "_").split("_")
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


if __name__ == "__main__":
    raise SystemExit(main())
