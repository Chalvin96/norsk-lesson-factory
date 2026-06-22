"""Entry point: ``run_graph`` / ``show_thread`` / ``resume_thread`` / ``list_threads``.

CLI driver for the Lesson-QA graph. ``lesson-data graph run <slug>`` kicks off
a review thread that parks at the human gate; ``graph show <slug>`` prints the
parked state; ``graph resume <slug> --decision ...`` resumes a parked thread
with a human decision (accept / edit / defer); ``graph list`` enumerates
parked threads in the checkpointer.

Scratch-vs-commit: a bare ``run``/``improve`` invocation never writes the
tracked ``data/lessons/``, ``dist/lessons/``, or
``data/lesson_acceptance_log.jsonl``. It writes lesson/dist artifacts and the
acceptance ledger under ``store/scratch/`` (gitignored) instead. Pass
``--commit`` to write the real tracked paths.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.graph_runner import (
    K_DEFAULT_FIXER,
    K_DEFAULT_JUDGE,
    K_FIXERS,
    K_JUDGES,
    K_REPO_ROOT,
    ThreadNotFoundError,
    ThreadNotParkedError,
    _acceptance_log_path,
    _scratch_root,
    list_threads,
    resume_thread,
    run_graph,
    show_thread,
)
from lesson_builder.pipeline.lesson_acceptance_log import mark_unverified

# ---------------------------------------------------------------------------
# argparse surface
# ---------------------------------------------------------------------------


def _add_run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("run", help="Start a lesson-QA run; parks at human gate")
    p.add_argument("slug")
    p.add_argument("--run-id", default=None)
    p.add_argument("--repo-root", default=None, help="defaults to the real repo root")
    p.add_argument(
        "--commit",
        action="store_true",
        help=(
            "write exported lesson/dist/ledger to the tracked repo paths instead of "
            "store/scratch/ (default: scratch, never touches tracked data/lessons, "
            "dist/lessons, or data/lesson_acceptance_log.jsonl)"
        ),
    )
    p.add_argument(
        "--fixer",
        choices=sorted(K_FIXERS),
        default=K_DEFAULT_FIXER,
        help=(
            "how blocking issues are repaired in the fix/regenerate loop: "
            "'codex' authors real repairs via the author agent (live LLM) "
            f"(default: {K_DEFAULT_FIXER})"
        ),
    )
    p.add_argument(
        "--judge",
        choices=sorted(K_JUDGES),
        default=K_DEFAULT_JUDGE,
        help=(
            "whether the reviewer-backed advisory panel runs: 'real' calls the "
            "live reviewer agent for pedagogy/objective_alignment/answer review "
            "(advisory only, never blocking pre-calibration) "
            f"(default: {K_DEFAULT_JUDGE})"
        ),
    )
    p.set_defaults(func=_cmd_run)


def _add_show_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("show", help="Show a parked or terminal thread state")
    p.add_argument("slug")
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo-root", default=None)
    p.add_argument("--full", action="store_true", help="also render the lesson under review and the regression diff")
    p.set_defaults(func=_cmd_show)


def _add_resume_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("resume", help="Resume a parked thread with a human decision")
    p.add_argument("slug")
    p.add_argument("--run-id", required=True)
    p.add_argument("--decision", required=True, choices=["accept", "edit", "defer"])
    p.add_argument("--lesson-file", default=None, help="path to edited lesson JSON for an edit decision")
    p.add_argument(
        "--override",
        action="store_true",
        help=(
            "with --decision accept: force export past remaining load-bearing blockers. "
            "Recorded as ledger status accepted_override (excluded from regression "
            "baselines), not a clean accepted baseline."
        ),
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_cmd_resume)


def _add_list_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("list", help="Enumerate threads parked at the human gate")
    p.set_defaults(func=_cmd_list)


def _cmd_run(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.lesson_qa_graph import LessonLoadError

    try:
        result = run_graph(
            args.slug,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            run_id=args.run_id,
            commit=args.commit,
            fixer_name=args.fixer,
            judge_name=args.judge,
        )
    except (FileNotFoundError, LessonLoadError):
        print(f"error: no lesson found for slug {args.slug!r}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        result = show_thread(
            args.slug,
            args.run_id,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            full=args.full,
        )
    except ThreadNotFoundError as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    decision: dict[str, Any] = {"status": args.decision}
    if args.decision == "edit" and args.lesson_file:
        decision["lesson"] = json.loads(Path(args.lesson_file).read_text())
    if args.decision == "accept" and args.override:
        decision["override"] = True
    try:
        result = resume_thread(
            args.slug,
            args.run_id,
            decision,
            repo_root=Path(args.repo_root) if args.repo_root else None,
        )
    except (ThreadNotFoundError, ThreadNotParkedError) as exc:
        print(f"error: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = list_threads()
    if not rows:
        print("no parked threads")
        return 0
    header = f"{'slug':<30} {'run_id':<14} {'park_status':<10} {'#blocking':<10} signoff_score"
    print(header)
    for row in rows:
        print(
            f"{row['slug']:<30} {row['run_id']:<14} {str(row['park_status']):<10} "
            f"{row['blocking_count']:<10} {row['signoff_score']}"
        )
    return 0


def add_parsers(parent: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the graph CLI subcommands into an existing argparse parent."""
    graph_parser = parent.add_parser("graph", help="Lesson-QA graph driver")
    graph_sub = graph_parser.add_subparsers(dest="graph_command", required=True)
    _add_run_parser(graph_sub)
    _add_show_parser(graph_sub)
    _add_resume_parser(graph_sub)
    _add_list_parser(graph_sub)

    improve_parser = parent.add_parser("improve", help="Iterative improvement flow")
    improve_parser.add_argument("text", help="Free-text improvement request")
    improve_parser.add_argument("--slug", default=None, help="Override target slug")
    improve_parser.add_argument("--add-exercise", action="store_true")
    improve_parser.add_argument("--bloom", default=None)
    improve_parser.add_argument("--count", type=int, default=1)
    improve_parser.add_argument("--repo-root", default=None)
    improve_parser.add_argument(
        "--commit",
        action="store_true",
        help="write exported lesson/dist/ledger to the tracked repo paths instead of store/scratch/",
    )
    improve_parser.set_defaults(func=_cmd_improve)
    _add_author_parser(parent)
    _add_import_parser(parent)
    _add_curriculum_parser(parent)
    _add_ledger_parser(parent)
    _add_terminology_parser(parent)
    _add_chat_parser(parent)


