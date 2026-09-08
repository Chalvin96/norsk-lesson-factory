"""Entry point: `review_existing_package` applies bounded post-generation edits.

The review/edit boundary is deliberately separate from authoring. A reviewer
may identify naturalness or source-structure defects and propose exact text
replacements, but it may not return a replacement lesson or regenerate
exercises. Edits are applied to a copied scratch package, then the ordinary
source load derives the disposable transcript and audio declarations.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.load_lesson import load_lesson
from lesson_builder.application.operations.repair_source import repair_exercise_source
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from lesson_builder.clients.llm.jobs import runner_for_job
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.workflow.lesson_generation.dependencies import approved_content_hash
from lesson_builder.workflow.lesson_generation.dependencies import validate_approved_plan
from lesson_builder.workflow.lesson_generation.dependencies import validate_approved_source
from lesson_builder.workflow.lesson_generation.generation_log import build_response_metadata
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_PLAN_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_MAX_REASKS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_SUBDIR

K_REVIEW_EDIT_ARTIFACTS: tuple[str, ...] = (
    K_LESSON_GENERATION_LESSON_FILE,
    K_LESSON_GENERATION_EXERCISES_FILE,
)
K_REVIEW_EDIT_REQUIRED_FILES: tuple[str, ...] = (
    K_LESSON_GENERATION_PLAN_FILE,
    *K_REVIEW_EDIT_ARTIFACTS,
)


class ReviewEditFinding(BaseModel):
    """One naturalness or structure defect found in the frozen package."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: Literal["blocking", "major", "minor"]
    artifact: Literal["lesson.md", "exercises.yaml"]
    location: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class ReviewEditOperation(BaseModel):
    """One exact replacement against one copied authoring file."""

    model_config = ConfigDict(extra="forbid")

    finding_code: str = Field(min_length=1)
    artifact: Literal["lesson.md", "exercises.yaml"]
    old_text: str = Field(min_length=1)
    new_text: str = Field(min_length=1)
    expected_occurrences: int = Field(default=1, ge=1, le=20)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _replacement_must_change_text(self) -> ReviewEditOperation:
        if self.old_text == self.new_text:
            raise ValueError("review edit replacement must change the source text")
        return self


