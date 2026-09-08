"""Entry point: `audit_exercise_source` validates parsed authored exercise data.

`audit_exercise_source` owns deterministic lesson-source policy. File access,
strict YAML parsing, and compiler validation are coordinated by the application
operation at ``application.operations.audit_source``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from typing import Literal

from lesson_builder.domain.lesson.models.source_audit import K_SOURCE_AUDIT_EXERCISES_FILE
from lesson_builder.domain.lesson.models.source_audit import K_SOURCE_AUDIT_LESSON_FILE
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.models.source_audit import MechanicalBuiltAnswer
from lesson_builder.domain.lesson.models.source_audit import MechanicalFinding
from lesson_builder.domain.lesson.models.source_audit import ReviewArtifact
from lesson_builder.domain.lesson.validation.standalone_references import scan_unresolved_references

K_SOURCE_AUDIT_MARKER_RE = re.compile(r"(?m)^\{\{exercise:\s*([^}\s]+)\}\}\s*$")
K_SOURCE_AUDIT_MARKER_FRAGMENT_RE = re.compile(r"\{\{exercise:")
K_SOURCE_AUDIT_TERMINAL_PUNCTUATION = frozenset(".!?…")
K_SOURCE_AUDIT_REQUIRED_ENGLISH_RE = re.compile(
    r"\b(?:write|respond|answer|reply|produce|use)\b[^.\n]{0,100}\bin English\b|\bin English\b[^.\n]{0,100}\b(?:response|answer|reply)\b",
    re.IGNORECASE,
)
K_SOURCE_AUDIT_NORWEGIAN_EXAMPLE_BUT_RE = re.compile(r"^-\s+no:\s+.*\bbut\b.*$", re.IGNORECASE)
K_SOURCE_AUDIT_UNSUPPORTED_FIND_FIX_RE = re.compile(
    r"\b(?:write|type|enter|submit|reorder|rearrange|explain|give|provide)\b[^.\n]{0,100}\b(?:repair|replacement|correction|corrected answer|fixed sentence)\b"
    r"|\b(?:repair|replace|move|remove|rewrite|restore|correct|fix|change|edit)\s+(?:only\s+)?(?:the|a|an|your|this|that|it|each|one|token|sentence|word)\b"
    r"|\bfind\s+and\s+(?:fix|repair|change|replace|rewrite|edit)\b"
    r"|\b(?:select|find|identify|locate)\b[^.\n]{0,80}\band\s+(?:fix|repair|change|replace|rewrite|edit)\b"
    r"|\b(?:explain|provide|state|give)\b[^.\n]{0,100}\b(?:why|the corrected|the repair|the correction|the meaning|the correct (?:form|version|sentence|answer))\b",
    re.IGNORECASE,
)
K_SOURCE_AUDIT_FIND_FIX_NON_IMPERATIVE_RE = re.compile(
    r"(?:\bdo\s+not\b|\bdon['’]t\b|\bnever\b|\bnot\s+to\b|\b(?:helps|shows|lets|allows)\s+you(?:\s+to)?\b)\s*$",
    re.IGNORECASE,
)
K_SOURCE_AUDIT_BROAD_SPEAK_RE = re.compile(
    r"\b(?:say something|speak about|talk about|describe|tell us|answer freely|respond freely|make up)\b",
    re.IGNORECASE,
)
K_SOURCE_AUDIT_EXACT_SPEAK_CUE_RE = re.compile(
    r"\b(?:say|read|repeat)(?:\s+the\s+following)?\s+exactly\b",
    re.IGNORECASE,
)
K_SOURCE_AUDIT_MIN_OPTIONS = 2
K_SOURCE_AUDIT_MIN_QUOTED_VALUE_LENGTH = 2
K_SOURCE_AUDIT_HIDDEN_RESTATEMENT_FIELDS = frozenset(
    {
        "judge_prompt",
        "criteria",
        "derived_from",
        "feedback",
        "why",
        "correct",
        "answer_index",
        "answer_id",
        "answer_order",
        "error_token_id",
        "is_correct",
        "pairs",
        "bucket_id",
        "audio_target",
        "handle",
        "op",
        "objective",
        "bloom",
    }
)


def audit_exercise_source(
    lesson_text: str,
    exercise_items: list[dict[str, Any]] | None,
    *,
    source_validated: bool = False,
    initial_findings: Iterable[MechanicalFinding] = (),
    parsed_findings: Iterable[MechanicalFinding] = (),
    validation_findings: Iterable[MechanicalFinding] = (),
) -> MechanicalAudit:
    """Audit lesson text and parsed exercises without filesystem or providers.

    ``exercise_items`` is the already-parsed strict YAML document. The caller
    supplies ``source_validated=True`` when compiler validation succeeded so
    the audit can preserve the existing build-answer gate. ``initial_findings``
    carries source-read errors and is emitted before policy findings. Parsed
    document and compiler-validation findings are inserted at their original
    audit boundaries so compatibility callers keep the existing order.
    """
    findings = list(initial_findings)
    marker_handles = _marker_handles(lesson_text, findings)
    _audit_norwegian_example_language(lesson_text, findings)
    _audit_typed_example_outer_quote_wrappers(lesson_text, findings)
    findings.extend(parsed_findings)
    raw_items = exercise_items
    exercise_handles = _raw_handles(raw_items, findings)
    _compare_handles(exercise_handles, marker_handles, findings)
    findings.extend(validation_findings)

    built_answers: list[MechanicalBuiltAnswer] = []
    if raw_items is not None and source_validated:
        for index, raw_item in enumerate(raw_items):
            handle = exercise_handles[index] if index < len(exercise_handles) else f"item-{index}"
            _audit_operation_shape(raw_item, handle, findings)
            _audit_standalone_references(raw_item, handle, findings)
            if raw_item.get("op") == "build":
                built = _audit_build(raw_item, handle, findings)
                if built is not None:
                    built_answers.append(built)

    status = _status_for(findings)
    return MechanicalAudit(
        status=status,
        exercise_handles=exercise_handles,
        marker_handles=marker_handles,
        built_answers=built_answers,
        findings=findings,
    )


def _marker_handles(text: str, findings: list[MechanicalFinding]) -> list[str]:
    """Extract exercise markers and reject malformed marker fragments."""
    handles = K_SOURCE_AUDIT_MARKER_RE.findall(text)
    fragments = K_SOURCE_AUDIT_MARKER_FRAGMENT_RE.findall(text)
    if len(fragments) != len(handles):
        findings.append(
            MechanicalFinding(
                code="exercise-marker-malformed",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_LESSON_FILE,
                location="exercise markers",
                evidence=f"{len(fragments)} marker fragment(s), {len(handles)} valid marker(s)",
                explanation="Every exercise marker must use the compiler's exact line format.",
            )
        )
    _find_duplicate_values(
        handles,
        code="exercise-marker-duplicate",
        artifact=K_SOURCE_AUDIT_LESSON_FILE,
        location="exercise markers",
        explanation="A marker handle occurs more than once and cannot identify one exercise unambiguously.",
        findings=findings,
        severity="blocking",
    )
    return handles


def _audit_norwegian_example_language(text: str, findings: list[MechanicalFinding]) -> None:
    """Block standalone English ``but`` on learner-facing Norwegian examples."""
    for line_number, line in enumerate(text.splitlines(), start=1):
        if K_SOURCE_AUDIT_NORWEGIAN_EXAMPLE_BUT_RE.match(line):
            _finding(
                findings,
                "norwegian-example-english-conjunction",
                "blocking",
                K_SOURCE_AUDIT_LESSON_FILE,
                f"line {line_number}",
                line,
                "A Norwegian example line must use the Bokmål conjunction `men`, not English `but`.",
            )


def _audit_typed_example_outer_quote_wrappers(text: str, findings: list[MechanicalFinding]) -> None:
    """Reject matching ASCII wrappers that are YAML-style syntax, not content.

    The source parser preserves typed example text, so silently removing these
    characters would change authored content. The narrow pair check catches the
    accidental wrapper pattern used on both sides of a bilingual item while
    allowing apostrophes, internal quotes, guillemets, and other intentional
    visible punctuation to pass through.
    """
    in_examples = False
    pending_no: tuple[int, str] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        in_examples, pending_no, pair = _typed_example_transition(line.strip(), line_number, in_examples, pending_no)
        if pair is None:
            continue
        no_line, quote = pair
        _finding(
            findings,
            "typed-example-outer-quote-wrapper",
            "blocking",
            K_SOURCE_AUDIT_LESSON_FILE,
            f"lines {no_line}-{line_number}",
            f"matching outer {quote!r} wrappers on paired no:/en: values",
            "Typed example labels are Markdown syntax; matching outer ASCII quote characters "
            "would become learner-visible text. Remove accidental wrappers, or use intentional "
            "internal/typographic quotation punctuation.",
        )


def _typed_example_transition(
    line: str,
    line_number: int,
    in_examples: bool,
    pending_no: tuple[int, str] | None,
) -> tuple[bool, tuple[int, str] | None, tuple[int, str] | None]:
    """Advance typed-example state and return a detected wrapper pair."""
    if re.match(r"^:::\s*(?:\{?\s*)?(?:example|examples)\b", line):
        return True, None, None
    if in_examples and line == ":::":
        return False, None, None
    if not in_examples:
        return in_examples, pending_no, None
    match = re.match(r"^-\s+(no|en):\s*(\S.*)?$", line)
    if match is None:
        return in_examples, pending_no, None
    label, raw_value = match.groups()
    wrapper = _matching_outer_ascii_quote((raw_value or "").strip())
    if label == "no":
        return in_examples, (line_number, wrapper[0]) if wrapper is not None else None, None
    if pending_no is None:
        return in_examples, None, None
    no_line, no_quote = pending_no
    if wrapper is None or wrapper[0] != no_quote:
        return in_examples, None, None
    return in_examples, None, (no_line, no_quote)


def _matching_outer_ascii_quote(value: str) -> tuple[str, str] | None:
    """Return a matching ASCII wrapper unless its body contains that quote."""
    if len(value) < K_SOURCE_AUDIT_MIN_QUOTED_VALUE_LENGTH or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None
    body = value[1:-1]
    if value[0] in body:
        return None
    return value[0], body


def _raw_handles(raw_items: list[dict[str, Any]] | None, findings: list[MechanicalFinding]) -> list[str]:
    """Collect source handles and report missing or duplicate handles."""
    if raw_items is None:
        return []
    handles: list[str] = []
    for index, item in enumerate(raw_items):
        value = item.get("handle")
        if not isinstance(value, str) or not value.strip():
            findings.append(
                MechanicalFinding(
                    code="exercise-handle-missing",
                    severity="blocking",
                    artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                    location=f"item[{index}].handle",
                    evidence=repr(value),
                    explanation="Every exercise needs one non-empty stable handle.",
                )
            )
            handles.append(f"item-{index}")
        else:
            handles.append(value.strip())
    _find_duplicate_values(
        handles,
        code="exercise-handle-duplicate",
        artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
        location="handle",
        explanation="Exercise handles must be unique across the package.",
        findings=findings,
        severity="blocking",
    )
    return handles


def _compare_handles(
    exercise_handles: list[str],
    marker_handles: list[str],
    findings: list[MechanicalFinding],
) -> None:
    """Compare lesson marker set and YAML handle set and preserve order evidence."""
    exercise_set = set(exercise_handles)
    marker_set = set(marker_handles)
    missing = [handle for handle in exercise_handles if handle not in marker_set]
    extra = [handle for handle in marker_handles if handle not in exercise_set]
    if missing or extra:
        findings.append(
            MechanicalFinding(
                code="exercise-marker-handle-mismatch",
                severity="blocking",
                artifact=K_SOURCE_AUDIT_LESSON_FILE,
                location="exercise markers",
                evidence=f"missing={missing!r}; extra={extra!r}",
                explanation="Every authored exercise must have exactly one lesson marker.",
            )
        )
    if not missing and not extra and exercise_handles != marker_handles:
        findings.append(
            MechanicalFinding(
                code="exercise-marker-order-drift",
                severity="minor",
                artifact=K_SOURCE_AUDIT_LESSON_FILE,
                location="exercise markers",
                evidence=f"YAML={exercise_handles!r}; lesson={marker_handles!r}",
                explanation=(
                    "The lesson and YAML display orders differ. This may be intentional, "
                    "but it should be visible to the semantic reviewer."
                ),
            )
        )


def _audit_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Check deterministic identifiers and answer containers for one operation."""
    operation = raw.get("op")
    handlers = {
        "choose": _audit_choose_operation_shape,
        "recall_fill": _audit_recall_fill_operation_shape,
        "match_pairs": _audit_match_pairs_operation_shape,
        "categorize": _audit_categorize_operation_shape,
        "judge": _audit_judge_operation_shape,
        "find_fix": _audit_find_fix_operation_shape,
        "speak": _audit_speak_operation_shape,
        "write": _audit_write_operation_shape,
    }
    handler = handlers.get(operation) if isinstance(operation, str) else None
    if handler is not None:
        handler(raw, handle, findings)


