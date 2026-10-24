"""Entry points: curriculum-plan materialization from the approved catalog.

``application.operations.materialize_curriculum.materialize_curriculum_plan`` writes the
deterministic scratch course plan and, with explicit approval, the committed
``content/curriculum/plan.yaml``;
``application.operations.materialize_curriculum.validate_committed_curriculum_plan`` revalidates
the committed plan against its approved catalog. ``models`` holds the typed
plan artifacts and staged curriculum edits; ``settings`` holds the plan schema,
scratch/committed paths, active kinds, and early-CEFR terminology policy.
Import each module directly; ``services.plan_policy.build_plan_slots`` contains
approved-catalog ordering policy, while
``services.curriculum_edits.preview_curriculum_edits`` transforms staged edits
and locates their dependency effects. Dependency governance lives in
``lesson_builder.domain.catalog.services``.
"""