class ReviewEditPackage(BaseModel):
    """Structured reviewer response; never a complete lesson replacement."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "needs_edit"]
    summary: str = Field(min_length=1)
    audited_handles: list[str] = Field(default_factory=list)
    findings: list[ReviewEditFinding] = Field(default_factory=list)
    edits: list[ReviewEditOperation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _verdict_matches_edits(self) -> ReviewEditPackage:
        # Minor findings are advisory copy/cohesion signals. A clean pass may
        # retain them in the review receipt for human audit; bounded repair is
        # reserved for material findings or an already-triggered edit response.
        material = any(finding.severity in {"blocking", "major"} for finding in self.findings)
        if self.verdict == "pass" and (material or self.edits):
            raise ValueError("a passing review cannot contain material findings or edits")
        if self.verdict == "needs_edit" and not self.findings:
            raise ValueError("needs_edit review must explain at least one finding")
        codes = {finding.code for finding in self.findings}
        unknown = sorted({edit.finding_code for edit in self.edits} - codes)
        if unknown:
            raise ValueError(f"review edits reference unknown finding code(s): {unknown!r}")
        return self


@dataclass(frozen=True)
class ReviewEditResult:
    """Paths and provenance produced by a review/edit pass."""

    status: str
    source_dir: Path
    review_path: Path
    receipt_path: Path
    review: ReviewEditPackage | None
    applied_edits: tuple[dict[str, Any], ...]
    mechanical_audit: MechanicalAudit
    post_edit_mechanical_audit: MechanicalAudit | None = None


def review_existing_package(
    *,
    source_dir: Path,
    output_root: Path,
    repo_root: Path,
    run_id: str,
    reviewer_job: str = K_LESSON_GENERATION_REVIEW_EDIT_JOB,
    review_context: str | None = None,
) -> ReviewEditResult:
    """Review a frozen package and apply only exact edits to its copy.

    The input package is never modified. A malformed reviewer response, an
    ambiguous replacement, or a compiler failure leaves the original package
    intact and records the diagnostic under ``output_root/review_edit``.
    """
    source = Path(source_dir)
    root = Path(output_root)
    review_root = root / K_LESSON_GENERATION_REVIEW_EDIT_SUBDIR
    review_root.mkdir(parents=True, exist_ok=True)
    copied_source = review_root / "source"
    _copy_authoring_source(source, copied_source)
    identity_repairs = _repair_lesson_identity(copied_source)
    original_hash = _package_hash(source)
    repair_result = repair_exercise_source(
        (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
        lesson_text=(copied_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
    )
    (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).write_text(
        repair_result.exercises_yaml,
        encoding="utf-8",
    )
    # Generated scratch packages historically omitted the protected hash from
    # ``plan.md`` (the production approval copier has always required it). A
    # review/edit pass must still be able to repair those immutable scratch
    # artifacts, so normalize the hash on the *copy* after all automatic source
    # repairs. Never mutate the input package merely to make it reviewable.
    _refresh_approved_source_hash(copied_source / K_LESSON_GENERATION_PLAN_FILE, copied_source)
    (review_root / "mechanical-repairs.yaml").write_text(
        yaml.safe_dump(
            {
                "repairs": [item.model_dump(mode="json") for item in repair_result.repairs],
                "preflight": repair_result.audit.model_dump(mode="json"),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    mechanical_audit = audit_source_directory(copied_source)
    (review_root / "mechanical.yaml").write_text(
        yaml.safe_dump(
            mechanical_audit.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    # Build the prompt from the exact copied bytes that will receive edits.
    # ``repair_exercise_source`` may re-serialize YAML before the LLM call;
    # pass those exact prepared bytes so valid old_text patches can apply.
    prompt = build_review_edit_prompt(
        lesson_md=(copied_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
        exercises_yaml=(copied_source / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
        mechanical_audit=mechanical_audit,
        review_context=review_context,
    )
    (review_root / "prompt.md").write_text(prompt, encoding="utf-8")
    receipt: dict[str, Any] = {
        "mode": "review_edit",
        "run_id": run_id,
        "reviewer_job": reviewer_job,
        "original_source_hash": original_hash,
        "mechanical_repairs": [item.model_dump(mode="json") for item in repair_result.repairs],
        "source_identity_repairs": identity_repairs,
        "mechanical_preflight": mechanical_audit.model_dump(mode="json"),
        "stages": [],
    }
    if mechanical_audit.status == "invalid":
        receipt["status"] = "needs_human"
        receipt["error"] = "deterministic review preflight could not parse the source"
        (review_root / "review.yaml").write_text(
            yaml.safe_dump(
                {"status": "not_run", "reason": receipt["error"]},
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        _write_receipt(review_root, receipt)
        return ReviewEditResult(
            status="needs_human",
            source_dir=copied_source,
            review_path=review_root / "review.yaml",
            receipt_path=review_root / "receipt.json",
            review=None,
            applied_edits=(),
            mechanical_audit=mechanical_audit,
        )
    try:
        review, responses = _invoke_review(
            prompt=prompt,
            repo_root=Path(repo_root),
            reviewer_job=reviewer_job,
            review_root=review_root,
            expected_handles=mechanical_audit.exercise_handles,
            source_dir=copied_source,
        )
        _validate_review_handles(review, mechanical_audit.exercise_handles)
    except LlmException as exc:
        receipt["status"] = "reviewer_unavailable"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        _write_receipt(review_root, receipt)
        raise
    except (ValueError, TypeError) as exc:
        receipt["status"] = "reviewer_invalid"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        _write_receipt(review_root, receipt)
        raise LlmParseException(f"review/edit response was invalid: {exc}") from exc

    receipt["review"] = review.model_dump(mode="json")
    receipt["review_response"] = [build_response_metadata(response) for response in responses]
    (review_root / "review.yaml").write_text(
        yaml.safe_dump(review.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    if review.verdict == "pass" or not review.edits:
        if mechanical_audit.material_findings:
            receipt["status"] = "needs_human"
            receipt["error"] = "mechanical preflight findings remain unresolved"
        else:
            receipt["status"] = (
                "edited"
                if repair_result.repairs and review.verdict == "pass"
                else "unchanged"
                if review.verdict == "pass"
                else "needs_human"
            )
            if repair_result.repairs:
                receipt["applied_repairs"] = [item.model_dump(mode="json") for item in repair_result.repairs]
        _write_receipt(review_root, receipt)
        return ReviewEditResult(
            status=receipt["status"],
            source_dir=copied_source,
            review_path=review_root / "review.yaml",
            receipt_path=review_root / "receipt.json",
            review=review,
            applied_edits=(),
            mechanical_audit=mechanical_audit,
        )

    try:
        applied = _apply_review_edits(copied_source, review.edits)
        # A semantic edit may introduce YAML punctuation in a plain scalar
        # (for example, ``prompt_md: Explain this: ...``). Re-run the existing
        # transport-only repair before parsing the post-edit package. This
        # quotes/normalizes serialization but never invents learner content or
        # answer keys; semantic edits remain exactly those returned by the reviewer.
        post_repair = repair_exercise_source(
            (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
            lesson_text=(copied_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
        )
        (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).write_text(
            post_repair.exercises_yaml,
            encoding="utf-8",
        )
        receipt["post_edit_mechanical_repairs"] = [item.model_dump(mode="json") for item in post_repair.repairs]
        post_edit_mechanical_audit = audit_source_directory(copied_source)
        receipt["post_edit_mechanical_preflight"] = post_edit_mechanical_audit.model_dump(mode="json")
        (review_root / "mechanical-post-edit.yaml").write_text(
            yaml.safe_dump(
                post_edit_mechanical_audit.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        if post_edit_mechanical_audit.material_findings:
            receipt["status"] = "needs_human"
            receipt["error"] = "mechanical preflight findings remain after review edits"
            receipt["applied_edits"] = [edit.model_dump(mode="json") for edit in review.edits]
            _write_receipt(review_root, receipt)
            return ReviewEditResult(
                status="needs_human",
                source_dir=copied_source,
                review_path=review_root / "review.yaml",
                receipt_path=review_root / "receipt.json",
                review=review,
                applied_edits=(),
                mechanical_audit=mechanical_audit,
                post_edit_mechanical_audit=post_edit_mechanical_audit,
            )
        _refresh_approved_source_hash(copied_source / K_LESSON_GENERATION_PLAN_FILE, copied_source)
        validate_approved_source(copied_source)
        load_lesson(copied_source, audit=post_edit_mechanical_audit)
    except (OSError, ValueError) as exc:
        receipt["status"] = "edit_failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        receipt["applied_edits"] = applied if "applied" in locals() else []
        _write_receipt(review_root, receipt)
        raise

    receipt["status"] = "edited"
    receipt["applied_edits"] = applied
    receipt["edited_source_hash"] = _package_hash(copied_source)
    receipt["lesson_hash_changed"] = original_hash != receipt["edited_source_hash"]
    _write_receipt(review_root, receipt)
    return ReviewEditResult(
        status="edited",
        source_dir=copied_source,
        review_path=review_root / "review.yaml",
        receipt_path=review_root / "receipt.json",
        review=review,
        applied_edits=tuple(applied),
        mechanical_audit=mechanical_audit,
        post_edit_mechanical_audit=post_edit_mechanical_audit,
    )


def build_review_edit_prompt(
    *,
    lesson_md: str,
    exercises_yaml: str,
    mechanical_audit: MechanicalAudit,
    review_context: str | None = None,
) -> str:
    """Build a reviewer prompt from prepared bytes; permit patches but no regeneration."""
    audit_json = json.dumps(mechanical_audit.model_dump(mode="json"), ensure_ascii=False, indent=2)
    context_block = (
        "\nINDEPENDENT EXERCISE-VERIFIER FINDINGS (authoritative for these handles)\n"
        "---BEGIN exercise-verifier-findings---\n"
        f"{review_context}\n"
        "---END exercise-verifier-findings---\n"
        "Repair only the reported exercise handles. Do not change lesson.md for a"
        " closed-task answerability defect unless the finding explicitly names a"
        " lesson example or rule as its cause.\n"
        if review_context
        else ""
    )
    return f"""You are the post-generation reviewer for a Norwegian lesson package.
