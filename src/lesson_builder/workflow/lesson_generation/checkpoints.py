"""Entry points: `split_checkpoint_directives`, `validate_routed_checkpoint_requests`, and `assert_checkpoint_metadata_absent`.

Called by the rich-authoring workflow's `split_checkpoint` and
`validate_checkpoint_boundary` operations.

Checkpoint intent is authored beside the teaching prose but must never enter
learner-facing Markdown. This module owns the deterministic boundary that
consumes typed draft directives into exercise markers and a scratch-only
request handoff before source normalization. Contracts live in the workflow
models module; this module owns only boundary policy.
"""

from __future__ import annotations

import re
from typing import Any
from typing import cast

import yaml
from pydantic import ValidationError

from lesson_builder.workflow.lesson_generation.models import CheckpointIntent
from lesson_builder.workflow.lesson_generation.models import CheckpointSplit

K_CHECKPOINT_DIRECTIVE_RE = re.compile(r"(?ms)^\{\{checkpoint[ \t]*\n(?P<body>.*?)\n\}\}[ \t]*(?=\n|$)")
K_CHECKPOINT_HANDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
K_CHECKPOINT_LEGACY_TOKEN_RE = re.compile(
    r"(?im)^Checkpoint\s+\S+\s+\[[^\]\n]*\bobjective\b[^\]\n]*\bbloom\b[^\]\n]*\]\s*:"
)
K_CHECKPOINT_MARKER_TEMPLATE = "{{{{exercise: {handle}}}}}"
K_CHECKPOINT_INTENT_FIELDS = ("handle", "objective_ref", "bloom", "evidence")
K_CHECKPOINT_REQUEST_FIELDS = frozenset((*K_CHECKPOINT_INTENT_FIELDS, "evidence_route"))


def split_checkpoint_directives(draft_text: str) -> CheckpointSplit:
    """Consume typed checkpoint directives into markers and frozen intents."""
    if K_CHECKPOINT_LEGACY_TOKEN_RE.search(draft_text):
        raise ValueError("draft contains a prose-shaped checkpoint token; use a typed {{checkpoint ...}} directive")

    intents: list[CheckpointIntent] = []

    def _replace(match: re.Match[str]) -> str:
        intent = _load_checkpoint_intent(match.group("body"))
        intents.append(intent)
        return K_CHECKPOINT_MARKER_TEMPLATE.format(handle=intent.handle)

    prose_md = K_CHECKPOINT_DIRECTIVE_RE.sub(_replace, draft_text)
    if "{{checkpoint" in prose_md:
        raise ValueError("draft contains a malformed checkpoint directive")
    if not intents:
        raise ValueError("draft must contain at least one typed checkpoint directive")
    handles = [intent.handle for intent in intents]
    if len(handles) != len(set(handles)):
        raise ValueError("checkpoint directive handles must be unique")
    marker_handles = re.findall(r"\{\{exercise:\s*([^}\s]+)\s*\}\}", prose_md)
    if marker_handles != handles:
        raise ValueError(
            f"checkpoint split did not preserve marker order: markers={marker_handles!r}, intents={handles!r}"
        )
    intents_yaml = cast(
        str,
        yaml.safe_dump(
            [intent.model_dump(mode="json") for intent in intents],
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    return CheckpointSplit(prose_md=prose_md, intents_yaml=intents_yaml)


def validate_routed_checkpoint_requests(requests_yaml: str, intents_yaml: str) -> None:
    """Reject any normalizer change beyond adding one evidence route."""
    raw_requests = _load_yaml_list(requests_yaml, label="routed exercise requests")
    raw_intents = _load_yaml_list(intents_yaml, label="checkpoint intents")
    projected_requests: list[dict[str, Any]] = []
    for request in raw_requests:
        if not isinstance(request, dict):
            raise TypeError("routed exercise request entries must be mappings")
        if set(request) != K_CHECKPOINT_REQUEST_FIELDS:
            raise ValueError("routed exercise requests may add only evidence_route to checkpoint intent")
        if not isinstance(request.get("evidence_route"), str) or not request["evidence_route"].strip():
            raise ValueError(f"exercise request {request.get('handle', '#unknown')!r} is missing evidence_route")
        projected_requests.append({field: request.get(field) for field in K_CHECKPOINT_INTENT_FIELDS})
    if projected_requests != raw_intents:
        raise ValueError("routed exercise requests changed authored checkpoint intent or order")


def assert_checkpoint_metadata_absent(lesson_md: str) -> None:
    """Reject internal checkpoint syntax in learner-facing lesson Markdown."""
    if "{{checkpoint" in lesson_md or K_CHECKPOINT_LEGACY_TOKEN_RE.search(lesson_md):
        raise ValueError("learner-facing lesson Markdown contains internal checkpoint metadata")


def _load_checkpoint_intent(body: str) -> CheckpointIntent:
    try:
        value = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ValueError(f"checkpoint directive is not valid YAML: {exc}") from exc
    try:
        intent = CheckpointIntent.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"malformed checkpoint directive: {exc}") from exc
    if not K_CHECKPOINT_HANDLE_RE.fullmatch(intent.handle):
        raise ValueError(f"invalid checkpoint handle {intent.handle!r}")
    return intent


def _load_yaml_list(text: str, *, label: str) -> list[Any]:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{label} are not valid YAML: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty YAML list")
    return value


__all__ = [
    # Draft boundary
    "assert_checkpoint_metadata_absent",
    "split_checkpoint_directives",
    "validate_routed_checkpoint_requests",
]
