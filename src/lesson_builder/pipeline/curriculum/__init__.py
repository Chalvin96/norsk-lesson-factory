"""Entry point: ``run_increment`` / ``commit_increment_draft``.

The curriculum subcommand's implementation now lives in
``curriculum_design.increment`` (Phase 3 INCREMENT mode). This package
re-exports the increment entry points so the CLI surface
(``lesson-data curriculum <text>``) stays stable after ``curriculum/flow.py``
was retired. The increment runs the Phase-1 ``run_curriculum_loop`` to produce
a FULL ``ConceptRequirements`` card (replacing the anchors-only stub) and keeps
the triage-routing + stale_impacts + tmp-draft/commit discipline.
"""

from lesson_builder.pipeline.curriculum_design.increment import (
    IncrementCommitResult,
    IncrementDraft,
    IncrementImpact,
    IncrementResult,
    IncrementRoute,
    IncrementStaleImpact,
    IncrementStatus,
    commit_increment_draft,
    run_increment,
)

# Back-compat aliases so callers that imported the old flow names keep working
# during the transition. These point at the new increment implementation.
run_curriculum_flow = run_increment
commit_curriculum_draft = commit_increment_draft

__all__ = [
    "IncrementCommitResult",
    "IncrementDraft",
    "IncrementImpact",
    "IncrementResult",
    "IncrementRoute",
    "IncrementStaleImpact",
    "IncrementStatus",
    "commit_curriculum_draft",
    "commit_increment_draft",
    "run_curriculum_flow",
    "run_increment",
]