Review this exact frozen package for two things:
1. naturalness/correctness of Bokmål, English glosses, answers, and feedback;
2. semantic defects that make the authored package misleading (unnatural
   Norwegian, wrong valency or meaning, bad translations, an answer key that
   teaches the wrong decision, or a transfer item that is not genuinely new).

The deterministic preflight below has checked marker alignment, IDs,
token permutations, assembled build answers, keyed recall punctuation, and
operation shape. Treat blocking findings as authoritative: do not spend review
output re-deriving those facts. Still inspect the assembled targets for meaning,
naturalness, and answerability. Repeated visible token text is reported as a
warning because it may be intentional or may be an ambiguous token pool; decide
that semantic question from context.

Every exercise handle must be reviewed exactly once. Return all handles in
`audited_handles`, even when no finding applies.

The compiler derives model audio from typed `example(s)` and `reading` blocks.
An example whose English side says `Incorrect`, `Wrong`, `Intended meaning`,
`Not standard`, or `Not:` is a negative teaching example and must not become a
model-audio transcript. If the source currently exposes one as an audio
candidate, make the smallest safe source edit (usually an explicit negative
label or moving only that example into an existing warning callout).

Only use the source contract's supported callout syntax when a move is needed:
`::: callout {{variant=warning}}` ... `:::`. Never invent a Pandoc class such as
`::: {{.warning}}`. Keep callout paragraphs plain; do not use Markdown hard
line-break markers (`  ` followed by a newline) inside a callout.