def _audit_choose_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit choose options and the single correct answer."""
    options = raw.get("options")
    if not isinstance(options, list) or len(options) < K_SOURCE_AUDIT_MIN_OPTIONS:
        _finding(
            findings,
            "choose-options-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(options),
            "A choose exercise needs at least two option mappings.",
        )
        return
    _audit_mapping_ids(options, "id", handle, "choose option", findings)
    _audit_text_values(options, "text", handle, "choose option", findings)
    _audit_nonempty_texts(options, "text", handle, "choose option", findings)
    _audit_sentence_initial_options(raw, handle, findings)
    correct = [item for item in options if isinstance(item, dict) and item.get("correct") is True]
    if len(correct) != 1:
        _finding(
            findings,
            "choose-answer-cardinality",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(len(correct)),
            "A choose exercise must expose exactly one correct option.",
        )


def _audit_recall_fill_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit recall audio, typed blanks, and rendered punctuation."""
    audio_target = raw.get("audio_target")
    if not isinstance(audio_target, str) or not audio_target.strip():
        _finding(
            findings,
            "recall-audio-target-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(audio_target),
            "A recall_fill exercise needs a non-empty Norwegian audio_target.",
        )
    segments = raw.get("segments")
    if not isinstance(segments, list):
        _finding(
            findings,
            "recall-segments-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(segments),
            "A recall_fill exercise needs an ordered segment list.",
        )
        return
    blanks = [item for item in segments if isinstance(item, dict) and "blank_id" in item]
    if not blanks:
        _finding(
            findings,
            "recall-blank-missing",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(segments),
            "A recall_fill exercise needs at least one typed blank.",
        )
    _audit_mapping_ids(blanks, "blank_id", handle, "recall blank", findings)
    for segment in segments:
        _audit_recall_segment_shape(segment, handle, findings)
    _audit_recall_rendering(segments, handle, findings)


