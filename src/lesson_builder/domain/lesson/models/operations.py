"""Entry points: `operation_policy`, `operation_names`, and guidance renderers.

The operation registry is the shared policy seam for authoring, generation,
and deterministic quality checks. Bloom and evidence policy is stored beside
each operation so callers do not maintain separate, drifting matrices. The
evidence-route registry records the learner evidence shape before a model fills
operation-specific payload details.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

EvidenceClass = Literal["oracle", "spoken", "llm_judged"]
BuildStage = Literal["notice", "retrieve", "controlled", "transfer"]
EvidenceRoute = Literal[
    "meaning_selection",
    "bounded_retrieval",
    "pair_matching",
    "sentence_judgement",
    "sentence_construction",
    "form_repair",
    "contrast_classification",
    "spoken_production",
    "open_production",
]

K_EVIDENCE_ROUTE_DESCRIPTIONS: dict[str, str] = {
    "meaning_selection": "select one meaning or interpretation from competing options",
    "bounded_retrieval": "retrieve a taught form in a bounded sentence or slot",
    "pair_matching": "match two sides of a taught form/meaning pair",
    "sentence_judgement": "judge whether one complete sentence is correct",
    "sentence_construction": "construct a complete sentence from supplied tokens",
    "form_repair": "find and repair one taught form error",
    "contrast_classification": "classify items into explicitly taught contrast groups",
    "spoken_production": "produce one exact Norwegian utterance for a cue",
    "open_production": "produce open Norwegian text against authored criteria",
}


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    """Policy metadata for one supported exercise operation."""

    name: str
    bloom_levels: tuple[str, ...]
    default_bloom: str
    build_stage: BuildStage
    payload_kind: str
    payload_fields: tuple[str, ...]
    evidence_class: EvidenceClass
    evidence_routes: tuple[EvidenceRoute, ...]


K_OPERATION_PAYLOAD_EXAMPLES: dict[str, str] = {
    "recall_fill": '''audio_target: "I dag kommer jeg hjem."
segments:
  - text_md: "I dag "
  - blank_id: finite-verb
    options: [kommer, komme]
    answer_index: 0
  - text_md: " jeg hjem."''',
    "match_pairs": """left:
  - left_id: left-1
    text: "Jeg kommer."
right:
  - right_id: right-1
    text: "Statement"
pairs:
  - left_id: left-1
    right_id: right-1""",
    "speak": '''target: "Jeg snakker tydelig norsk."''',
    "write": """response_language: "no"
min_words: 20
max_words: 35
judge_prompt: >-
  Check the response against the task and report evidence for each criterion.
criteria:
  - id: main-point
    instruction: States the main point in natural Norwegian.""",
    "judge": '''sentence_md: "Jeg liker kaffe."
is_correct: true
feedback: "The sentence uses the taught form correctly."''',
    "choose": '''stem_md: "Choose the sentence that follows the pattern."
options:
  - id: option-a
    text: "Jeg kommer i morgen."
    correct: true
    why: "The sentence follows the taught pattern."
  - id: option-b
    text: "I morgen jeg kommer."
    correct: false
    why: "The words are not in the taught order."''',
    "build": """tokens:
  - token_id: tok-1
    text: Jeg
  - token_id: tok-2
    text: kommer.
answer_order: [tok-1, tok-2]""",
    "find_fix": '''tokens:
  - token_id: tok-1
    text: Jeg
  - token_id: tok-2
    text: kommer
error_token_id: tok-2
feedback: "Add the taught ending to the verb."''',
    "categorize": """buckets:
  - bucket_id: group-a
    label: "Pattern A"
  - bucket_id: group-b
    label: "Pattern B"