Return verdict, summary, audited_handles, findings, and edits using the supplied schema.
Use pass with no edits for sound content; do not manufacture findings. A material
defect without a safe repair still requires needs_edit and a grounded finding.
Return exact bounded text replacements. Every edit includes the literal old_text copied from
one file, the literal new_text, and expected_occurrences. Use one occurrence by
default. If a defect cannot be fixed safely with an exact replacement, report it
as a finding and return no edit for that finding. Do not edit plan.md or the
derived transcript; the compiler will derive those after source edits.

For a local exercise-structure defect, `old_text` may be one complete YAML
exercise item and `new_text` may be that same item with a safer operation or
payload, provided its handle, objective, Bloom intent, and learner target stay
the same. This is still an edit, not regeneration: do not add requests, rewrite
the lesson, or replace the whole exercises file. Prefer this when a single
multi-sentence token pool makes exact-token grading ambiguous.
Keep every edited `prompt_md`, `text_md`, `why`, and `judge_prompt` as a single
inline YAML scalar. Do not emit block-scalar markers such as `|`, `|-`, `>`, or
`>-`; quote the full scalar instead, even when it contains a colon.

Review the package below. Explanatory prose may be English; Norwegian learner
content must be natural Bokmål. Preserve the lesson's teaching intent and all
valid content. Prefer the smallest local edit.

Before deciding that a package passes, perform this checklist:
- Render every keyed recall answer by concatenating its text spans and keyed
  option values. Flag a malformed result such as duplicate punctuation,
  missing spaces, or an answer that changes the taught sentence.
- Read every choose/recall/build/find_fix answer in its full visible context.
  A valid Bokmål variant, natural word order, or meaning-preserving English
  translation is not a defect merely because the author preferred another
  form. If the prompt permits two answers, either tighten the prompt/context
  with a local edit or report the ambiguity for human review; never mark the
  valid alternative wrong by assertion. Apply explicit requested forms and meanings
  first: grammatical responses that omit a requested tense or construction need not pass.
- For build and recall tasks, verify that the keyed answer is grammatical and
  that the visible options/tokens do not support another equally valid answer.
  For judge, evaluate the stated proposition: a grammatical sentence can fail an
  explicitly requested form or meaning. For find_fix, require a concrete replaceable
  error under the visible task, not an unstated style preference.
- Check every exercise's feedback against the actual answer and the lesson
  explanation. Feedback should explain a useful contrast or likely error without
  redundant per-option commentary. Missing optional feedback is not a defect for
  simple recall when it would only repeat the key. Preserve correct answers when
  repairing false feedback.
