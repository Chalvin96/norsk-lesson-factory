"""Entry point: ``run_catalog``.

The runner supplies repository-scoped SQLite checkpoints and writes only a
scratch proposal. Canonical curriculum, requirements, lessons, and exports are
never changed by this flow.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

from lesson_builder.domain.catalog.services.normalization import normalize_slug
from lesson_builder.domain.lesson.models.lesson import CefrLevel
from lesson_builder.workflow.catalog_design.dependencies import CatalogDesignDependencies
from lesson_builder.workflow.catalog_design.dependencies import default_catalog_design_dependencies
from lesson_builder.workflow.catalog_design.graph import build_catalog_graph
from lesson_builder.workflow.catalog_design.models import CatalogProposal
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.settings import K_CATALOG_DESIGN_GRAPH_VERSION
from lesson_builder.workspace.paths import K_WORKSPACE_ROOT

K_CHECKPOINTS_FILENAME = "checkpoints.db"
K_SCRATCH_DIRNAME = "scratch"
K_CATALOG_DIRNAME = "catalog"
K_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def run_catalog(
    category: str,
    *,
    category_guidance: str = "",
    cefr_tags: Sequence[CefrLevel] = (),
    max_iterations: int = 3,
    repo_root: Path | None = None,
    run_id: str | None = None,
    deps: CatalogDesignDependencies | None = None,
) -> dict[str, Any]:
    """Run one category proposal and save its human-reviewable scratch artifact."""
    root = Path(repo_root) if repo_root is not None else K_WORKSPACE_ROOT
    request = CatalogRequest(
        category=category,
        category_guidance=category_guidance,
        cefr_tags=list(cefr_tags),
        max_iterations=max_iterations,
    )
    chosen_run_id = _validated_run_id(run_id)
    category_slug = normalize_slug(request.category) or "catalog"
    proposal_path = _proposal_path(root, category_slug, chosen_run_id)
    if proposal_path.exists():
        raise FileExistsError(
            f"catalog run {chosen_run_id!r} already has a proposal at {proposal_path}; "
            "choose a new run_id so prior artifacts remain intact"
        )
    thread_id = f"catalog:{K_CATALOG_DESIGN_GRAPH_VERSION}:{category_slug}:{chosen_run_id}"
    checkpoints_path = root / "store" / K_CHECKPOINTS_FILENAME
    initial_state: dict[str, Any] = {
        "request": request.model_dump(mode="json"),
        "repo_root": str(root),
        "run_id": chosen_run_id,
        "iteration": 0,
        "max_iterations": request.max_iterations,
        "stagnation_count": 0,
        "last_fingerprint": None,
        "advice_used": False,
        "advice": None,
        "errors": [],
    }
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    with _open_checkpointer(checkpoints_path) as checkpointer:
        graph = build_catalog_graph(
            deps if deps is not None else default_catalog_design_dependencies(repo_root=root),
            checkpointer=checkpointer,
        )
        result = graph.invoke(initial_state, config=config)

    proposal = CatalogProposal.model_validate(result["proposal"])
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(
        json.dumps(proposal.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "thread_id": thread_id,
        "run_id": chosen_run_id,
        "proposal_path": str(proposal_path),
        "proposal": proposal.model_dump(mode="json"),
    }


@contextlib.contextmanager
def _open_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(connection)
    finally:
        connection.close()


def _proposal_path(root: Path, category_slug: str, run_id: str) -> Path:
    return root / "store" / K_SCRATCH_DIRNAME / K_CATALOG_DIRNAME / category_slug / run_id / "proposal.json"


def _validated_run_id(run_id: str | None) -> str:
    value = run_id or uuid.uuid4().hex[:12]
    if not K_RUN_ID_RE.fullmatch(value):
        raise ValueError("run_id must be a single safe identifier of at most 64 characters")
    return value


__all__ = ["run_catalog"]