def _cmd_improve(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.improvement_flow import run_improvement_flow

    result = run_improvement_flow(
        args.text,
        repo_root=Path(args.repo_root) if args.repo_root else K_REPO_ROOT,
        slug=args.slug,
        add_exercise=args.add_exercise,
        bloom=args.bloom,
        count=args.count,
        commit=args.commit,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_author_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "author",
        help="Cold-author a lesson from requirements (Journey 1: nothing -> draft)",
    )
    p.add_argument("slug", help="Concept slug with requirements but no lesson file")
    p.add_argument("--run-id", default=None)
    p.add_argument("--repo-root", default=None)
    p.add_argument(
        "--commit",
        action="store_true",
        help="write exported lesson/dist/ledger to the tracked repo paths instead of store/scratch/",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing data/lessons/<slug>.json (default: refuse, point to improve)",
    )
    p.add_argument("--fixer", choices=sorted(K_FIXERS), default=K_DEFAULT_FIXER)
    p.add_argument("--judge", choices=sorted(K_JUDGES), default=K_DEFAULT_JUDGE)
    p.set_defaults(func=_cmd_author)


def _cmd_author(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.cold_author import cold_author_flow

    result = cold_author_flow(
        args.slug,
        repo_root=Path(args.repo_root) if args.repo_root else K_REPO_ROOT,
        run_id=args.run_id,
        commit=args.commit,
        force=args.force,
        fixer_name=args.fixer,
        judge_name=args.judge,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_import_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "import",
        help="Import an external lesson file so it can be improved/QA'd (Journey 2)",
    )
    p.add_argument("file", nargs="?", help="Path to the lesson JSON file to import")
    p.add_argument("--slug", default=None, help="Override the slug (defaults to the file stem)")
    p.add_argument(
        "--status",
        default=None,
        choices=["imported_unverified", "accepted", "curated"],
        help="override the acceptance-ledger status (default: imported_unverified)",
    )
    p.add_argument("--repo-root", default=None)
    p.add_argument(
        "--commit",
        action="store_true",
        help="write to tracked data/lessons/ and the ledger (default: store/scratch/)",
    )
    p.set_defaults(func=_cmd_import)

    regenerate = sub.add_parser(
        "regenerate-dist",
        help="Regenerate dist/lessons/*.json from data/lessons/*.json",
    )
    regenerate.add_argument("--repo-root", default=None)
    regenerate.set_defaults(func=_cmd_regenerate_dist)


def _cmd_import(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.lesson_import import import_lesson

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    if not args.file:
        raise SystemExit("provide a lesson file to import")
    # Scratch-vs-commit mirrors run_graph: a bare import never dirties the tracked
    # tree; without --commit it writes under store/scratch/ (gitignored).
    output_root = repo_root if args.commit else _scratch_root(repo_root)
    result = import_lesson(
        Path(args.file),
        slug=args.slug,
        status=args.status or "imported_unverified",
        repo_root=repo_root,
        output_root=output_root,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _cmd_regenerate_dist(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.lesson_import import regenerate_dist

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    summary = regenerate_dist(repo_root)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _add_curriculum_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the curriculum CLI: increment (default) + bootstrap path.

    Backwards compat: ``curriculum <text>`` still routes to increment (the
    existing Phase-3 behavior). ``curriculum bootstrap`` is the Phase-2
    whole-course path (detected by the literal text ``bootstrap``).
    ``curriculum increment <text>`` is also accepted explicitly via
    ``curriculum increment:<text>``.
    """
    p = sub.add_parser("curriculum", help="Draft or commit curriculum artifacts")
    p.add_argument(
        "text",
        nargs="?",
        help=(
            "Curriculum request (increment mode), or 'bootstrap' for Phase-2 "
            "whole-course mode"
        ),
    )
    p.add_argument("--slug", default=None, help="Override target slug (increment mode)")
    p.add_argument("--run-id", default=None)
    p.add_argument("--repo-root", default=None)
    p.add_argument("--commit-draft", default=None, help="Commit an approved draft directory")
    p.set_defaults(func=_cmd_curriculum)


def _cmd_curriculum(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT

    if args.text == "bootstrap":
        return _cmd_curriculum_bootstrap(args, repo_root=repo_root)

    from lesson_builder.pipeline.curriculum_design import (
        IncrementCommitResult,
        IncrementResult,
        commit_increment_draft,
        run_increment,
    )

    result: IncrementCommitResult | IncrementResult
    if args.commit_draft:
        result = commit_increment_draft(
            repo_root=repo_root,
            draft_path=Path(args.commit_draft),
        )
    else:
        if args.text is None:
            raise SystemExit("curriculum text is required unless --commit-draft is provided")
        result = run_increment(
            args.text,
            repo_root=repo_root,
            slug=args.slug,
            run_id=args.run_id,
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _cmd_curriculum_bootstrap(
    args: argparse.Namespace, *, repo_root: Path
) -> int:
    from lesson_builder.pipeline.curriculum_design import (
        BootstrapCommitResult,
        BootstrapResult,
        commit_bootstrap_draft,
        run_bootstrap,
    )

    result: BootstrapCommitResult | BootstrapResult
    if args.commit_draft:
        result = commit_bootstrap_draft(
            repo_root=repo_root,
            draft_path=Path(args.commit_draft),
        )
    else:
        result = run_bootstrap(
            repo_root=repo_root,
            run_id=args.run_id,
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_ledger_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ledger_parser = sub.add_parser("ledger", help="Acceptance-ledger maintenance")
    ledger_sub = ledger_parser.add_subparsers(dest="ledger_command", required=True)

    mark_unverified_parser = ledger_sub.add_parser(
        "mark-unverified",
        help="Demote a slug's regression baseline by appending an imported_unverified entry",
    )
    mark_unverified_parser.add_argument("slug")
    mark_unverified_parser.add_argument("--reason", required=True, help="why the baseline is untrusted")
    mark_unverified_parser.add_argument("--reviewer", default="system")
    mark_unverified_parser.add_argument("--repo-root", default=None)
    mark_unverified_parser.set_defaults(func=_cmd_ledger_mark_unverified)


def _cmd_ledger_mark_unverified(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    entry = mark_unverified(
        _acceptance_log_path(repo_root),
        args.slug,
        reviewer=args.reviewer,
        reason=args.reason,
    )
    print(json.dumps(entry.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_chat_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "chat",
        help="Conversational terminal REPL over the lesson chat controller",
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_cmd_chat)


def _cmd_chat(args: argparse.Namespace) -> int:
    from lesson_builder.chat.repl import run_repl

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    run_repl(repo_root)
    return 0


def _add_terminology_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the terminology CLI: audit + markdown-backed ban maintenance."""
    term_parser = sub.add_parser(
        "terminology",
        help="Terminology style-guide audit and maintenance",
    )
    term_sub = term_parser.add_subparsers(dest="terminology_command", required=True)

    audit_parser = term_sub.add_parser(
        "audit",
        help="Scan lesson JSON files for banned phrases and prose tells",
    )
    audit_parser.add_argument(
        "paths",
        nargs="*",
        help="lesson JSON files to scan (defaults to all dist/lessons/*.json)",
    )
    audit_parser.add_argument("--repo-root", default=None)
    audit_parser.add_argument(
        "--style-guide",
        default=None,
        help="path to terminology-style-guide.md (defaults to docs/terminology-style-guide.md)",
    )
    audit_parser.add_argument(
        "--summary",
        action="store_true",
        help="summarize counts by phrase instead of emitting per-finding rows",
    )
    audit_parser.set_defaults(func=_cmd_terminology_audit)

    list_parser = term_sub.add_parser(
        "list",
        help="List pinned terms, hard bans, and prose tells from the markdown style guide",
    )
    list_parser.add_argument("--repo-root", default=None)
    list_parser.add_argument("--style-guide", default=None)
    list_parser.set_defaults(func=_cmd_terminology_list)

    pin_parser = term_sub.add_parser(
        "pin",
        help="Add or update one pinned term in the markdown Active rules table",
    )
    pin_parser.add_argument("concept")
    pin_parser.add_argument("term")
    pin_parser.add_argument("--also-ok", default="—")
    pin_parser.add_argument("--avoid", default="—")
    pin_parser.add_argument("--level", default="all levels")
    pin_parser.add_argument("--repo-root", default=None)
    pin_parser.add_argument("--style-guide", default=None)
    pin_parser.set_defaults(func=_cmd_terminology_pin)

    ban_parser = term_sub.add_parser(
        "ban",
        help="Add a deterministic hard-ban phrase to the markdown style guide",
    )
    ban_parser.add_argument("phrase")
    ban_parser.add_argument("--repo-root", default=None)
    ban_parser.add_argument("--style-guide", default=None)
    ban_parser.set_defaults(func=_cmd_terminology_ban)

    unban_parser = term_sub.add_parser(
        "unban",
        help="Remove a deterministic hard-ban phrase from the markdown style guide",
    )
    unban_parser.add_argument("phrase")
    unban_parser.add_argument("--repo-root", default=None)
    unban_parser.add_argument("--style-guide", default=None)
    unban_parser.set_defaults(func=_cmd_terminology_unban)


def _cmd_terminology_audit(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.checks.validators.terminology import terminology_audit
    from lesson_builder.pipeline.terminology import (
        load_terminology_bans,
        style_guide_path_for_repo,
    )

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    style_guide_path = _terminology_style_guide_path(args, repo_root, style_guide_path_for_repo)
    bans = load_terminology_bans(style_guide_path)

    paths: list[Path] = []
    if args.paths:
        paths = [Path(p) for p in args.paths]
    else:
        dist_root = repo_root / "dist" / "lessons"
        if not dist_root.exists():
            # Fall back to data/lessons when dist has not been generated.
            dist_root = repo_root / "data" / "lessons"
        paths = sorted(dist_root.glob("*.json"))

    all_rows: list[dict[str, Any]] = []
    for path in paths:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        rows = terminology_audit(lesson, bans)
        for row in rows:
            row["slug"] = lesson.get("concept_slug") or lesson.get("key") or path.stem
        all_rows.extend(rows)

    if args.summary:
        summary: dict[tuple[str, str], int] = {}
        for row in all_rows:
            key = (row.get("check_id", "?"), row.get("phrase", "?"))
            summary[key] = summary.get(key, 0) + 1
        print(json.dumps(
            [{"check_id": c, "phrase": p, "count": n} for (c, p), n in sorted(summary.items())],
            indent=2,
            ensure_ascii=False,
        ))
    else:
        print(json.dumps(all_rows, indent=2, ensure_ascii=False))
    return 0


def _cmd_terminology_list(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.terminology import (
        load_active_terminology_rules,
        load_terminology_bans,
        style_guide_path_for_repo,
    )

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    style_guide_path = _terminology_style_guide_path(args, repo_root, style_guide_path_for_repo)
    rules = load_active_terminology_rules(style_guide_path)
    bans = load_terminology_bans(style_guide_path)
    print(json.dumps(
        {
            "active_rules": [rule.model_dump() for rule in rules],
            "bans": bans.model_dump(),
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


def _cmd_terminology_pin(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.terminology import (
        ActiveTerminologyRule,
        load_active_terminology_rules,
        style_guide_path_for_repo,
        upsert_active_terminology_rule,
    )

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    style_guide_path = _terminology_style_guide_path(args, repo_root, style_guide_path_for_repo)
    rule = ActiveTerminologyRule(
        concept=args.concept.strip(),
        pinned_term=args.term.strip(),
        also_ok=args.also_ok.strip(),
        avoid_banned=args.avoid.strip(),
        level_note=args.level.strip(),
    )
    upsert_active_terminology_rule(style_guide_path, rule)
    rules = load_active_terminology_rules(style_guide_path)
    print(json.dumps([item.model_dump() for item in rules], indent=2, ensure_ascii=False))
    return 0


def _cmd_terminology_ban(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.terminology import (
        load_terminology_bans,
        style_guide_path_for_repo,
        write_terminology_bans,
    )

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    style_guide_path = _terminology_style_guide_path(args, repo_root, style_guide_path_for_repo)
    bans = load_terminology_bans(style_guide_path)
    phrase = args.phrase.strip()
    if phrase and phrase.lower() not in {item.lower() for item in bans.banned_phrases}:
        bans.banned_phrases.append(phrase)
        write_terminology_bans(style_guide_path, bans)
    print(json.dumps(bans.model_dump(), indent=2, ensure_ascii=False))
    return 0


def _cmd_terminology_unban(args: argparse.Namespace) -> int:
    from lesson_builder.pipeline.terminology import (
        TerminologyBans,
        load_terminology_bans,
        style_guide_path_for_repo,
        write_terminology_bans,
    )

    repo_root = Path(args.repo_root) if args.repo_root else K_REPO_ROOT
    style_guide_path = _terminology_style_guide_path(args, repo_root, style_guide_path_for_repo)
    bans = load_terminology_bans(style_guide_path)
    phrase = args.phrase.strip().lower()
    updated = TerminologyBans(
        banned_phrases=[item for item in bans.banned_phrases if item.lower() != phrase],
        prose_tells=bans.prose_tells,
    )
    if updated != bans:
        write_terminology_bans(style_guide_path, updated)
    print(json.dumps(updated.model_dump(), indent=2, ensure_ascii=False))
    return 0


def _terminology_style_guide_path(
    args: argparse.Namespace,
    repo_root: Path,
    resolver: Callable[[Path], Path],
) -> Path:
    if args.style_guide:
        return Path(args.style_guide)
    return resolver(repo_root)