- For write, compare the visible prompt, response language and bounds with criteria
  AND judge_prompt in both directions. Check every requested obligation without
  adding hidden translations, example-only answer lists, or other extra requirements.
  Preserve explicitly requested grammar even when another sentence conveys a similar
  meaning. English instructions do not require English output. Remove unrequested
  rubric obligations rather than expanding the task to justify them; restore missing
  requested criteria rather than weakening the prompt.
- Check the lesson's examples, translations, terminology, and scope. Fix a
  concrete defect with an exact local replacement; do not expand the lesson or
  invent a new teaching point.

Completion criterion: list every exercise handle exactly once in audited_handles,
report every grounded semantic defect in the full package, and provide only exact,
bounded edits whose old_text occurs as declared in the original artifact.
Edits are validated against the original source and then applied sequentially:
use non-overlapping anchors and do not depend on text produced by another edit. With verifier
findings, limit repairs to reported handles and explicitly implicated lesson text;
coverage of all handles does not authorize unrelated edits.

MECHANICAL PREFLIGHT (authoritative; do not re-derive it)
---BEGIN mechanical-preflight.json---
{audit_json}
---END mechanical-preflight.json---
{context_block}

LESSON.MD
---BEGIN lesson.md---
{lesson_md}
---END lesson.md---

