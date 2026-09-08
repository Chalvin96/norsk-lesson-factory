"""Entry points: ``build_catalog_graph`` and ``run_catalog``.

The catalog-design graph discovers lesson candidates, resolves overlap, rejects
duplicates and weak candidates, and writes a proposal without mutating the
canonical curriculum.
"""

from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies
from lesson_builder.workflow.catalog_design.dependencies import default_catalog_design_dependencies
from lesson_builder.workflow.catalog_design.graph import build_catalog_graph
from lesson_builder.workflow.catalog_design.models import AdviceResult
from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogDecision
from lesson_builder.workflow.catalog_design.models import CatalogProposal
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import EvaluationBatch
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.models import ResolutionBatch
from lesson_builder.workflow.catalog_design.runner import run_catalog

__all__ = [
    "CatalogCandidate",
    "CatalogDecision",
    "CatalogProposal",
    "CatalogRequest",
    "CatalogDesignDependencies",
    "CandidateBatch",
    "CandidateEvaluation",
    "CandidateResolution",
    "AdviceResult",
    "EvaluationBatch",
    "ExistingLesson",
    "ResolutionBatch",
    "default_catalog_design_dependencies",
    "build_catalog_graph",
    "run_catalog",
]
