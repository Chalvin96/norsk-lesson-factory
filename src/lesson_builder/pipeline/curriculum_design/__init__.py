"""Curriculum design package.

Phase 0 entry points: ``compute_curriculum_thresholds`` (``thresholds``) derives
deterministic thinness / too-broad / coverage thresholds from the 104
concept-requirements cards so the curriculum_design loop (Phase 1) can flag
concepts that are too thin, too broad, or incomplete. Mirrors the
``calibration.rubric_floors`` shape: pure math in ``thresholds``, live card
loading + CLI in ``thresholds_run``.

Phase 1 entry points: ``run_curriculum_loop`` (``loop``) is the
research -> write -> review -> converge core; ``researcher`` / ``resolve_citations``
(``research``) are the research + citation-truth stage; ``author_course_map``
(``author``) is the writer; ``review_map`` (``review``) is the deterministic +
advisory reviewer. All collaborators are DI-injected so the suite stays offline.

Phase 2 entry points: ``run_bootstrap`` (``bootstrap``) drafts the WHOLE course
map from an empty-repo state using the Phase-1 loop, with full-card schema
validation + coverage mapping + stratified authorability sampling.
``commit_bootstrap_draft`` is the transactional commit step (stage-all ->
validate -> atomic-rename; never per-file ``shutil.copyfile``). Lesson JSON is
never touched.

Phase 3 entry points: ``run_increment`` (``increment``) drafts ONE concept
add/split/merge against the existing 104-map. It subsumes the retired
``curriculum/flow.py``: keeps triage-routing + stale_impacts + tmp-draft/commit;
replaces the anchors-only stub with the Phase-1 loop producing a FULL
``ConceptRequirements`` card. ``commit_increment_draft`` is the commit step.
"""

from lesson_builder.pipeline.curriculum_design.author import author_concept, author_course_map
from lesson_builder.pipeline.curriculum_design.bootstrap import (
    AuthorabilitySample,
    BootstrapCommitResult,
    BootstrapDraft,
    BootstrapResult,
    BootstrapStatus,
    BootstrapValidationError,
    CoverageReport,
    authorability_sample,
    commit_bootstrap_draft,
    run_bootstrap,
)
from lesson_builder.pipeline.curriculum_design.increment import (
    IncrementCommitResult,
    IncrementDraft,
    IncrementImpact,
    IncrementResult,
    IncrementStaleImpact,
    commit_increment_draft,
    run_increment,
)
from lesson_builder.pipeline.curriculum_design.loop import (
    K_CURRICULUM_LOOP_MAX_ROUNDS,
    run_curriculum_loop,
)
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    CurriculumLoopResult,
    LoopStatus,
    ResearchNote,
    ReviewSignals,
)
from lesson_builder.pipeline.curriculum_design.research import (
    FetchFn,
    ResearchBackend,
    researcher,
    resolve_citations,
    unresolved,
)
from lesson_builder.pipeline.curriculum_design.review import review_map
from lesson_builder.pipeline.curriculum_design.thresholds import (
    K_CURRICULUM_THRESHOLDS_PATH,
    CoverageFloor,
    CurriculumThresholds,
    DistributionStats,
    compute_curriculum_thresholds,
    curriculum_thresholds_path_for_repo,
    load_curriculum_thresholds,
    save_curriculum_thresholds,
)
from lesson_builder.pipeline.curriculum_design.thresholds_run import (
    run_curriculum_thresholds,
)

__all__ = [
    # Phase 2: bootstrap mode (whole course, empty repo)
    "AuthorabilitySample",
    "BootstrapCommitResult",
    "BootstrapDraft",
    "BootstrapResult",
    "BootstrapStatus",
    "BootstrapValidationError",
    "CoverageReport",
    "authorability_sample",
    "commit_bootstrap_draft",
    "run_bootstrap",
    # Phase 3: increment mode (one concept add/split/merge)
    "IncrementCommitResult",
    "IncrementDraft",
    "IncrementImpact",
    "IncrementResult",
    "IncrementStaleImpact",
    "commit_increment_draft",
    "run_increment",
    # Phase 0: deterministic thresholds
    "CoverageFloor",
    "CurriculumThresholds",
    "DistributionStats",
    "K_CURRICULUM_THRESHOLDS_PATH",
    "compute_curriculum_thresholds",
    "curriculum_thresholds_path_for_repo",
    "load_curriculum_thresholds",
    "run_curriculum_thresholds",
    "save_curriculum_thresholds",
    # Phase 1: models
    "ConceptDraft",
    "CourseMap",
    "CurriculumLoopResult",
    "LoopStatus",
    "ResearchNote",
    "ReviewSignals",
    # Phase 1: research stage
    "FetchFn",
    "ResearchBackend",
    "resolve_citations",
    "researcher",
    "unresolved",
    # Phase 1: author stage
    "author_concept",
    "author_course_map",
    # Phase 1: review stage
    "review_map",
    # Phase 1: loop core
    "K_CURRICULUM_LOOP_MAX_ROUNDS",
    "run_curriculum_loop",
]