EXERCISES.YAML
---BEGIN exercises.yaml---
{exercises_yaml}
---END exercises.yaml---
"""


def _invoke_review(
    *,
    prompt: str,
    repo_root: Path,
    reviewer_job: str,
    review_root: Path,
    expected_handles: list[str],
    source_dir: Path,
) -> tuple[ReviewEditPackage, list[LlmResponse]]:
    """Invoke the configured reviewer with one bounded schema re-ask."""
    agent = runner_for_job(reviewer_job, repo_root=repo_root)
    instruction = build_structured_json_prompt(prompt, ReviewEditPackage)
    schema_json = json.dumps(ReviewEditPackage.model_json_schema(), ensure_ascii=False)
    responses: list[LlmResponse] = []
    last_error: Exception | None = None
    for attempt in range(K_LESSON_GENERATION_REVIEW_EDIT_MAX_REASKS + 1):
        response = agent.invoke_response(instruction)
        responses.append(response)
        (review_root / f"response-{attempt + 1}.txt").write_text(response.text, encoding="utf-8")
        try:
            review = ReviewEditPackage.model_validate(extract_json_object(response.text))
            _validate_review_handles(review, expected_handles)
            _validate_review_edits_against_source(review, source_dir)
            return review, responses
        except (ValueError, TypeError) as exc:
            last_error = exc
            instruction = (
                f"{prompt}\n\nYour previous response was invalid: {exc}. "
                "Correct the edit list against the exact copied source bytes. "
                "Return only a valid JSON object matching this schema; do not return a complete lesson.\n"
                f"{schema_json}"
            )
    raise LlmParseException(f"review/edit response remained invalid: {last_error}")


def _validate_review_handles(review: ReviewEditPackage, expected_handles: list[str]) -> None:
    """Require one semantic-review acknowledgement for every exercise handle."""
    actual = review.audited_handles
    if len(actual) != len(set(actual)):
        raise ValueError("audited_handles must not contain duplicates")
    if set(actual) != set(expected_handles) or len(actual) != len(expected_handles):
        missing = [handle for handle in expected_handles if handle not in actual]
        extra = [handle for handle in actual if handle not in expected_handles]
        raise ValueError(f"audited_handles mismatch: missing={missing!r}; extra={extra!r}")


def _validate_review_edits_against_source(review: ReviewEditPackage, source_dir: Path) -> None:
    """Reject or re-ask patches whose literals do not exist in the copied source."""
    errors: list[str] = []
    for edit in review.edits:
        path = Path(source_dir) / edit.artifact
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{edit.artifact}: source is unreadable ({exc})")
            continue
        occurrences = text.count(edit.old_text)
        if occurrences != edit.expected_occurrences:
            errors.append(
                f"{edit.artifact} finding {edit.finding_code!r}: expected "
                f"{edit.expected_occurrences} occurrence(s), found {occurrences}; "
                "copy old_text literally from the supplied source"
            )
    if errors:
        raise ValueError("review edit literals are not applicable: " + "; ".join(errors))


def _apply_review_edits(source_dir: Path, edits: list[ReviewEditOperation]) -> list[dict[str, Any]]:
    """Apply literal replacements and fail closed on missing/ambiguous text."""
    applied: list[dict[str, Any]] = []
    for edit in edits:
        path = Path(source_dir) / edit.artifact
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(edit.old_text)
        if occurrences != edit.expected_occurrences:
            raise ValueError(
                f"review edit for {edit.artifact!r} expected {edit.expected_occurrences} "
                f"occurrence(s), found {occurrences}: {edit.old_text[:120]!r}"
            )
        path.write_text(text.replace(edit.old_text, edit.new_text), encoding="utf-8")
        applied.append(
            {
                **edit.model_dump(mode="json"),
                "actual_occurrences": occurrences,
            }
        )
    return applied


def _copy_authoring_source(source_dir: Path, destination: Path) -> None:
    """Copy only the three authored files; never copy derived artifacts."""
    # ``validate_approved_plan`` checks the authoring boundary without relying
    # on a generated package having a current self-hash. The copied source is
    # hash-normalized by ``review_existing_package`` before edits are applied.
    validate_approved_plan(Path(source_dir))
    destination.mkdir(parents=True, exist_ok=False)
    for name in K_REVIEW_EDIT_REQUIRED_FILES:
        shutil.copy2(Path(source_dir) / name, destination / name)


def _repair_lesson_identity(source_dir: Path) -> list[dict[str, str]]:
    """Align a copied lesson slug with the approved plan's lesson identity."""
    plan_path = Path(source_dir) / K_LESSON_GENERATION_PLAN_FILE
    lesson_path = Path(source_dir) / K_LESSON_GENERATION_LESSON_FILE
    plan_metadata, _ = parse_source_frontmatter(plan_path.read_text(encoding="utf-8"))
    lesson_text = lesson_path.read_text(encoding="utf-8")
    lesson_metadata, lesson_body = parse_source_frontmatter(lesson_text)
    lesson_body = lesson_body.lstrip("\n")
    lesson_id = plan_metadata.get("lesson_id")
    slug = lesson_metadata.get("slug")
    if not isinstance(lesson_id, str) or not lesson_id or slug == lesson_id:
        return []
    if not isinstance(slug, str) or not slug:
        raise ValueError("lesson.md must contain a non-empty slug")
    lesson_metadata["slug"] = lesson_id
    updated = (
        "---\n"
        + yaml.safe_dump(lesson_metadata, allow_unicode=True, sort_keys=False).rstrip()
        + "\n---\n"
        + lesson_body
    )
    lesson_path.write_text(updated, encoding="utf-8")
    return [{"code": "lesson-slug-align", "from": slug, "to": lesson_id}]


def _refresh_approved_source_hash(plan_path: Path, source_dir: Path) -> None:
    """Refresh the plan's protected hash after an approved local edit."""
    text = Path(plan_path).read_text(encoding="utf-8")
    metadata, body = parse_source_frontmatter(text)
    metadata["approved_source_hash"] = "pending"
    provisional = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip() + "\n---" + body
    Path(plan_path).write_text(provisional, encoding="utf-8")
    metadata["approved_source_hash"] = approved_content_hash(source_dir)
    updated = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip() + "\n---" + body
    Path(plan_path).write_text(updated, encoding="utf-8")


def _package_hash(source_dir: Path) -> str:
    """Hash the exact authored package before or after review edits."""
    hasher = hashlib.sha256()
    for name in K_REVIEW_EDIT_REQUIRED_FILES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((Path(source_dir) / name).read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _write_receipt(review_root: Path, receipt: dict[str, Any]) -> Path:
    """Persist the review/edit receipt atomically enough for scratch use."""
    path = Path(review_root) / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "ReviewEditFinding",
    "ReviewEditOperation",
    "ReviewEditPackage",
    "ReviewEditResult",
    "build_review_edit_prompt",
    "review_existing_package",
]
