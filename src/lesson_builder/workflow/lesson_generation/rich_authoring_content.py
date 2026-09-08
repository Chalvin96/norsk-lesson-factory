"""Entry points: deterministic rich-authoring policy and repair operations.

Provider-neutral authoring policy for the rich authoring lesson path: the
transport envelopes exchanged with the model, deterministic content parsing,
validation, and repair helpers. Prompt construction is application-owned;
provider invocation and scratch orchestration are workflow-owned. The stage
sequencer calls these operations; this module never writes workflow receipts,
attestations, cache inputs, or scratch artifact paths.

"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from typing import cast

import panflute
import yaml

from lesson_builder.application.operations.convert_lesson_blocks import convert_blocks
from lesson_builder.domain.lesson.models.operations import evidence_route_bloom_levels
from lesson_builder.domain.lesson.models.operations import evidence_route_operations
from lesson_builder.domain.lesson.models.operations import operation_policy
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.quality_review import PreservationFinding
from lesson_builder.domain.lesson.models.quality_review import QualityFinding
from lesson_builder.domain.lesson.models.rich_authoring import ExerciseRequest
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEdit
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizationEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizationTextEdit
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.domain.lesson.validation.standalone_references import scan_unresolved_references
from lesson_builder.formats.markdown.pandoc import parse_markdown
from lesson_builder.formats.yaml import load_unique_yaml
from lesson_builder.workflow.lesson_generation.rich_authoring_exercise_repairs import (
    repair_exercise_item as _repair_exercise_item,
)

K_RICH_INTENT_MIN_EVIDENCE_LENGTH = 16
K_RICH_REQUEST_FIELDS = ("handle", "objective_ref", "bloom", "evidence_route", "evidence")
K_RICH_LEGACY_REQUEST_FIELDS = frozenset(
    {
        "evidence_family",
        "intent",
        "context",
        "learner_action",
        "success_criteria",
        "source_section",
    }
)
K_RICH_INTENT_OPERATION_FIELDS = frozenset(
    {
        "op",
        "options",
        "tokens",
        "answer",
        "answer_index",
        "answer_order",
        "error_token_id",
        "buckets",
        "items",
        "pairs",
        "segments",
        "left",
        "right",
        "stem_md",
        "sentence_md",
        "target",
        "feedback",
        "judge_prompt",
        "criteria",
    }
)


def apply_lesson_draft_edits(
    draft_text: str,
    review: LessonQualityReview,
    edit_response: LessonDraftEditResponse,
) -> str:
    """Apply non-overlapping exact replacements and reject regeneration.

    Validate the complete edit envelope against the original bytes before making
    any replacement. This prevents a first valid edit from hiding a later
    occurrence-count error and gives the stateless transport correction one
    complete diagnostic set to repair.
    """
    finding_refs: list[tuple[str, QualityFinding]] = []
    code_counts: dict[str, int] = {}
    for finding in review.findings:
        code_counts[finding.code] = code_counts.get(finding.code, 0) + 1
        finding_refs.append((f"{finding.code}:{code_counts[finding.code]}", finding))
    material_refs = {ref for ref, finding in finding_refs if finding.severity in {"blocking", "major"}}
    edit_refs = {edit.finding_ref for edit in edit_response.edits}
    unknown_refs = sorted(edit_refs - {ref for ref, _finding in finding_refs})
    missing_refs = sorted(material_refs - edit_refs)
    errors: list[str] = []
    if unknown_refs:
        errors.append(f"unknown finding ref(s): {unknown_refs!r}")
    if missing_refs:
        errors.append("missing material finding ref(s): " + ", ".join(missing_refs))

    replacements, replacement_errors = _lesson_edit_replacements(draft_text, edit_response.edits)
    errors.extend(replacement_errors)
    ordered_replacements = sorted(replacements, key=lambda item: (item[0], item[1]))
    errors.extend(_overlap_errors(ordered_replacements, "overlapping exact edits"))
    if errors:
        raise ValueError("lesson edits invalid: " + "; ".join(errors))

    repaired_text = draft_text
    for start, end, new_text, _finding_ref in reversed(ordered_replacements):
        repaired_text = repaired_text[:start] + new_text + repaired_text[end:]
    return repaired_text


def apply_normalization_edits(
    package: NormalizedPackage,
    review: NormalizationPreservationReview,
    edit_response: NormalizationEditResponse,
) -> NormalizedPackage:
    """Apply exact replacements to normalized fields without regeneration."""
    finding_refs = _normalization_finding_refs(review)
    known_refs = {ref for ref, _finding in finding_refs}
    edit_refs = {edit.finding_ref for edit in edit_response.edits}
    unknown_refs = sorted(edit_refs - known_refs)
    missing_refs = sorted(known_refs - edit_refs)
    if unknown_refs:
        raise ValueError("normalization edits reference unknown finding refs: " + ", ".join(unknown_refs))
    if missing_refs:
        raise ValueError("normalization edits do not address finding refs: " + ", ".join(missing_refs))
    values = {
        "lesson_md": package.lesson_md,
        "exercise_requests_yaml": package.exercise_requests_yaml,
    }
    replacements: dict[str, list[tuple[int, int, str, str]]] = {
        "lesson_md": [],
        "exercise_requests_yaml": [],
    }
    errors = _apply_normalization_edits(values, replacements, edit_response.edits)
    if errors:
        raise ValueError("normalization edits invalid: " + "; ".join(errors))
    return package.model_copy(update=values)


def validate_normalized_lesson_shape(lesson_md: str) -> None:
    """Reject source examples that cannot produce paired learner translations."""
    compiler_safe_lesson = normalize_compiler_unsafe_markdown(lesson_md)
    _validate_forbidden_lesson_lines(lesson_md)
    _validate_rule_blocks(lesson_md)
    _validate_reading_attributes(lesson_md)
    _validate_compiler_blocks(compiler_safe_lesson)
    _validate_examples_blocks(lesson_md)


def validate_exercise_requests(
    requests_yaml: str,
    objective_ids: set[str],
    lesson_md: str,
) -> str:
    """Validate the compact handoff without allowing operation details upstream."""
    raw = load_unique_yaml(requests_yaml)
    if not isinstance(raw, list) or not raw:
        raise ValueError("exercise request handoff must be a non-empty YAML list")
    requests = [_validate_exercise_request_value(value, objective_ids) for value in raw]
    handles = [request.handle for request in requests]
    if len(set(handles)) != len(handles):
        raise ValueError("exercise request handles must be unique")
    marker_handles = re.findall(r"\{\{exercise:\s*([^}\s]+)\s*\}\}", lesson_md)
    if marker_handles != handles:
        raise ValueError(
            "lesson exercise markers and exercise request handles differ in set or order: "
            f"markers={marker_handles!r}, requests={handles!r}"
        )
    return cast(
        str,
        yaml.safe_dump(
            [request.model_dump(mode="json") for request in requests],
            allow_unicode=True,
            sort_keys=False,
        ),
    )


def extract_exercise_requests(
    package: NormalizedPackage,
    objective_ids: set[str],
) -> tuple[str, list[str]]:
    """Extract and attest the operation-free request sidecar after normalization.

    Normalization may still return a transport envelope containing lesson prose
    and request text for replay compatibility. This boundary makes extraction a
    separate deterministic operation: it validates and canonicalizes the YAML,
    then runs the intent-only checks before any operation-bearing model call.
    """
    requests_yaml = validate_exercise_requests(
        package.exercise_requests_yaml,
        objective_ids,
        package.lesson_md,
    )
    return requests_yaml, _exercise_intent_findings(requests_yaml)


def validate_exercise_alignment(exercises_yaml: str, requests_yaml: str) -> None:
    """Ensure downstream exercise details preserve each authored request exactly."""
    exercises = load_unique_yaml(exercises_yaml)
    requests = load_unique_yaml(requests_yaml)
    if not isinstance(exercises, list) or not isinstance(requests, list):
        raise TypeError("exercise alignment requires two YAML lists")
    exercise_handles = [item.get("handle") for item in exercises if isinstance(item, dict)]
    request_handles = [item.get("handle") for item in requests if isinstance(item, dict)]
    if exercise_handles != request_handles:
        raise ValueError(
            "exercise handles must preserve request set and order: "
            f"requests={request_handles!r}, exercises={exercise_handles!r}"
        )
    for request, exercise in zip(requests, exercises, strict=True):
        _validate_exercise_alignment_item(request, exercise)


# complexipy: ignore -- legacy complexity retained during a behavior-preserving move.
def complete_exercise_metadata(
    exercises_yaml: str,
    plan_metadata: object,
) -> str:
    """Fill wrapper metadata while keeping the exercise source self-contained.

    The compact request handoff carries no section anchors, so no internal
    provenance is derived from requests: newly generated exercises stay free
    of ``derived_from`` and rely on their own learner-visible payload.
    """
    raw = load_unique_yaml(exercises_yaml)
    if not isinstance(raw, list):
        raise TypeError("normalized exercises source must be a YAML list")
    objective_id = _first_objective_id(plan_metadata)
    bloom_by_operation = {
        "choose": "understand",
        "recall_fill": "apply",
        "match_pairs": "remember",
        "judge": "understand",
        "categorize": "analyze",
        "build": "apply",
        "find_fix": "analyze",
        "speak": "apply",
        "write": "apply",
    }
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("normalized exercises must contain mappings")
        if "handle" not in item and isinstance(item.get("id"), str):
            # The normalizer sometimes uses the transport-friendly ``id``
            # field for the exercise handle.  The source contract names this
            # field ``handle``; the value is otherwise the same stable ID.
            item["handle"] = item.pop("id")
        if "op" not in item and isinstance(item.get("operation"), str):
            item["op"] = item.pop("operation")
        if "prompt_md" not in item and isinstance(item.get("prompt"), str):
            item["prompt_md"] = item.pop("prompt")
        if objective_id and not isinstance(item.get("objective"), str):
            item["objective"] = objective_id
        operation = item.get("op")
        if isinstance(operation, str) and not isinstance(item.get("bloom"), str):
            item["bloom"] = bloom_by_operation.get(operation, "understand")
        _repair_exercise_item(item)
        _normalize_yaml_strings(item)
    return cast(str, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))


def normalize_compiler_unsafe_markdown(lesson_md: str) -> str:
    """Keep rich prose while removing Markdown constructs the source parser rejects."""
    # Models often number each turn of one conversation (``office-01``,
    # ``office-02``).  The compiler uses dialogue_id to group turns, so keep
    # the scene stem and remove only a trailing turn number.
    lesson_md = re.sub(
        r"(\bdialogue_id\s*=\s*['\"])([A-Za-z0-9][A-Za-z0-9_-]*?)-\d+(['\"])",
        r"\1\2\3",
        lesson_md,
    )
    # The source contract represents every heading as a typed lesson section;
    # nested textbook subheads remain visible as bold labels instead.
    lesson_md = re.sub(r"(?m)^#{3,6}\s+(.+)$", r"**\1**", lesson_md)
    # A wrong-example marker is sometimes escaped inside a bold label, e.g.
    # ``**\*I morgen jeg jobber hjemme.**``. Pandoc then nests a literal
    # asterisk inside ``StrongSpan``, whose exported leaf values must be plain
    # text. Drop only escaped Markdown punctuation; the surrounding label and
    # the learner-facing ``Not:`` context remain intact.
    lesson_md = re.sub(r"\\([*_`])", "", lesson_md)
    # Code spans are plain text in the lesson schema; strip Markdown marker
    # characters from an erroneous example such as ``*word`` instead of
    # letting the parser create an invalid CodeSpan.
    lesson_md = _normalize_inline_markdown(lesson_md)
    # A prose author occasionally opens a bold label and forgets the closing
    # marker. Remove only unbalanced strong markers so valid emphasis remains
    # available to the normalized lesson.
    lesson_md = _remove_unbalanced_strong_markers(lesson_md)
    lesson_md = re.sub(
        r"(?m)(Activity request:\s*)([A-Za-z0-9][A-Za-z0-9_-]*)",
        lambda match: match.group(1) + match.group(2).replace("_", "-"),
        lesson_md,
    )
    lesson_md = _normalize_section_objective_attributes(lesson_md)
    # Pandoc exposes two trailing spaces as LineBreak, which the authoring span
    # contract intentionally rejects. A soft break keeps the visible text.
    lesson_md = re.sub(r"[ ]{2,}\n", "\n", lesson_md)
    # Preserve a common retrieval sentence if the converter accidentally
    # swallowed the inline code delimiters around the target terms.
    return re.sub(
        r"After `kan,\s*use the\s+without å`",
        "After `kan`, use the infinitive without `å`",
        lesson_md,
    )


def failed_exercise_handles(report: dict[str, Any]) -> list[str]:
    """Extract only exercise IDs that can be repaired without touching prose."""
    handles: set[str] = set()
    for key in ("missing", "ambiguous", "unanswerable", "context_dependent"):
        values = report.get(key)
        if isinstance(values, list):
            handles.update(value for value in values if isinstance(value, str))
    mismatches = report.get("mismatches")
    if isinstance(mismatches, list):
        handles.update(item["id"] for item in mismatches if isinstance(item, dict) and isinstance(item.get("id"), str))
    return sorted(handles)


# complexipy: ignore -- legacy complexity retained during a behavior-preserving move.
def merge_failed_exercises(
    current_yaml: str,
    replacement_yaml: str,
    failed_handles: list[str],
) -> str:
    """Splice repaired exercise items while preserving all unaffected items."""
    current = load_unique_yaml(current_yaml)
    replacements = load_unique_yaml(replacement_yaml)
    if not isinstance(current, list) or not isinstance(replacements, list):
        raise TypeError("exercise repair requires two YAML lists")
    replacement_by_handle = {
        item.get("handle"): item
        for item in replacements
        if isinstance(item, dict) and isinstance(item.get("handle"), str)
    }
    replacement_handles: list[str] = [
        item["handle"] for item in replacements if isinstance(item, dict) and isinstance(item.get("handle"), str)
    ]
    if len(replacement_handles) != len(set(replacement_handles)):
        raise ValueError("exercise repair returned duplicate handles")
    unexpected = sorted(set(replacement_handles) - set(failed_handles))
    if unexpected:
        raise ValueError(f"exercise repair returned unexpected handles: {unexpected}")
    missing = [handle for handle in failed_handles if handle not in replacement_by_handle]
    if missing:
        raise ValueError(f"exercise repair omitted failed handles: {missing}")
    failed = set(failed_handles)
    merged: list[Any] = []
    current_by_handle: dict[str, dict[str, Any]] = {}
    for item in current:
        if not isinstance(item, dict):
            raise TypeError("current exercises must contain mappings")
        handle = item.get("handle")
        if isinstance(handle, str):
            current_by_handle[handle] = item
        merged.append(replacement_by_handle[handle] if handle in failed else item)
    _validate_unaffected_exercise_items(merged, current_by_handle, failed)
    return cast(str, yaml.safe_dump(merged, allow_unicode=True, sort_keys=False))


def unaffected_exercise_hashes(exercises_yaml: str, failed_handles: list[str]) -> dict[str, str]:
    """Hash every non-repaired source item for final artifact integrity."""
    items = load_unique_yaml(exercises_yaml)
    if not isinstance(items, list):
        raise TypeError("exercise hash check requires a YAML list")
    failed = set(failed_handles)
    hashes: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("handle"), str):
            raise TypeError("exercise hash check requires handles on every item")
        handle = item["handle"]
        if handle not in failed:
            hashes[handle] = _canonical_item_hash(item)
    return hashes


def _lesson_edit_replacements(
    draft_text: str, edits: list[LessonDraftEdit]
) -> tuple[list[tuple[int, int, str, str]], list[str]]:
    """Validate lesson edit anchors and collect exact replacement spans."""
    replacements: list[tuple[int, int, str, str]] = []
    errors: list[str] = []
    for edit in edits:
        if edit.old_text == edit.new_text:
            continue
        if edit.old_text == draft_text:
            errors.append(f"{edit.finding_ref!r} returned a complete-draft replacement")
            continue
        positions = [position for position in range(len(draft_text)) if draft_text.startswith(edit.old_text, position)]
        if len(positions) != 1:
            errors.append(
                f"{edit.finding_ref!r} requires a unique old_text anchor, found {len(positions)} occurrence(s)"
            )
            continue
        replacements.extend(
            (position, position + len(edit.old_text), edit.new_text, edit.finding_ref) for position in positions
        )
    return replacements, errors


def _overlap_errors(replacements: list[tuple[int, int, str, str]], message_prefix: str) -> list[str]:
    """Return diagnostics for overlapping exact replacements."""
    return [
        f"{message_prefix}: {previous[3]!r} and {current[3]!r}"
        for previous, current in zip(replacements, replacements[1:], strict=False)
        if current[0] < previous[1]
    ]


def _normalization_finding_refs(
    review: NormalizationPreservationReview,
) -> list[tuple[str, PreservationFinding]]:
    """Index normalization findings by category and occurrence."""
    finding_refs: list[tuple[str, PreservationFinding]] = []
    category_counts: dict[str, int] = {}
    for finding in review.findings:
        category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
        finding_refs.append((f"{finding.category}:{category_counts[finding.category]}", finding))
    return finding_refs


def _apply_normalization_edits(
    values: dict[str, str],
    replacements: dict[str, list[tuple[int, int, str, str]]],
    edits: list[NormalizationTextEdit],
) -> list[str]:
    """Validate normalization anchors, apply valid spans, and return errors."""
    errors: list[str] = []
    for edit in edits:
        current = values[edit.artifact]
        if edit.old_text == current:
            errors.append(f"{edit.finding_ref!r} returned a complete-field replacement")
            continue
        first = current.find(edit.old_text)
        second = current.find(edit.old_text, first + len(edit.old_text)) if first >= 0 else -1
        occurrences = int(first >= 0) + int(second >= 0)
        if occurrences != 1:
            errors.append(
                f"normalization edit {edit.finding_ref!r} requires a unique old_text "
                f"anchor, found {occurrences} occurrence(s)"
            )
            continue
        replacements[edit.artifact].append((first, first + len(edit.old_text), edit.new_text, edit.finding_ref))
    for artifact, artifact_replacements in replacements.items():
        ordered = sorted(artifact_replacements, key=lambda item: (item[0], item[1]))
        errors.extend(_overlap_errors(ordered, f"normalization edits overlap for {artifact}"))
        for start, end, new_text, _finding_ref in reversed(ordered):
            values[artifact] = values[artifact][:start] + new_text + values[artifact][end:]
    return errors


def _validate_forbidden_lesson_lines(lesson_md: str) -> None:
    """Reject Markdown constructs outside the typed lesson source contract."""
    for line in lesson_md.splitlines():
        stripped = line.lstrip()
        if line.startswith(("  :::", "\t:::")):
            raise ValueError("typed blocks cannot be nested inside Markdown list items; close the list first")
        if stripped.startswith(">"):
            raise ValueError("lesson source cannot contain Markdown blockquotes; use a paragraph or callout")
        if stripped.startswith("```"):
            raise ValueError("lesson source cannot contain Markdown fences; use a typed content block")
        if re.match(r"^\|.*\|\s*$", stripped):
            raise ValueError("lesson source cannot contain bare pipe tables; use a typed col_langs table")


def _validate_rule_blocks(lesson_md: str) -> None:
    """Reject rule blocks containing more than one paragraph."""
    rule_blocks = re.findall(
        r"(?ms)^:::\s*(?:\{\.rule\}|rule)\s*\n(?P<body>.*?)^:::\s*$",
        lesson_md,
    )
    for body in rule_blocks:
        if re.search(r"\n\s*\n", body):
            raise ValueError("rule block must contain exactly one paragraph")


def _validate_reading_attributes(lesson_md: str) -> None:
    """Require a non-empty translation attribute on every reading block."""
    reading_attrs = re.findall(
        r"(?m)^:::\s*\{\.reading(?P<attrs>[^}]*)\}\s*$",
        lesson_md,
    )
    for attrs in reading_attrs:
        match = re.search(
            r"\btranslation\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)",
            attrs,
        )
        if match is None or match.group("value").strip("\"'").strip() == "":
            raise ValueError("reading block must have a non-empty translation attribute")


# complexipy: ignore -- legacy complexity retained during a behavior-preserving move.
def _validate_compiler_blocks(compiler_safe_lesson: str) -> None:
    """Run the compiler against normalized typed blocks and exercise markers."""
    frontmatter_match = re.match(r"(?s)^---\s*\n.*?\n---\s*\n(?P<body>.*)\Z", compiler_safe_lesson)
    if frontmatter_match is None:
        return
    doc = parse_markdown(frontmatter_match.group("body"))
    for element in doc.content:
        if isinstance(element, panflute.Div) and "examples" in element.classes:
            bullet_lists = [child for child in element.content if isinstance(child, panflute.BulletList)]
            if len(bullet_lists) != 1:
                raise ValueError(f"examples div must contain exactly one bullet list, found {len(bullet_lists)}.")
        if isinstance(element, panflute.Para):
            marker = "".join(
                getattr(child, "text", " ") if not isinstance(child, panflute.Space) else " "
                for child in element.content
            )
            if re.fullmatch(r"\{\{exercise:\s*[^}\s]+\}\}", marker):
                continue
        convert_blocks([element], [], "nb")


def _validate_examples_blocks(lesson_md: str) -> None:
    """Require each examples block to contain alternating no:/en: items."""
    blocks = re.findall(
        r"(?ms)^:::\s+examples\s*\n(?P<body>.*?)^:::\s*$",
        lesson_md,
    )
    for block in blocks:
        labels = [
            match.group(1) for line in block.splitlines() if (match := re.match(r"^\s*-\s+(no|en):(?:\s|$)", line))
        ]
        if not labels:
            raise ValueError("examples block must contain no:/en: pairs")
        if any(labels[index : index + 2] != ["no", "en"] for index in range(0, len(labels), 2)):
            raise ValueError(
                "examples block must contain one `no` item followed by one `en` item "
                "for every example, including incorrect examples"
            )


def _validate_exercise_alignment_item(request: object, exercise: object) -> None:
    """Validate one generated exercise against its compact request."""
    if not isinstance(request, dict) or not isinstance(exercise, dict):
        raise TypeError("exercise alignment entries must be mappings")
    handle = str(request.get("handle", ""))
    if exercise.get("objective") != request.get("objective_ref"):
        raise ValueError(f"exercise {handle!r} changed its objective reference")
    if exercise.get("bloom") != request.get("bloom"):
        raise ValueError(f"exercise {handle!r} changed its Bloom level")
    operation = exercise.get("op")
    policy = operation_policy(str(operation))
    if policy is None:
        raise ValueError(f"exercise {handle!r} uses unsupported operation {operation!r}")
    evidence_route = request.get("evidence_route")
    if evidence_route is not None:
        allowed_operations = evidence_route_operations(str(evidence_route))
        if str(operation) not in allowed_operations:
            raise ValueError(
                f"exercise {handle!r} uses operation {operation!r} for evidence_route "
                f"{evidence_route!r}; allowed={list(allowed_operations)!r}"
            )
    if exercise.get("bloom") not in policy.bloom_levels:
        raise ValueError(
            f"exercise {handle!r} uses operation {operation!r} at incompatible "
            f"Bloom {exercise.get('bloom')!r}; allowed={list(policy.bloom_levels)!r}"
        )


def _validate_unaffected_exercise_items(
    merged: list[Any], current_by_handle: dict[str, dict[str, Any]], failed: set[str]
) -> None:
    """Reject changes to exercise items outside the requested repair scope."""
    for item in merged:
        if not isinstance(item, dict):
            continue
        handle = item.get("handle")
        if (
            isinstance(handle, str)
            and handle not in failed
            and _canonical_item_hash(item) != _canonical_item_hash(current_by_handle[handle])
        ):
            raise ValueError(f"exercise repair mutated unaffected handle {handle!r}")


def _canonical_item_hash(item: dict[str, Any]) -> str:
    """Hash one exercise item for failed-handle-only repair assertions."""
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_exercise_request_value(value: object, objective_ids: set[str]) -> ExerciseRequest:
    """Validate one compact request against route and objective policy."""
    if isinstance(value, dict):
        legacy_fields = sorted(K_RICH_LEGACY_REQUEST_FIELDS.intersection(value))
        if legacy_fields:
            raise ValueError(
                f"exercise request {value.get('handle', '#unknown')!r} carries verbose "
                f"legacy field(s) {legacy_fields}; the compact handoff allows only "
                f"{list(K_RICH_REQUEST_FIELDS)}"
            )
    request = ExerciseRequest.model_validate(value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", request.handle):
        raise ValueError(f"invalid exercise request handle {request.handle!r}")
    if not evidence_route_operations(request.evidence_route):
        raise ValueError(f"exercise request {request.handle!r} has unknown evidence_route {request.evidence_route!r}")
    allowed_bloom_levels = evidence_route_bloom_levels(request.evidence_route)
    if request.bloom not in allowed_bloom_levels:
        raise ValueError(
            f"exercise request {request.handle!r} uses Bloom {request.bloom!r} "
            f"incompatible with evidence_route {request.evidence_route!r}; "
            f"allowed={list(allowed_bloom_levels)!r}"
        )
    if objective_ids and request.objective_ref not in objective_ids:
        raise ValueError(f"exercise request {request.handle!r} references unknown objective {request.objective_ref!r}")
    return request


def _exercise_intent_findings(requests_yaml: str) -> list[str]:
    """Deterministically reject vague or operation-bearing checkpoint tokens.

    This gate runs after the structural handoff validation and before the
    exercise author is invoked. It is fully deterministic: it rejects an
    evidence statement too thin to describe observable evidence, any
    backward-looking instruction inside the token, and any leaked operation
    payload field. Semantic quality stays with the model reviewers.
    """
    try:
        raw = load_unique_yaml(requests_yaml)
    except (ValueError, TypeError, yaml.YAMLError) as exc:
        return [f"exercise request handoff was not valid YAML: {exc}"]
    if not isinstance(raw, list) or not raw:
        return ["exercise request handoff must be a non-empty YAML list"]
    findings: list[str] = []
    seen_handles: set[str] = set()
    for index, value in enumerate(raw, start=1):
        findings.extend(_exercise_intent_findings_for_value(index, value, seen_handles))
    return findings


def _exercise_intent_findings_for_value(index: int, value: object, seen_handles: set[str]) -> list[str]:
    """Return deterministic intent findings for one raw request value."""
    if not isinstance(value, dict):
        return [f"exercise request {index} is not a mapping"]
    findings: list[str] = []
    handle = value.get("handle")
    label = handle if isinstance(handle, str) and handle else f"#{index}"
    if isinstance(handle, str):
        if handle in seen_handles:
            findings.append(f"request {label!r} duplicates an earlier handle")
        seen_handles.add(handle)
    legacy_fields = sorted(K_RICH_LEGACY_REQUEST_FIELDS.intersection(value))
    if legacy_fields:
        findings.append(
            f"request {label!r} carries verbose legacy field(s) {legacy_fields}; "
            f"the compact handoff allows only {list(K_RICH_REQUEST_FIELDS)}"
        )
    evidence = value.get("evidence")
    if not isinstance(evidence, str) or len(evidence.strip()) < K_RICH_INTENT_MIN_EVIDENCE_LENGTH:
        findings.append(
            f"request {label!r} has a vague evidence statement: state the observable learner evidence in at least "
            f"{K_RICH_INTENT_MIN_EVIDENCE_LENGTH} characters"
        )
    elif scan_unresolved_references({"evidence": evidence}):
        findings.append(
            f"request {label!r} evidence contains a backward-looking reference; "
            "the checkpoint must name observable evidence without referring to earlier lesson material"
        )
    leaked_fields = sorted(K_RICH_INTENT_OPERATION_FIELDS.intersection(value))
    if leaked_fields:
        findings.append(
            f"request {label!r} leaks operation fields {leaked_fields}; the handoff "
            "must stay operation-free until the exercise author runs"
        )
    return findings


def _normalize_yaml_strings(value: object, *, field_name: str | None = None) -> object:
    """Clean compiler-unsafe inline Markdown inside nested exercise values."""
    if isinstance(value, dict):
        for key, child in value.items():
            value[key] = _normalize_yaml_strings(child, field_name=key)
        return value
    if isinstance(value, list):
        return [_normalize_yaml_strings(child, field_name=field_name) for child in value]
    if isinstance(value, str):
        normalized = _normalize_inline_markdown(value)
        # JSON/YAML transport responses sometimes encode paragraph breaks as
        # the two literal characters backslash-n. Inline exercise fields must
        # be a single compiler paragraph, so turn those escapes into ordinary
        # spacing before Pandoc sees them.
        normalized = re.sub(r"\\n", " ", normalized)
        normalized = re.sub(r"[ \t\r\n]+", " ", normalized)
        if field_name == "text_md":
            # Recall-fill spans deliberately retain a boundary space around a
            # blank.  The compiler uses that space to keep adjacent words
            # separate, so do not strip it while flattening model line breaks.
            normalized = re.sub(r"\s*\n\s*", " ", normalized)
            normalized = re.sub(r"^(\s*)(\d+)[\.)](\s+)", r"\g<1>Item \2:\3", normalized)
        elif field_name in {"prompt_md", "explanation_md", "sentence_md", "stem_md"}:
            # These fields are parsed as one inline paragraph by the source
            # compiler. Preserve the words while removing model-added list
            # breaks that would otherwise create multiple blocks.
            normalized = re.sub(r"\s*\n\s*", " ", normalized).strip()
            # A leading ``1.`` or ``1)`` makes Pandoc parse the whole inline
            # field as an ordered list. Keep the human-readable number while
            # using a labelled form that remains inline.
            normalized = re.sub(r"^(\s*)(\d+)[\.)](\s+)", r"\g<1>Item \2:\3", normalized)
            # The source contract uses [BLANK] for inline answer slots. Runs of
            # underscores are parsed as forbidden Markdown emphasis markers.
            normalized = re.sub(r"_{2,}", "[BLANK]", normalized)
        return normalized
    return value


def _normalize_inline_markdown(text: str) -> str:
    """Remove forbidden marker characters from inline code spans."""
    # Exercise inline fields are parsed as plain learner-facing text by the
    # source schema.  Markdown code spans therefore cannot be preserved: the
    # backticks themselves are rejected by ``TextSpan`` even when their
    # contents contain no other Markdown markers.  Keep the visible content,
    # while stripping all forbidden marker characters from the former span.
    normalized = re.sub(
        r"`([^`\n]*)`",
        lambda match: match.group(1).replace("*", "").replace("_", ""),
        text,
    )
    # A model can emit an unmatched delimiter when a phrase wraps across a
    # line or the closing delimiter is omitted.  There is no valid schema
    # representation for a dangling code span, so remove the remaining
    # delimiters while preserving the visible words.
    return normalized.replace("`", "")


def _remove_unbalanced_strong_markers(text: str) -> str:
    """Remove malformed line-local ``**`` pairs without flattening valid prose."""
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.count("**") % 2:
            line = line.replace("**", "")
        lines.append(line)
    return "".join(lines)


def _normalize_section_objective_attributes(text: str) -> str:
    """Keep model/contrast sections within the one-objective source contract."""
    pattern = re.compile(r"(?m)^(## .+\{[^}\n]*role=(?:model|contrast)\s+objectives=)([^}\s]+)(\})")

    def replace(match: re.Match[str]) -> str:
        objective_ids = match.group(2).split(",")
        return f"{match.group(1)}{objective_ids[0]}{match.group(3)}"

    return pattern.sub(replace, text)


def _first_objective_id(plan_metadata: object) -> str | None:
    """Read the first approved objective id for exercise wrapper completion."""
    if not isinstance(plan_metadata, dict):
        return None
    objectives = plan_metadata.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        return None
    first = objectives[0]
    if isinstance(first, dict):
        value = first.get("id")
        if isinstance(value, str):
            return value
    return None


__all__ = [
    "apply_lesson_draft_edits",
    "apply_normalization_edits",
    "complete_exercise_metadata",
    "extract_exercise_requests",
    "failed_exercise_handles",
    "merge_failed_exercises",
    "normalize_compiler_unsafe_markdown",
    "unaffected_exercise_hashes",
    "validate_exercise_alignment",
    "validate_exercise_requests",
    "validate_normalized_lesson_shape",
]
