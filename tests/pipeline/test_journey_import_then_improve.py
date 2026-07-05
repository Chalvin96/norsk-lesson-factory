"""Task F Step F4 (integration): import a fixture lesson, then ``improve`` parks
at the human gate. Proves the Journey 2 -> Journey 3 handoff end-to-end with
noop deps (offline).

Entry point: ``import_lesson`` -> ``run_improvement_flow``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.improvement_flow import run_improvement_flow
from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_import import import_lesson
from lesson_builder.pipeline.lesson_qa_graph import noop_fixer

ROOT = Path(__file__).resolve().parents[2]


class _FakeAuthor:
    def invoke(self, prompt: str, **kw: Any) -> str:
        return (
            '{"exercises": [{"operation": "judge", "prompt_text": "Is this correct?", '
            '"explanation_text": "practice", "flat": {"sentence_no": "Eksempel.", '
            '"is_correct": true, "feedback": "ok"}}]}'
        )


def test_import_then_improve_given_imported_lesson_expect_improve_parks_at_gate(
    tmp_path: Path, monkeypatch
):
    # setup: import a real lesson into a scratch repo.
    src = tmp_path / "ordinal_numbers.json"
    src.write_text(
        (ROOT / "data" / "lessons" / "ordinal_numbers.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    req_src = ROOT / "data" / "concept_requirements" / "ordinal_numbers.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    import_lesson(src, repo_root=repo, output_root=repo)
    assert not (repo / "dist" / "lessons" / "ordinal_numbers.json").exists()
    # the improve flow reads requirements from data/concept_requirements/.
    req_dir = repo / "data" / "concept_requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    (req_dir / "ordinal_numbers.json").write_text(req_src.read_text(encoding="utf-8"))
    (repo / "store").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo / "store" / "checkpoints.db")

    # execute: improve the freshly-imported lesson (J3 handoff).
    result = run_improvement_flow(
        "add an exercise",
        repo_root=repo,
        slug="ordinal_numbers",
        add_exercise=True,
        count=1,
        run_id="j2j3",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    # assert: the imported lesson reached the human gate via the shared back-half.
    assert result.status == "parked_for_review"
    assert result.graph is not None
    assert result.graph["next"] == ["human_gate"]
