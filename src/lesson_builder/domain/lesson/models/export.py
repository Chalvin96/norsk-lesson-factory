"""Not a check itself — public lesson packet contract definitions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Self
from typing import cast
from urllib.parse import urlsplit

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from lesson_builder.domain.lesson.models.elements import BuildPayload
from lesson_builder.domain.lesson.models.elements import CategorizePayload
from lesson_builder.domain.lesson.models.elements import FindFixPayload
from lesson_builder.domain.lesson.models.elements import MatchPairsPayload
from lesson_builder.domain.lesson.models.elements import SpeakPayload
from lesson_builder.domain.lesson.models.elements import WritePayload
from lesson_builder.domain.lesson.models.inline import CodeSpan
from lesson_builder.domain.lesson.models.inline import EmphasisSpan
from lesson_builder.domain.lesson.models.inline import ForeignTermSpan
from lesson_builder.domain.lesson.models.inline import StrongSpan
from lesson_builder.domain.lesson.models.inline import TextSpan
from lesson_builder.domain.lesson.settings import K_EXPORT_SPEAKER_ICON_BASE_URL

K_AUDIO_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
K_EXPORT_ASCII_CONTROL_LIMIT = 32
K_AUDIO_PATH_RE = re.compile(
    r"^audio/lessons/(?P<packet_id>[^/]+)/"
    r"(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})\.wav$"
)
ExportedInlineSpan = Annotated[
    TextSpan | EmphasisSpan | StrongSpan | CodeSpan | ForeignTermSpan,
    Field(discriminator="kind"),
]
ExportedSpans = list[ExportedInlineSpan]


class ExportBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportedHeadingBlock(ExportBase):
    kind: Literal["heading"]
    id: str
    level: Literal[3, 4]
    spans: ExportedSpans


class ExportedParagraphBlock(ExportBase):
    kind: Literal["paragraph"]
    id: str
    spans: ExportedSpans


class ExportedReadingBlock(ExportBase):
    kind: Literal["reading"]
    id: str
    spans: ExportedSpans
    translation: str = Field(min_length=1)
    speaker_id: str | None = None
    speaker_name: str | None = None
    dialogue_id: str | None = None
    turn_index: int | None = Field(default=None, ge=1)
    speaker_icon_url: str | None = None
    audio_id: str | None = None

    @field_validator("speaker_icon_url")
    @classmethod
    def _speaker_icon_uses_open_peeps(cls, value: str | None) -> str | None:
        """Keep public speaker icons on the versioned Open Peeps endpoint."""
        if value is not None and not value.startswith(f"{K_EXPORT_SPEAKER_ICON_BASE_URL}?"):
            raise ValueError("speaker_icon_url must use the versioned DiceBear Open Peeps endpoint")
        return value


class ExportedListBlock(ExportBase):
    kind: Literal["list"]
    id: str
    ordered: bool
    items: list[ExportedSpans]


class ExportedRuleBlock(ExportBase):
    kind: Literal["rule"]
    id: str
    statement: ExportedSpans


class ExportedExampleItem(ExportBase):
    id: str
    no: ExportedSpans
    en: ExportedSpans
    audio_id: str | None = None


class ExportedExampleBlock(ExportBase):
    kind: Literal["example"]
    id: str
    no: ExportedSpans
    en: ExportedSpans
    audio_id: str | None = None


class ExportedExamplesBlock(ExportBase):
    kind: Literal["examples"]
    id: str
    items: list[ExportedExampleItem]


class ExportedWordListItem(ExportBase):
    term: str
    form: str


class ExportedWordListBlock(ExportBase):
    kind: Literal["word_list"]
    id: str
    items: list[ExportedWordListItem]


class ExportedTableBlock(ExportBase):
    kind: Literal["table"]
    id: str
    col_langs: list[Literal["en", "no"]]
    headers: list[ExportedSpans]
    rows: list[list[ExportedSpans]]

    @model_validator(mode="after")
    def _columns_are_consistent(self) -> Self:
        """Require every table header and row to match its declared width."""
        width = len(self.col_langs)
        if len(self.headers) != width:
            raise ValueError("col_langs must match header count")
        for row in self.rows:
            if len(row) != width:
                raise ValueError("col_langs must match every row width")
        return self


class ExportedCalloutBlock(ExportBase):
    kind: Literal["callout"]
    id: str
    level: Literal["tip", "warning", "note"]
    blocks: list[ExportedBlock]


ExportedBlock = Annotated[
    ExportedHeadingBlock
    | ExportedParagraphBlock
    | ExportedReadingBlock
    | ExportedListBlock
    | ExportedRuleBlock
    | ExportedExampleBlock
    | ExportedExamplesBlock
    | ExportedWordListBlock
    | ExportedTableBlock
    | ExportedCalloutBlock,
    Field(discriminator="kind"),
]
ExportedCalloutBlock.model_rebuild()


class ExportedSection(ExportBase):
    kind: Literal["section"]
    id: str
    role: Literal["orient", "model", "contrast", "recap"]
    title: str
    objective_ids: list[str]
    blocks: list[ExportedBlock]


class ExportedContentItem(ExportBase):
    """One ordered learner-facing section or exercise reference."""

    kind: Literal["section", "exercise"]
    id: str


class ExportedRecallSpanSegment(ExportBase):
    kind: Literal["span"]
    spans: ExportedSpans


class ExportedRecallBlank(ExportBase):
    kind: Literal["blank"]
    blank_id: str
    options: list[str] = Field(min_length=2)
    answer_index: int

    @model_validator(mode="after")
    def _answer_in_range(self) -> ExportedRecallBlank:
        if not 0 <= self.answer_index < len(self.options):
            raise ValueError("answer_index out of range for options")
        return self


ExportedRecallSegment = Annotated[
    ExportedRecallSpanSegment | ExportedRecallBlank,
    Field(discriminator="kind"),
]


class ExportedRecallFillPayload(ExportBase):
    segments: list[ExportedRecallSegment]

    @model_validator(mode="after")
    def _blank_ids_are_unique(self) -> Self:
        duplicates = _duplicates(
            segment.blank_id for segment in self.segments if isinstance(segment, ExportedRecallBlank)
        )
        if duplicates:
            raise ValueError(f"duplicate blank id {sorted(duplicates)[0]!r} in recall_fill")
        return self


class ExportedJudgePayload(ExportBase):
    sentence: ExportedSpans
    is_correct: bool
    feedback: str | None = None


class ExportedChooseOption(ExportBase):
    option_id: str
    text: str
    why: str | None = None


class ExportedChoosePayload(ExportBase):
    options: list[ExportedChooseOption] = Field(min_length=2)
    answer_id: str
    stem: ExportedSpans | None = None

    @model_validator(mode="after")
    def _answer_exists(self) -> ExportedChoosePayload:
        if self.answer_id not in {option.option_id for option in self.options}:
            raise ValueError("answer_id must match an option_id")
        return self

    @model_validator(mode="after")
    def _option_ids_are_unique(self) -> Self:
        duplicates = _duplicates(option.option_id for option in self.options)
        if duplicates:
            raise ValueError(f"duplicate option id {sorted(duplicates)[0]!r} in choose")
        return self


class ExportedExerciseBase[OperationT: str, PayloadT: BaseModel](ExportBase):
    """Common fields for one statically typed exported exercise variant."""

    kind: Literal["exercise"]
    id: str
    operation: OperationT
    objective_id: str
    prompt: ExportedSpans
    explanation: ExportedSpans | None
    audio_id: str | None = None
    payload: PayloadT


type ExportedRecallFillExercise = ExportedExerciseBase[Literal["recall_fill"], ExportedRecallFillPayload]
type ExportedMatchPairsExercise = ExportedExerciseBase[Literal["match_pairs"], MatchPairsPayload]
type ExportedJudgeExercise = ExportedExerciseBase[Literal["judge"], ExportedJudgePayload]
type ExportedChooseExercise = ExportedExerciseBase[Literal["choose"], ExportedChoosePayload]
type ExportedCategorizeExercise = ExportedExerciseBase[Literal["categorize"], CategorizePayload]
type ExportedBuildExercise = ExportedExerciseBase[Literal["build"], BuildPayload]
type ExportedFindFixExercise = ExportedExerciseBase[Literal["find_fix"], FindFixPayload]
type ExportedSpeakExercise = ExportedExerciseBase[Literal["speak"], SpeakPayload]
type ExportedWriteExercise = ExportedExerciseBase[Literal["write"], WritePayload]

ExportedExercise = Annotated[
    ExportedRecallFillExercise
    | ExportedMatchPairsExercise
    | ExportedJudgeExercise
    | ExportedChooseExercise
    | ExportedCategorizeExercise
    | ExportedBuildExercise
    | ExportedFindFixExercise
    | ExportedSpeakExercise
    | ExportedWriteExercise,
    Field(discriminator="operation"),
]


class ExportedObjective(ExportBase):
    id: str
    statement: str


class ExportedPracticeGroup(ExportBase):
    id: str
    objective_id: str
    exercise_ids: list[str]


class ExportedAudio(ExportBase):
    id: str
    path: str
    url: str
    mime: str
    duration_ms: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    status: Literal["synthesized"]

    @field_validator("sha256")
    @classmethod
    def _sha256_is_bare_hex(cls, value: str | None) -> str | None:
        """Require the consumer-facing bare SHA-256 representation."""
        if value is not None and not K_AUDIO_SHA256_RE.fullmatch(value):
            raise ValueError("audio sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("path")
    @classmethod
    def _path_is_distribution_relative(cls, value: str) -> str:
        """Reject absolute or parent-traversing distribution asset paths."""
        if not value or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("audio path must be a non-empty distribution-relative path")
        if ".." in PurePosixPath(value).parts or ".." in PureWindowsPath(value).parts:
            raise ValueError("audio path must not traverse parent directories")
        if K_AUDIO_PATH_RE.fullmatch(value) is None:
            raise ValueError("audio path must match audio/lessons/<packet-id>/<uuid>.wav")
        return value

    @model_validator(mode="after")
    def _url_matches_path(self) -> ExportedAudio:
        """Bind the ready URL to the portable object key."""
        if not urlsplit(self.url).path.endswith(f"/{self.path}"):
            raise ValueError("audio url must end with the exported audio path")
        return self

    @field_validator("url")
    @classmethod
    def _url_is_absolute_https(cls, value: str) -> str:
        """Require a ready-to-fetch public HTTPS URL."""
        if (
            not value
            or value != value.strip()
            or any(ord(char) < K_EXPORT_ASCII_CONTROL_LIMIT or char.isspace() for char in value)
        ):
            raise ValueError("audio url must be an absolute HTTPS URL")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None or parsed.password is not None:
            raise ValueError("audio url must be an absolute HTTPS URL")
        return value


class ExportedMedia(ExportBase):
    audio: list[ExportedAudio]


class ExportedLesson(ExportBase):
    """The lesson packet consumed by the learning app.

    Sections and exercises remain separate lookup collections. ``content`` is
    the authored learner-facing order that interleaves those collections without
    adding app-side page or session state to the contract.
    """

    schema_version: Literal["4.0"]
    id: str
    kind: Literal["grammar", "phraseology", "communicative", "pronunciation", "writing"]
    language: Literal["nb-NO"] = "nb-NO"
    title: str
    cefr_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"]
    goal: str
    objectives: list[ExportedObjective] = Field(min_length=1)
    content: list[ExportedContentItem]
    sections: list[ExportedSection]
    exercises: list[ExportedExercise]
    practice_groups: list[ExportedPracticeGroup]
    media: ExportedMedia

    @model_validator(mode="after")
    def _audio_references_are_resolvable(self) -> ExportedLesson:
        """Require unique media IDs and resolve every public block reference."""
        media_ids = [audio.id for audio in self.media.audio]
        if len(media_ids) != len(set(media_ids)):
            raise ValueError("media.audio ids must be unique")
        expected_audio_prefix = f"audio/lessons/{self.id}/"
        foreign_audio = [audio.id for audio in self.media.audio if not audio.path.startswith(expected_audio_prefix)]
        if foreign_audio:
            raise ValueError("audio paths must belong to the packet lesson id: " + ", ".join(sorted(foreign_audio)))
        referenced_ids = {audio_id for section in self.sections for audio_id in _section_audio_ids(section)}
        referenced_ids.update(
            str(exercise.audio_id) for exercise in cast(list[Any], self.exercises) if exercise.audio_id
        )
        unknown_ids = referenced_ids.difference(media_ids)
        if unknown_ids:
            raise ValueError(
                "audio_id references must resolve to media.audio entries: " + ", ".join(sorted(unknown_ids))
            )
        orphan_synthesized_ids = {audio.id for audio in self.media.audio if audio.status == "synthesized"}.difference(
            referenced_ids
        )
        if orphan_synthesized_ids:
            raise ValueError(
                "synthesized media.audio entries must be referenced by a public block: "
                + ", ".join(sorted(orphan_synthesized_ids))
            )
        return self

    @model_validator(mode="after")
    def _lesson_references_are_resolvable(self) -> ExportedLesson:
        """Require objective and practice-group references to resolve."""
        objective_ids = {objective.id for objective in self.objectives}
        exercise_values = cast(list[Any], self.exercises)
        exercise_ids = {str(exercise.id) for exercise in exercise_values}
        unknown_section_objectives = {
            objective_id
            for section in self.sections
            for objective_id in section.objective_ids
            if objective_id not in objective_ids
        }
        unknown_exercise_objectives = {
            str(exercise.objective_id) for exercise in exercise_values if exercise.objective_id not in objective_ids
        }
        unknown_group_objectives = {
            group.objective_id for group in self.practice_groups if group.objective_id not in objective_ids
        }
        unknown_group_exercises = {
            exercise_id
            for group in self.practice_groups
            for exercise_id in group.exercise_ids
            if exercise_id not in exercise_ids
        }
        section_ids = {section.id for section in self.sections}
        content_section_ids = [item.id for item in self.content if item.kind == "section"]
        content_exercise_ids = [item.id for item in self.content if item.kind == "exercise"]
        unknown_content_sections = set(content_section_ids).difference(section_ids)
        unknown_content_exercises = set(content_exercise_ids).difference(exercise_ids)
        duplicate_content_sections = _duplicates(content_section_ids)
        duplicate_content_exercises = _duplicates(content_exercise_ids)
        missing_content_sections = section_ids.difference(content_section_ids)
        missing_content_exercises = exercise_ids.difference(content_exercise_ids)
        enrolled: list[str] = [exercise_id for group in self.practice_groups for exercise_id in group.exercise_ids]
        duplicate_group_exercises = _duplicates(enrolled)
        missing_group_exercises = exercise_ids.difference(enrolled)
        if unknown_section_objectives or unknown_exercise_objectives:
            raise ValueError(
                "lesson objective references must resolve: "
                + ", ".join(sorted(unknown_section_objectives | unknown_exercise_objectives))
            )
        if unknown_group_objectives or unknown_group_exercises:
            raise ValueError(
                "practice-group references must resolve: "
                + ", ".join(sorted(unknown_group_objectives | unknown_group_exercises))
            )
        if (
            unknown_content_sections
            or unknown_content_exercises
            or duplicate_content_sections
            or duplicate_content_exercises
            or missing_content_sections
            or missing_content_exercises
        ):
            problems: list[str] = []
            if unknown_content_sections or unknown_content_exercises:
                problems.append("unknown " + ", ".join(sorted(unknown_content_sections | unknown_content_exercises)))
            if duplicate_content_sections or duplicate_content_exercises:
                problems.append(
                    "repeated " + ", ".join(sorted(duplicate_content_sections | duplicate_content_exercises))
                )
            if missing_content_sections or missing_content_exercises:
                problems.append("missing " + ", ".join(sorted(missing_content_sections | missing_content_exercises)))
            raise ValueError("content references must resolve exactly once (" + "; ".join(problems) + ")")
        if duplicate_group_exercises or missing_group_exercises:
            group_problems: list[str] = []
            if duplicate_group_exercises:
                group_problems.append("assigned more than once: " + ", ".join(sorted(duplicate_group_exercises)))
            if missing_group_exercises:
                group_problems.append("not assigned: " + ", ".join(sorted(missing_group_exercises)))
            raise ValueError(
                "every exercise must belong to exactly one practice group (" + "; ".join(group_problems) + ")"
            )
        return self

    @model_validator(mode="after")
    def _all_public_ids_are_unique(self) -> ExportedLesson:
        """Reject duplicate IDs before an importer has to resolve ambiguity."""
        exercise_values = cast(list[Any], self.exercises)
        id_sets = {
            "objective": [objective.id for objective in self.objectives],
            "section": [section.id for section in self.sections],
            "exercise": [str(exercise.id) for exercise in exercise_values],
            "practice_group": [group.id for group in self.practice_groups],
            "block": [block_id for section in self.sections for block_id in _section_public_ids(section)],
        }
        duplicate_messages = []
        for kind, ids in id_sets.items():
            duplicates = _duplicates(ids)
            if duplicates:
                duplicate_messages.append(f"{kind}: {', '.join(sorted(duplicates))}")
        if duplicate_messages:
            raise ValueError("public IDs must be unique (" + "; ".join(duplicate_messages) + ")")
        return self


def _section_audio_ids(section: ExportedSection) -> list[str]:
    """Collect audio references from a section and nested callout blocks."""
    result: list[str] = []
    for block in section.blocks:
        if (isinstance(block, ExportedReadingBlock) and block.audio_id) or (
            isinstance(block, ExportedExampleBlock) and block.audio_id
        ):
            result.append(block.audio_id)
        elif isinstance(block, ExportedExamplesBlock):
            result.extend(item.audio_id for item in block.items if item.audio_id)
        elif isinstance(block, ExportedCalloutBlock):
            result.extend(_nested_block_audio_ids(block.blocks))
    return result


def _nested_block_audio_ids(blocks: list[ExportedBlock]) -> list[str]:
    """Collect audio references from nested callout blocks."""
    result: list[str] = []
    for block in blocks:
        if (isinstance(block, ExportedReadingBlock) and block.audio_id) or (
            isinstance(block, ExportedExampleBlock) and block.audio_id
        ):
            result.append(block.audio_id)
        elif isinstance(block, ExportedExamplesBlock):
            result.extend(item.audio_id for item in block.items if item.audio_id)
        elif isinstance(block, ExportedCalloutBlock):
            result.extend(_nested_block_audio_ids(block.blocks))
    return result


def _section_public_ids(section: ExportedSection) -> list[str]:
    """Collect block and example-item IDs from one section recursively."""
    result: list[str] = []
    for block in section.blocks:
        result.append(block.id)
        if isinstance(block, ExportedExamplesBlock):
            result.extend(item.id for item in block.items)
        elif isinstance(block, ExportedCalloutBlock):
            result.extend(_nested_public_ids(block.blocks))
    return result


def _nested_public_ids(blocks: list[ExportedBlock]) -> list[str]:
    """Collect public IDs from nested blocks and example items."""
    result: list[str] = []
    for block in blocks:
        result.append(block.id)
        if isinstance(block, ExportedExamplesBlock):
            result.extend(item.id for item in block.items)
        elif isinstance(block, ExportedCalloutBlock):
            result.extend(_nested_public_ids(block.blocks))
    return result


def _duplicates(values: Iterable[str]) -> set[str]:
    """Return repeated values from an ID list."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


__all__ = [
    "ExportedAudio",
    "ExportedBlock",
    "ExportedContentItem",
    "ExportedElement",
    "ExportedExercise",
    "ExportedLesson",
    "ExportedMedia",
    "ExportedObjective",
    "ExportedPracticeGroup",
    "ExportedSection",
]


ExportedElement = Annotated[ExportedSection | ExportedExercise, Field(discriminator="kind")]