items:
  - item_id: item-1
    text: "Jeg kommer."
    bucket_id: group-a""",
}


K_OPERATION_POLICIES: dict[str, OperationPolicy] = {
    "recall_fill": OperationPolicy(
        name="recall_fill",
        bloom_levels=("remember", "understand", "apply"),
        default_bloom="remember",
        build_stage="retrieve",
        payload_kind="segments",
        payload_fields=("audio_target", "segments"),
        evidence_class="oracle",
        evidence_routes=("bounded_retrieval",),
    ),
    "match_pairs": OperationPolicy(
        name="match_pairs",
        bloom_levels=("remember",),
        default_bloom="remember",
        build_stage="notice",
        payload_kind="left_right_pairs",
        payload_fields=("left", "right", "pairs"),
        evidence_class="oracle",
        evidence_routes=("pair_matching",),
    ),
    "speak": OperationPolicy(
        name="speak",
        bloom_levels=("remember", "apply"),
        default_bloom="remember",
        build_stage="transfer",
        payload_kind="target",
        payload_fields=("target",),
        evidence_class="spoken",
        evidence_routes=("spoken_production",),
    ),
    "write": OperationPolicy(
        name="write",
        bloom_levels=("apply",),
        default_bloom="apply",
        build_stage="transfer",
        payload_kind="open_response",
        payload_fields=(
            "response_language",
            "min_words",
            "max_words",
            "judge_prompt",
            "criteria",
        ),
        evidence_class="llm_judged",
        evidence_routes=("open_production",),
    ),
    "judge": OperationPolicy(
        name="judge",
        bloom_levels=("understand",),
        default_bloom="understand",
        build_stage="controlled",
        payload_kind="sentence",
        payload_fields=("sentence_md", "is_correct", "feedback"),
        evidence_class="oracle",
        evidence_routes=("sentence_judgement",),
    ),
    "choose": OperationPolicy(
        name="choose",
        bloom_levels=("understand", "analyze"),
        default_bloom="understand",
        build_stage="notice",
        payload_kind="options",
        payload_fields=("stem_md", "options"),
        evidence_class="oracle",
        evidence_routes=("meaning_selection",),
    ),
    "build": OperationPolicy(
        name="build",
        bloom_levels=("apply",),
        default_bloom="apply",
        build_stage="controlled",
        payload_kind="tokens",
        payload_fields=("tokens", "answer_order"),
        evidence_class="oracle",
        evidence_routes=("sentence_construction",),
    ),
    "find_fix": OperationPolicy(
        name="find_fix",
        bloom_levels=("analyze",),
        default_bloom="analyze",
        build_stage="controlled",
        payload_kind="tokens_with_error",
        payload_fields=("tokens", "error_token_id", "feedback"),
        evidence_class="oracle",
        evidence_routes=("form_repair",),
    ),
    "categorize": OperationPolicy(
        name="categorize",
        bloom_levels=("analyze",),
        default_bloom="analyze",
        build_stage="notice",
        payload_kind="buckets_and_items",
        payload_fields=("buckets", "items"),
        evidence_class="oracle",
        evidence_routes=("contrast_classification",),
    ),
}

K_OPERATION_NAMES: tuple[str, ...] = tuple(K_OPERATION_POLICIES)


def operation_policy(operation: str) -> OperationPolicy | None:
    """Return the registered policy for an operation, or ``None`` if unknown."""
    return K_OPERATION_POLICIES.get(operation)


def operation_names() -> tuple[str, ...]:
    """Return supported operation names in stable authoring order."""
    return K_OPERATION_NAMES


def evidence_route_names() -> tuple[str, ...]:
    """Return the stable learner-evidence route names."""
    return tuple(K_EVIDENCE_ROUTE_DESCRIPTIONS)


def evidence_route_operations(route: str) -> tuple[str, ...]:
    """Return operations that can represent one evidence route."""
    if route not in K_EVIDENCE_ROUTE_DESCRIPTIONS:
        return ()
    return tuple(name for name, policy in K_OPERATION_POLICIES.items() if route in policy.evidence_routes)


def evidence_route_bloom_levels(route: str) -> tuple[str, ...]:
    """Return Bloom levels supported by a registered operation for an evidence route."""
    levels: list[str] = []
    for operation in evidence_route_operations(route):
        policy = K_OPERATION_POLICIES[operation]
        for level in policy.bloom_levels:
            if level not in levels:
                levels.append(level)
    return tuple(levels)


def render_evidence_route_guidance(routes: Iterable[str] | None = None) -> str:
    """Render route guidance for the operation-free exercise handoff."""
    selected = set(routes) if routes is not None else None
    return "\n".join(
        f"- {route}: {description}; operation={', '.join(evidence_route_operations(route))}; "
        f"allowed_bloom={', '.join(evidence_route_bloom_levels(route))}"
        for route, description in K_EVIDENCE_ROUTE_DESCRIPTIONS.items()
        if selected is None or route in selected
    )


def render_operation_guidance(operations: Iterable[str] | None = None) -> str:
    """Render operation/Bloom/payload guidance without internal diagnostics.

    ``build_stage`` is factory-only metadata used by deterministic diagnostics.
    It is deliberately not rendered into model prompts or serialized exercise
    source, where a model could mistake it for a supported YAML field.
    """
    selected = set(operations) if operations is not None else None
    return "\n".join(
        f"- {policy.name} = {', '.join(policy.bloom_levels)}; payload={', '.join(policy.payload_fields)}"
        for policy in K_OPERATION_POLICIES.values()
        if selected is None or policy.name in selected
    )


def render_operation_payload_guidance(operations: Iterable[str] | None = None) -> str:
    """Render copyable source payload shapes for every registered operation."""
    selected = set(operations) if operations is not None else None
    sections: list[str] = []
    for policy in K_OPERATION_POLICIES.values():
        if selected is not None and policy.name not in selected:
            continue
        example = K_OPERATION_PAYLOAD_EXAMPLES[policy.name]
        sections.append(f"{policy.name} (fields: {', '.join(policy.payload_fields)}):\n```yaml\n{example}\n```")
    return "\n\n".join(sections)


def eligible_operations(bloom_targets: list[str]) -> list[str]:
    """Return operations allowed by the registered Bloom policy.

    Operations are deliberately reusable across lesson kinds: a lesson kind
    does not decide whether an exercise is useful; the operation's Bloom
    level, payload contract, and evidence policy do.
    """
    targets = set(bloom_targets)
    return [name for name in K_OPERATION_NAMES if targets.intersection(K_OPERATION_POLICIES[name].bloom_levels)]


def is_oracle_operation(operation: str) -> bool:
    """Return whether an operation has a deterministic answer oracle."""
    policy = operation_policy(operation)
    return policy is not None and policy.evidence_class == "oracle"


__all__ = [
    "BuildStage",
    "EvidenceClass",
    "EvidenceRoute",
    "OperationPolicy",
    "eligible_operations",
    "evidence_route_bloom_levels",
    "evidence_route_names",
    "evidence_route_operations",
    "render_evidence_route_guidance",
    "is_oracle_operation",
    "operation_names",
    "operation_policy",
    "render_operation_guidance",
    "render_operation_payload_guidance",
]