def _audit_recall_segment_shape(segment: object, handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit one recall span or typed blank mapping."""
    if not isinstance(segment, dict):
        _finding(
            findings,
            "recall-segment-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(segment),
            "Every recall segment must be a text span or typed blank mapping.",
        )
        return
    text = segment.get("text_md")
    if isinstance(text, str) and "[BLANK]" in text:
        _finding(
            findings,
            "recall-inline-blank-marker",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            text,
            "A typed recall blank must not also be encoded as an inline marker.",
        )
    if "blank_id" not in segment:
        if not isinstance(text, str):
            _finding(
                findings,
                "recall-span-invalid",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                repr(segment),
                "A recall span must carry a string text_md value.",
            )
        return
    _audit_recall_blank_shape(segment, handle, findings)


def _audit_recall_blank_shape(segment: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit one typed recall blank's options and selected answer."""
    options = segment.get("options")
    answer_index = segment.get("answer_index")
    if not isinstance(options, list) or len(options) < K_SOURCE_AUDIT_MIN_OPTIONS:
        _finding(
            findings,
            "recall-options-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(options),
            "Each typed recall blank needs at least two options.",
        )
        return
    _audit_duplicate_strings(
        options,
        code="recall-option-duplicate",
        handle=handle,
        label="recall option",
        findings=findings,
    )
    if any(not isinstance(option, str) or not option.strip() for option in options):
        _finding(
            findings,
            "recall-option-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(options),
            "Recall options must be non-empty strings.",
        )
    if not isinstance(answer_index, int) or not 0 <= answer_index < len(options):
        _finding(
            findings,
            "recall-answer-index-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(answer_index),
            "Each recall answer_index must point to exactly one option.",
        )


def _audit_match_pairs_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit matching item lists and declared pair references."""
    for field, id_key, label in (
        ("left", "left_id", "left item"),
        ("right", "right_id", "right item"),
    ):
        _audit_match_side(raw.get(field), field, id_key, label, handle, findings)
    pairs = raw.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        _finding(
            findings,
            "match-pairs-empty",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(pairs),
            "A matching exercise needs at least one declared pair.",
        )
    if isinstance(pairs, list):
        _audit_match_pair_references(raw, pairs, handle, findings)


def _audit_match_side(
    items: object,
    field: str,
    id_key: str,
    label: str,
    handle: str,
    findings: list[MechanicalFinding],
) -> None:
    """Audit one side of a matching exercise."""
    if not isinstance(items, list) or not items:
        _finding(
            findings,
            f"match-{field}-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(items),
            f"A matching exercise needs a non-empty {field} item list.",
        )
        return
    _audit_mapping_ids(items, id_key, handle, label, findings)
    _audit_nonempty_texts(items, "text", handle, label, findings)
    _audit_text_values(items, "text", handle, label, findings)


def _audit_match_pair_references(
    raw: dict[str, Any], pairs: list[Any], handle: str, findings: list[MechanicalFinding]
) -> None:
    """Audit matching pair uniqueness and references to both item lists."""
    values = [(item.get("left_id"), item.get("right_id")) for item in pairs if isinstance(item, dict)]
    _find_duplicate_values(
        values,
        code="match-pair-duplicate",
        artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
        location=handle,
        explanation="A matching pair must not be declared twice.",
        findings=findings,
        severity="blocking",
    )
    raw_left = raw.get("left")
    raw_right = raw.get("right")
    left_ids = {
        item.get("left_id") for item in (raw_left if isinstance(raw_left, list) else []) if isinstance(item, dict)
    }
    right_ids = {
        item.get("right_id") for item in (raw_right if isinstance(raw_right, list) else []) if isinstance(item, dict)
    }
    for left_id, right_id in values:
        if left_id not in left_ids or right_id not in right_ids:
            _finding(
                findings,
                "match-pair-reference-invalid",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                repr((left_id, right_id)),
                "Every pair must reference an existing left and right item.",
            )


def _audit_categorize_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit category collections and each item's bucket reference."""
    for field, id_key, label in (
        ("buckets", "bucket_id", "category bucket"),
        ("items", "item_id", "category item"),
    ):
        items = raw.get(field)
        if isinstance(items, list):
            _audit_mapping_ids(items, id_key, handle, label, findings)
    buckets = raw.get("buckets")
    items = raw.get("items")
    _audit_category_collection(
        buckets,
        minimum=2,
        text_key="label",
        label="category bucket",
        code="categorize-buckets-invalid",
        message="A categorize exercise needs at least two buckets.",
        handle=handle,
        findings=findings,
    )
    _audit_category_collection(
        items,
        minimum=1,
        text_key="text",
        label="category item",
        code="categorize-items-empty",
        message="A categorize exercise needs at least one item.",
        handle=handle,
        findings=findings,
    )
    bucket_ids = (
        {item.get("bucket_id") for item in buckets if isinstance(item, dict)} if isinstance(buckets, list) else set()
    )
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("bucket_id") not in bucket_ids:
            _finding(
                findings,
                "categorize-reference-invalid",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                repr(item.get("bucket_id")),
                "Every category item must reference an existing bucket.",
            )


def _audit_category_collection(
    items: object,
    *,
    minimum: int,
    text_key: str,
    label: str,
    code: str,
    message: str,
    handle: str,
    findings: list[MechanicalFinding],
) -> None:
    """Audit required size and visible text for one category collection."""
    if not isinstance(items, list) or len(items) < minimum:
        _finding(
            findings,
            code,
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(items),
            message,
        )
        return
    _audit_nonempty_texts(items, text_key, handle, label, findings)
    _audit_text_values(items, text_key, handle, label, findings)


def _audit_judge_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit a judge sentence and required corrective feedback."""
    sentence = raw.get("sentence_md")
    if not isinstance(sentence, str) or not sentence.strip():
        _finding(
            findings,
            "judge-sentence-empty",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(sentence),
            "A judge exercise needs a non-empty learner sentence.",
        )
    if raw.get("is_correct") is False and not str(raw.get("feedback") or "").strip():
        _finding(
            findings,
            "judge-feedback-empty",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(raw.get("feedback")),
            "A false judge item needs corrective feedback.",
        )


def _audit_find_fix_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit find_fix token references and learner attempt surface."""
    tokens = raw.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        _finding(
            findings,
            "find-fix-tokens-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(tokens),
            "A find_fix exercise needs a non-empty token list.",
        )
        return
    _audit_mapping_ids(tokens, "token_id", handle, "find_fix token", findings)
    _audit_nonempty_texts(tokens, "text", handle, "find_fix token", findings)
    target = raw.get("error_token_id")
    token_ids = {item.get("token_id") for item in tokens if isinstance(item, dict)}
    if target not in token_ids:
        _finding(
            findings,
            "find-fix-target-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(target),
            "error_token_id must reference exactly one supplied token.",
        )
    if not str(raw.get("feedback") or "").strip():
        _finding(
            findings,
            "find-fix-feedback-empty",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(raw.get("feedback")),
            "A find_fix exercise needs corrective feedback.",
        )
    _audit_find_fix_attempt_surface(raw, handle, findings)


def _audit_speak_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit a speak target and its learner cue."""
    target = raw.get("target")
    prompt = raw.get("prompt_md")
    if not isinstance(target, str) or not target.strip():
        _finding(
            findings,
            "speak-target-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(target),
            "A speak exercise needs one non-empty Norwegian target utterance.",
        )
    if isinstance(prompt, str) and K_SOURCE_AUDIT_BROAD_SPEAK_RE.search(prompt):
        _finding(
            findings,
            "speak-target-cue-broad",
            "major",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            prompt,
            "A hidden exact speak target is not justified by a broad or open learner cue.",
        )
    if isinstance(target, str) and isinstance(prompt, str):
        normalized_target = _normalize_speak_text(target)
        normalized_prompt = _normalize_speak_text(prompt)
        if normalized_target and normalized_target not in normalized_prompt:
            _finding(
                findings,
                "speak-target-not-visible",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                target,
                "A speak target must be visible in the learner cue so the exercise does not grade a hidden sentence.",
            )
        if not K_SOURCE_AUDIT_EXACT_SPEAK_CUE_RE.search(prompt):
            _finding(
                findings,
                "speak-target-cue-exact-missing",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                prompt,
                "A visible speak target needs an explicit exact-production cue such as say exactly, read exactly, or repeat exactly.",
            )


def _audit_write_operation_shape(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Audit writing criteria, judging rubric, and response surface."""
    criteria = raw.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        _finding(
            findings,
            "write-criteria-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(criteria),
            "A writing exercise needs at least one rubric criterion.",
        )
    else:
        _audit_mapping_ids(criteria, "id", handle, "writing criterion", findings)
        _audit_nonempty_texts(criteria, "instruction", handle, "writing criterion", findings)
    if not isinstance(raw.get("judge_prompt"), str) or not raw["judge_prompt"].strip():
        _finding(
            findings,
            "write-judge-prompt-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(raw.get("judge_prompt")),
            "A writing exercise needs an explicit downstream judging rubric.",
        )
    _audit_write_attempt_surface(raw, handle, findings)


def _audit_find_fix_attempt_surface(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Reject find/fix prompts that ask for an answer the operation cannot collect."""
    prompt = raw.get("prompt_md")
    if isinstance(prompt, str) and _has_unsupported_find_fix_instruction(prompt):
        _finding(
            findings,
            "find-fix-attempt-capability",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            prompt,
            "find_fix collects only the selected erroneous token; it cannot collect a written, reordered, or explained repair.",
        )


def _has_unsupported_find_fix_instruction(prompt: str) -> bool:
    """Return whether a positive learner instruction exceeds find_fix capability."""
    for match in K_SOURCE_AUDIT_UNSUPPORTED_FIND_FIX_RE.finditer(prompt):
        sentence_start = max(prompt.rfind(mark, 0, match.start()) for mark in ".!?\n") + 1
        prefix = prompt[sentence_start : match.start()]
        if not K_SOURCE_AUDIT_FIND_FIX_NON_IMPERATIVE_RE.search(prefix):
            return True
    return False


def _audit_write_attempt_surface(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Check visible write instructions against language and rubric capabilities."""
    prompt = raw.get("prompt_md")
    criteria = raw.get("criteria")
    texts = [value for value in (prompt, raw.get("judge_prompt")) if isinstance(value, str)]
    if isinstance(criteria, list):
        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            instruction = criterion.get("instruction")
            if isinstance(instruction, str):
                texts.append(instruction)
    combined = "\n".join(texts)
    response_language = str(raw.get("response_language") or "no").lower()
    if response_language in {"no", "nb", "bokmal", "bokmål"} and K_SOURCE_AUDIT_REQUIRED_ENGLISH_RE.search(combined):
        _finding(
            findings,
            "write-response-language-conflict",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            combined,
            "response_language requires Norwegian, but the learner is instructed to produce English.",
        )


def _audit_sentence_initial_options(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    """Flag lowercase complete options only when the stem proves sentence start."""
    stem = raw.get("stem_md")
    options = raw.get("options")
    if not isinstance(stem, str) or not isinstance(options, list):
        return
    starts_sentence = stem.strip().startswith(("[BLANK]", "___"))
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("text"), str):
            continue
        text = option["text"].lstrip()
        if starts_sentence and text and text[0].islower() and _has_terminal_punctuation(text):
            _finding(
                findings,
                "choose-sentence-start-lowercase",
                "major",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                text,
                "A complete option inserted at the proven start of a sentence must begin with a capital letter.",
            )


def _audit_recall_rendering(segments: list[object], handle: str, findings: list[MechanicalFinding]) -> None:
    """Check the keyed recall sentence for punctuation introduced by a blank.

    Typed blank segments are concatenated exactly as authored by the compiler.
    A blank such as ``Forresten,`` followed by a span beginning with ``,`` is
    therefore a deterministic malformed learner target, not a stylistic review
    question. Keep this check narrow so normal sentence punctuation remains
    semantic-review territory.
    """
    rendered: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if "blank_id" in segment:
            options = segment.get("options")
            answer_index = segment.get("answer_index")
            if (
                isinstance(options, list)
                and isinstance(answer_index, int)
                and 0 <= answer_index < len(options)
                and isinstance(options[answer_index], str)
            ):
                rendered.append(options[answer_index])
            continue
        text = segment.get("text_md")
        if isinstance(text, str):
            rendered.append(text)
    sentence = "".join(rendered)
    if ",," in sentence or re.search(r"\s+[,:;.!?]", sentence):
        _finding(
            findings,
            "recall-rendered-punctuation",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            sentence,
            "The keyed recall answer renders adjacent or space-separated punctuation and is malformed for the learner.",
        )
    if any(_missing_recall_boundary(left, right) for left, right in zip(rendered, rendered[1:], strict=False)):
        _finding(
            findings,
            "recall-rendered-boundary-missing",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            sentence,
            "Adjacent recall segments concatenate without a visible word or sentence boundary.",
        )
    _audit_recall_sentence_start(segments, handle, findings)


def _missing_recall_boundary(left: str, right: str) -> bool:
    """Return whether two authored segment values need an exported separator."""
    left_value = left.rstrip()
    right_value = right.lstrip()
    if not left_value or not right_value:
        return False
    if left.endswith(" ") or right.startswith(" "):
        return False
    return (
        left_value[-1].isalnum()
        and right_value[0].isalnum()
        or (left_value[-1] in K_SOURCE_AUDIT_TERMINAL_PUNCTUATION and right_value[0].isalpha())
        or (right_value[0] in K_SOURCE_AUDIT_TERMINAL_PUNCTUATION and len(right_value) > 1 and right_value[1].isalpha())
    )


def _audit_recall_sentence_start(segments: list[object], handle: str, findings: list[MechanicalFinding]) -> None:
    """Flag lowercase keyed options at a sentence start, preserving fragments."""
    rendered = ""
    for segment in segments:
        text, answer = _recall_segment_values(segment)
        if text is not None:
            rendered += text
            continue
        if answer is None:
            continue
        _audit_recall_answer_start(answer, rendered, handle, findings)
        rendered += answer


def _recall_segment_values(segment: object) -> tuple[str | None, str | None]:
    """Return one recall span or keyed blank value for sentence-start checks."""
    if not isinstance(segment, dict):
        return None, None
    if "blank_id" not in segment:
        text = segment.get("text_md")
        return (text, None) if isinstance(text, str) else (None, None)
    options = segment.get("options")
    answer_index = segment.get("answer_index")
    if not isinstance(options, list) or not isinstance(answer_index, int):
        return None, None
    if not 0 <= answer_index < len(options) or not isinstance(options[answer_index], str):
        return None, None
    return None, options[answer_index]


def _audit_recall_answer_start(answer: str, rendered: str, handle: str, findings: list[MechanicalFinding]) -> None:
    """Record a lowercase keyed answer at a proven sentence start."""
    candidate = answer.lstrip()
    prefix = rendered.rstrip()
    at_sentence_start = not prefix or prefix[-1] in K_SOURCE_AUDIT_TERMINAL_PUNCTUATION
    if at_sentence_start and candidate and candidate[0].islower():
        _finding(
            findings,
            "recall-sentence-start-lowercase",
            "major",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            candidate,
            "A keyed option at a proven sentence start must begin with a capital letter.",
        )


def _audit_standalone_references(
    raw: dict[str, Any],
    handle: str,
    findings: list[MechanicalFinding],
) -> None:
    """Report unresolved backward references in learner-visible prompt surfaces.

    This deterministic scan supplements the semantic standalone review: it
    surfaces high-signal wording that presupposes lesson context the exercise
    payload does not restate. Findings are minor so ambiguous corpus cases are
    reported for review instead of blocking compilation.
    """
    prompt_fields: dict[str, str] = {}
    for key in ("prompt_md", "stem_md", "sentence_md"):
        value = raw.get(key)
        if isinstance(value, str):
            prompt_fields[key] = value
    recall_spans = raw.get("segments")
    if isinstance(recall_spans, list):
        for index, segment in enumerate(recall_spans):
            if not isinstance(segment, dict):
                continue
            text = segment.get("text_md")
            if isinstance(text, str):
                prompt_fields[f"segments[{index}]"] = text
    restatement_text = _learner_visible_restatement_text(raw)
    for finding in scan_unresolved_references(prompt_fields, restatement_text=restatement_text):
        _finding(
            findings,
            "standalone-reference-unresolved",
            "minor",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            f"{finding['kind']}: {finding['reference']}",
            (
                "The learner-visible prompt refers to earlier lesson material that the "
                "exercise payload does not restate; confirm the task is standalone or "
                "restate the referenced facts locally."
            ),
        )


def _learner_visible_restatement_text(raw: dict[str, Any]) -> str:
    """Collect choice-like authored material that can restate a referenced item."""
    return " ".join(_collect_restatement_parts(raw))


def _collect_restatement_parts(value: object) -> list[str]:
    """Collect learner-visible strings while skipping hidden answer metadata."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [part for child in value for part in _collect_restatement_parts(child)]
    if isinstance(value, dict):
        return [
            part
            for key, child in value.items()
            if key not in K_SOURCE_AUDIT_HIDDEN_RESTATEMENT_FIELDS
            for part in _collect_restatement_parts(child)
        ]
    return []


def _audit_build(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> MechanicalBuiltAnswer | None:
    """Assemble one build answer and audit its exact token references."""
    raw_tokens = raw.get("tokens")
    answer_order = raw.get("answer_order")
    if not isinstance(raw_tokens, list) or not isinstance(answer_order, list):
        return None
    ordered_text = _ordered_build_text(raw_tokens, answer_order, handle, findings)
    if ordered_text is None:
        return None
    answer = _join_tokens(ordered_text)
    if not answer:
        _finding(
            findings,
            "build-answer-empty",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(answer),
            "A build exercise must have a non-empty assembled answer.",
        )
        return None
    _audit_build_answer_quality(answer, ordered_text, handle, findings)
    return MechanicalBuiltAnswer(handle=handle, text=answer, token_ids=list(answer_order))


def _ordered_build_text(
    raw_tokens: list[Any], answer_order: list[Any], handle: str, findings: list[MechanicalFinding]
) -> list[str] | None:
    """Validate build token ids and return answer-order text."""
    _audit_nonempty_texts(raw_tokens, "text", handle, "build token", findings)
    token_ids = [
        item.get("token_id") for item in raw_tokens if isinstance(item, dict) and isinstance(item.get("token_id"), str)
    ]
    _find_duplicate_values(
        token_ids,
        code="build-token-id-duplicate",
        artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
        location=handle,
        explanation="Build token IDs must be unique so answer_order is unambiguous.",
        findings=findings,
        severity="blocking",
    )
    token_map = {
        item.get("token_id"): item.get("text")
        for item in raw_tokens
        if isinstance(item, dict) and isinstance(item.get("token_id"), str)
    }
    unknown = [token_id for token_id in answer_order if token_id not in token_map]
    missing = [token_id for token_id in token_ids if token_id not in answer_order]
    if unknown or missing or len(answer_order) != len(token_ids):
        _finding(
            findings,
            "build-answer-order-invalid",
            "blocking",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            f"unknown={unknown!r}; missing={missing!r}; order={answer_order!r}",
            "answer_order must contain every token ID exactly once.",
        )
        return None
    return [str(token_map[token_id]).strip() for token_id in answer_order]


def _audit_build_answer_quality(
    answer: str, ordered_text: list[str], handle: str, findings: list[MechanicalFinding]
) -> None:
    """Record punctuation and visible-token quality findings."""
    if not _has_terminal_punctuation(answer):
        _finding(
            findings,
            "build-answer-punctuation-missing",
            "major",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            answer,
            "The assembled learner target has no terminal punctuation.",
        )
    normalized_texts = [_normalize_visible_text(value) for value in ordered_text]
    repeated = sorted({value for value in normalized_texts if value and normalized_texts.count(value) > 1})
    if repeated:
        _finding(
            findings,
            "build-visible-token-duplicate",
            "minor",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(repeated),
            "Repeated visible token text can make exact-token authoring ambiguous; confirm it is intentional.",
        )


def _audit_mapping_ids(
    items: list[Any],
    key: str,
    handle: str,
    label: str,
    findings: list[MechanicalFinding],
) -> None:
    """Check unique IDs in one operation-specific mapping list."""
    values = [item.get(key) for item in items if isinstance(item, dict)]
    _find_duplicate_values(
        values,
        code=f"{_slug(label)}-id-duplicate",
        artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
        location=handle,
        explanation=f"{label.capitalize()} IDs must be unique.",
        findings=findings,
        severity="blocking",
    )


def _audit_text_values(items: list[Any], key: str, handle: str, label: str, findings: list[MechanicalFinding]) -> None:
    """Check duplicate visible text in an option-like list."""
    values = [item.get(key) for item in items if isinstance(item, dict)]
    _audit_duplicate_strings(
        values,
        code=f"{_slug(label)}-text-duplicate",
        handle=handle,
        label=label,
        findings=findings,
    )


def _audit_nonempty_texts(
    items: list[Any], key: str, handle: str, label: str, findings: list[MechanicalFinding]
) -> None:
    """Block missing or blank visible text in operation payload mappings."""
    for item in items:
        value = item.get(key) if isinstance(item, dict) else None
        if not isinstance(value, str) or not value.strip():
            _finding(
                findings,
                f"{_slug(label)}-text-invalid",
                "blocking",
                K_SOURCE_AUDIT_EXERCISES_FILE,
                handle,
                repr(value),
                f"Each {label} needs non-empty visible text.",
            )


def _audit_duplicate_strings(
    values: list[Any],
    *,
    code: str,
    handle: str,
    label: str,
    findings: list[MechanicalFinding],
) -> None:
    """Surface duplicate answer text as a major semantic-review concern."""
    normalized = [_normalize_visible_text(value) for value in values if isinstance(value, str)]
    duplicates = sorted({value for value in normalized if value and normalized.count(value) > 1})
    if duplicates:
        _finding(
            findings,
            code,
            "major",
            K_SOURCE_AUDIT_EXERCISES_FILE,
            handle,
            repr(duplicates),
            f"Duplicate visible text makes the {label} choices ambiguous.",
        )


def _find_duplicate_values(
    values: list[Any],
    *,
    code: str,
    artifact: ReviewArtifact,
    location: str,
    explanation: str,
    findings: list[MechanicalFinding],
    severity: Literal["blocking", "major", "minor"],
) -> None:
    """Record one finding for duplicate comparable values."""
    comparable = [value for value in values if value is not None]
    duplicates = sorted({repr(value) for value in comparable if comparable.count(value) > 1})
    if duplicates:
        _finding(
            findings,
            code,
            severity,
            artifact,
            location,
            ", ".join(duplicates),
            explanation,
        )


def _finding(
    findings: list[MechanicalFinding],
    code: str,
    severity: Literal["blocking", "major", "minor"],
    artifact: ReviewArtifact,
    location: str,
    evidence: str,
    explanation: str,
) -> None:
    """Append a typed finding while keeping call sites compact."""
    findings.append(
        MechanicalFinding(
            code=code,
            severity=severity,
            artifact=artifact,
            location=location,
            evidence=evidence,
            explanation=explanation,
        )
    )


def _join_tokens(tokens: list[str]) -> str:
    """Join token text with the compiler's punctuation-spacing rule."""
    text = " ".join(token for token in tokens if token).strip()
    for punctuation in ",.!?;:":
        text = text.replace(f" {punctuation}", punctuation)
    return text


def _normalize_visible_text(value: object) -> str:
    """Normalize visible text for duplicate-token comparison."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[.!?,;:]+$", "", " ".join(value.split()).casefold()).strip()


def _normalize_speak_text(value: object) -> str:
    """Normalize a speak cue while ignoring Markdown and quote decoration."""
    if not isinstance(value, str):
        return ""
    plain = value.replace("*", "").replace("_", "").replace(chr(96), "").replace(chr(34), "")
    return re.sub(r"[.!?,;:]+$", "", " ".join(plain.split()).casefold()).strip()


def _has_terminal_punctuation(value: str) -> bool:
    """Return whether a target ends in sentence punctuation after quote marks."""
    candidate = re.sub(r"[\"”»’')\]]+$", "", value.rstrip())
    return bool(candidate) and candidate[-1] in K_SOURCE_AUDIT_TERMINAL_PUNCTUATION


def _slug(value: str) -> str:
    """Build a stable finding-code fragment from a human label."""
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "item"


def _status_for(findings: list[MechanicalFinding]) -> Literal["clean", "warning", "blocked", "invalid"]:
    """Classify findings for review/edit gating."""
    if any(
        finding.code in {"exercise-yaml-malformed", "exercise-list-invalid", "exercise-source-invalid"}
        for finding in findings
    ):
        return "invalid"
    if any(finding.severity in {"blocking", "major"} for finding in findings):
        return "blocked"
    if findings:
        return "warning"
    return "clean"


__all__ = [
    "MechanicalAudit",
    "MechanicalBuiltAnswer",
    "MechanicalFinding",
    "audit_exercise_source",
]
