"""Entry point: ``finalize_node`` (registered as ``finalize``).

``build_lesson_generation_graph`` calls this node after an accepted human
decision. Finalization is the only node allowed to write the disposable JSON
export and acceptance evidence, and it refuses stale prepared bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.dependencies import source_package_hash
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXPORT_INDENT
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXPORT_SUBDIR
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_LEDGER_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_ACCEPTED
from lesson_builder.workflow.lesson_generation.stage_attestations import read_stage_attestations
from lesson_builder.workflow.lesson_generation.stage_attestations import summary_statuses
from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


def finalize_node(state: LessonGenerationState, deps: LessonGenerationDeps) -> dict[str, Any]:
    """Write the exact immutable approval and immediately promote its lesson."""
    source_dir = Path(state.get("source_dir") or "")
    if not source_dir.is_dir():
        raise ValueError("cannot accept a lesson package without its prepared source directory")
    current_source_hash = source_package_hash(source_dir)
    prepared_source_hash = state.get("source_hash", "")
    if current_source_hash != prepared_source_hash:
        raise ValueError(
            "prepared catalog source changed after the human gate; rerun preparation "
            f"(prepared={prepared_source_hash!r}, current={current_source_hash!r})"
        )
    output_root = Path(state["output_root"])
    export_doc = dict(state.get("export_doc") or {})
    if export_doc.get("source_hash") != current_source_hash:
        raise ValueError("prepared export is stale for the current source bytes")
    slot_hash = state.get("curriculum_slot_sha256")
    catalog_id = state.get("catalog_id")
    plan_text = (source_dir / "plan.md").read_text(encoding="utf-8")
    front_matter = yaml.safe_load(plan_text.split("---", 2)[1]) if plan_text.startswith("---") else {}
    plan_lesson_id = front_matter.get("lesson_id") if isinstance(front_matter, dict) else None
    packet_id = str(export_doc.get("artifact_id", "")).removeprefix("lesson:")
    slot_id = str((state.get("scheduled_slot") or {}).get("catalog_id") or "")
    identities = {value for value in (catalog_id, plan_lesson_id, packet_id, slot_id) if value}
    if len(identities) > 1:
        raise ValueError("lesson package identities disagree across batch, plan, packet, and curriculum slot")
    evidence_issues = verify_source_attestations(source_dir)
    if slot_hash and evidence_issues:
        raise ValueError("fresh stage evidence is stale: " + "; ".join(evidence_issues))
    export_dir = output_root / K_LESSON_GENERATION_EXPORT_SUBDIR
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"{state['run_id']}.json"
    export_text = (
        json.dumps(
            export_doc,
            ensure_ascii=False,
            indent=K_LESSON_GENERATION_EXPORT_INDENT,
            sort_keys=True,
        )
        + "\n"
    )
    if not export_path.exists() or export_path.read_text(encoding="utf-8") != export_text:
        export_path.write_text(export_text, encoding="utf-8")

    ledger_path = output_root / K_LESSON_GENERATION_LEDGER_FILE
    ledger_entry = deps.ledger(
        ledger_path=ledger_path,
        run_id=state["run_id"],
        export_doc=export_doc,
        source_hash=state.get("source_hash", ""),
    )
    lesson_id = str(
        (state.get("scheduled_slot") or {}).get("catalog_id")
        or export_doc.get("artifact_id", "").removeprefix("lesson:")
    )
    if not lesson_id:
        plan_text = (source_dir / "plan.md").read_text(encoding="utf-8")
        lesson_id = str(yaml.safe_load(plan_text.split("---", 2)[1]).get("lesson_id", ""))
    if not lesson_id:
        raise ValueError("accepted lesson package has no lesson_id")
    # Standalone fixture graphs remain scratch-only until a real curriculum slot is supplied.
    if not slot_hash:
        return {
            "export_path": str(export_path),
            "ledger_path": str(ledger_path),
            "ledger_entry": ledger_entry,
            "stage": K_LESSON_GENERATION_STAGE_ACCEPTED,
        }
    evidence_hash = hashlib.sha256(
        json.dumps(summary_statuses(read_stage_attestations(source_dir)), sort_keys=True).encode()
    ).hexdigest()
    repo_root = Path(state.get("repo_root") or ".")
    from lesson_builder.workflow.lesson_generation.approval import create_lesson_approval
    from lesson_builder.workflow.lesson_generation.promotion import promote_lesson_approval

    approval = create_lesson_approval(
        repo_root=repo_root,
        source_dir=source_dir,
        lesson_id=lesson_id,
        curriculum_slot_sha256=slot_hash,
        evidence_sha256=f"sha256:{evidence_hash}",
    )
    promote_lesson_approval(repo_root=repo_root, approval_path=approval)
    return {
        "export_path": str(export_path),
        "ledger_path": str(ledger_path),
        "ledger_entry": ledger_entry,
        "approval_path": str(approval),
        "promotion_source_hash": current_source_hash,
        "stage": K_LESSON_GENERATION_STAGE_ACCEPTED,
    }


__all__ = ["finalize_node"]
