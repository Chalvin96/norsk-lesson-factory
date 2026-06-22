"""Entry point: ``extract_learner_text`` / ``load_terminology_bans``.

Shared learner-facing text extraction used by BOTH the live gate
(``terminology_check``) and the audit CLI. The extraction walks the same span
tree for both call sites so the audit baseline is directly comparable to the
live findings. ``extract_learner_text`` returns location metadata (text + path
+ unit_id + context_kind), not bare strings, so findings carry actionable fix
hints.

``docs/terminology-style-guide.md`` is the single source of truth. Its fenced
``bans`` block holds ``ban:`` lines (hard-banned strings) and ``prose-tell:``
lines (literal generated-prose phrases). The ban list is a STRICT SUBSET of the
"Guidance / hard bans" column in the same guide. The appositive-density pattern
(``"X, meaning Y"`` / ``"X, which means Y"`` repeated above a threshold) is a
linter constant, not config data.

Not a check itself — the deterministic check lives in
``checks/validators/terminology.py``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

K_STYLE_GUIDE_RELATIVE_PATH = Path("docs") / "terminology-style-guide.md"
K_STYLE_GUIDE_PATH = Path(__file__).resolve().parents[3] / K_STYLE_GUIDE_RELATIVE_PATH
K_BANS_BLOCK_START = "```bans"
K_BANS_BLOCK_END = "```"

ContextKind = Literal[
    "section_prose",
    "section_rule",
    "section_example",
    "section_list_item",
    "table_header",
    "table_cell",
    "callout",
    "exercise_prompt",
    "exercise_explanation",
    "option_text",
    "option_why",
    "judge_payload",
    "feedback",
    "match_text",
    "categorize_text",
    "build_token",
]


class LearnerText(BaseModel):
    """One extracted learner-facing text fragment with its location."""

    text: str
    path: str
    unit_id: str
    context_kind: ContextKind


class TerminologyBans(BaseModel):
    """Hard-banned phrases + literal prose-tell strings parsed from the guide."""

    banned_phrases: list[str] = Field(default_factory=list)
    prose_tells: list[str] = Field(default_factory=list)


class ActiveTerminologyRule(BaseModel):
    """One pinned terminology preference from the Active rules table."""

    concept: str
    pinned_term: str
    also_ok: str = "—"
    avoid_banned: str = "—"
    level_note: str = "all levels"


# ---------------------------------------------------------------------------
# Shared learner-facing text extraction
# ---------------------------------------------------------------------------


def extract_learner_text(lesson: dict[str, Any]) -> list[LearnerText]:
    """Walk the lesson span tree and return every learner-facing text fragment.

    Returns location-tagged fragments (text + path + unit_id + context_kind) so
    findings can name the exact block/exercise/option that surfaced a term. The
    SAME function feeds the live gate and the audit CLI so the two produce
    identical findings for the same lesson.

    Scans: section prose/rule/example/list blocks, table headers/cells, callouts,
    exercise prompts/explanations, option text + why, judge payloads, feedback,
    match/categorize/build tokens. Excludes ids, bucket ids, objective keys.
    """
    fragments: list[LearnerText] = []
    for element in lesson.get("elements", []) or []:
        if not isinstance(element, dict):
            continue
        kind = element.get("kind") or element.get("element_kind")
        if kind == "section":
            fragments.extend(_extract_section(element))
        elif kind == "exercise":
            fragments.extend(_extract_exercise(element))
    return fragments


# ---------------------------------------------------------------------------
# Ban-list loader/editor (cached, mirrors load_rubric_floors)
# ---------------------------------------------------------------------------


def load_terminology_bans(path: str | Path | None = None) -> TerminologyBans:
    """Load the fenced ``bans`` block from the markdown style guide.

    The gate runs ~5x per lesson (initial + fix loop + regen + post-edit), so a
    per-call disk read in that hot loop is the top perf risk. Mirrors
    ``load_rubric_floors`` with a path-keyed cache.
    """
    resolved = Path(path).resolve() if path else K_STYLE_GUIDE_PATH
    return _load_bans_cached(resolved)


@lru_cache(maxsize=8)
def _load_bans_cached(resolved: Path) -> TerminologyBans:
    if not resolved.exists():
        return TerminologyBans()
    return parse_terminology_bans(resolved.read_text(encoding="utf-8"))


def style_guide_path_for_repo(repo_root: str | Path | None = None) -> Path:
    """Resolve the terminology style guide for a specific repo root."""
    if repo_root is None:
        return K_STYLE_GUIDE_PATH
    return Path(repo_root) / K_STYLE_GUIDE_RELATIVE_PATH


def parse_terminology_bans(markdown: str) -> TerminologyBans:
    """Parse the fenced ``bans`` block from the terminology style guide."""
    lines = _bans_block_lines(markdown)
    banned_phrases: list[str] = []
    prose_tells: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("ban:"):
            banned_phrases.append(line.removeprefix("ban:").strip())
        elif line.startswith("prose-tell:"):
            prose_tells.append(line.removeprefix("prose-tell:").strip())
    return TerminologyBans(
        banned_phrases=[phrase for phrase in banned_phrases if phrase],
        prose_tells=[phrase for phrase in prose_tells if phrase],
    )


def format_terminology_bans_block(bans: TerminologyBans, existing_lines: list[str] | None = None) -> str:
    """Render the fenced ``bans`` block, preserving non-CRUD lines when present."""
    preserved = [
        line
        for line in (existing_lines or [])
        if not line.strip().startswith(("ban:", "prose-tell:"))
    ]
    lines = [K_BANS_BLOCK_START]
    lines.extend(f"ban: {phrase}" for phrase in bans.banned_phrases)
    lines.extend(f"prose-tell: {phrase}" for phrase in bans.prose_tells)
    lines.extend(preserved)
    lines.append(K_BANS_BLOCK_END)
    return "\n".join(lines)


def write_terminology_bans(path: str | Path, bans: TerminologyBans) -> None:
    """Replace the style guide's fenced ``bans`` block and clear the loader cache."""
    resolved = Path(path).resolve()
    markdown = resolved.read_text(encoding="utf-8")
    start, end, lines = _bans_block_bounds(markdown)
    rendered = format_terminology_bans_block(bans, lines)
    updated = f"{markdown[:start]}{rendered}{markdown[end:]}"
    resolved.write_text(updated, encoding="utf-8")
    _load_bans_cached.cache_clear()


def load_active_terminology_rules(path: str | Path | None = None) -> list[ActiveTerminologyRule]:
    """Load the Active rules markdown table from the style guide."""
    resolved = Path(path).resolve() if path else K_STYLE_GUIDE_PATH
    if not resolved.exists():
        return []
    markdown = resolved.read_text(encoding="utf-8")
    return parse_active_terminology_rules(markdown)


def parse_active_terminology_rules(markdown: str) -> list[ActiveTerminologyRule]:
    """Parse the style guide's Active rules pipe table."""
    rows = _active_rules_table_rows(markdown)
    rules: list[ActiveTerminologyRule] = []
    for row in rows:
        cells = _split_markdown_row(row)
        if len(cells) != 5:
            continue
        rules.append(
            ActiveTerminologyRule(
                concept=cells[0],
                pinned_term=_strip_markdown_emphasis(cells[1]),
                also_ok=cells[2],
                avoid_banned=cells[3],
                level_note=cells[4],
            )
        )
    return rules


def upsert_active_terminology_rule(path: str | Path, rule: ActiveTerminologyRule) -> None:
    """Add or replace one row in the Active rules table."""
    _validate_markdown_table_rule(rule)
    resolved = Path(path).resolve()
    markdown = resolved.read_text(encoding="utf-8")
    start, end, rows = _active_rules_table_bounds(markdown)
    rendered_rows = _upsert_rule_row(rows, rule)
    updated = f"{markdown[:start]}{''.join(rendered_rows)}{markdown[end:]}"
    resolved.write_text(updated, encoding="utf-8")


def _bans_block_lines(markdown: str) -> list[str]:
    _, _, lines = _bans_block_bounds(markdown)
    return lines


def _bans_block_bounds(markdown: str) -> tuple[int, int, list[str]]:
    start = markdown.find(K_BANS_BLOCK_START)
    if start < 0:
        return len(markdown), len(markdown), []
    content_start = start + len(K_BANS_BLOCK_START)
    if markdown[content_start:content_start + 1] == "\n":
        content_start += 1
    end = markdown.find(K_BANS_BLOCK_END, content_start)
    if end < 0:
        return start, len(markdown), markdown[content_start:].splitlines()
    content = markdown[content_start:end]
    block_end = end + len(K_BANS_BLOCK_END)
    return start, block_end, content.splitlines()


def _active_rules_table_rows(markdown: str) -> list[str]:
    _, _, rows = _active_rules_table_bounds(markdown)
    return rows[2:]


def _active_rules_table_bounds(markdown: str) -> tuple[int, int, list[str]]:
    header = "| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |"
    start = markdown.find(header)
    if start < 0:
        return len(markdown), len(markdown), []
    end = start
    rows: list[str] = []
    while end < len(markdown):
        next_end = markdown.find("\n", end)
        if next_end < 0:
            next_end = len(markdown)
            line = markdown[end:next_end]
            line_end = next_end
        else:
            line = markdown[end:next_end]
            line_end = next_end + 1
        if not line.startswith("|"):
            break
        rows.append(markdown[end:line_end])
        end = line_end
    return start, end, rows


def _upsert_rule_row(rows: list[str], rule: ActiveTerminologyRule) -> list[str]:
    if len(rows) < 2:
        rows = [
            "| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |\n",
            "|---|---|---|---|---|\n",
        ]
    rendered = _render_rule_row(rule)
    target = rule.concept.lower()
    output = rows[:2]
    replaced = False
    for row in rows[2:]:
        cells = _split_markdown_row(row)
        if cells and cells[0].lower() == target:
            output.append(rendered)
            replaced = True
        else:
            output.append(row)
    if not replaced:
        output.append(rendered)
    return output


def _render_rule_row(rule: ActiveTerminologyRule) -> str:
    return (
        f"| {rule.concept} | **{rule.pinned_term}** | {rule.also_ok or '—'} | "
        f"{rule.avoid_banned or '—'} | {rule.level_note or 'all levels'} |\n"
    )


def _validate_markdown_table_rule(rule: ActiveTerminologyRule) -> None:
    fields = {
        "concept": rule.concept,
        "pinned_term": rule.pinned_term,
        "also_ok": rule.also_ok,
        "avoid_banned": rule.avoid_banned,
        "level_note": rule.level_note,
    }
    unsafe = [name for name, value in fields.items() if "|" in value]
    if unsafe:
        field_names = ", ".join(unsafe)
        raise ValueError(f"Active rules table fields cannot contain '|': {field_names}")


def _split_markdown_row(row: str) -> list[str]:
    stripped = row.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _strip_markdown_emphasis(text: str) -> str:
    return text.strip().replace("**", "")


# ---------------------------------------------------------------------------
# Private extraction helpers
# ---------------------------------------------------------------------------


def _extract_section(section: dict[str, Any]) -> list[LearnerText]:
    section_id = str(section.get("id", ""))
    fragments: list[LearnerText] = []
    for block_index, block in enumerate(section.get("blocks", []) or []):
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        base_path = f"elements[{section_id}].blocks[{block_index}]"
        if kind == "paragraph" or kind == "heading":
            fragments.extend(
                _from_spans(block.get("spans") or [], base_path, section_id, "section_prose")
            )
        elif kind == "rule":
            for stmt_index, statement in enumerate(block.get("statement") or []):
                if isinstance(statement, list):
                    fragments.extend(
                        _from_spans(statement, f"{base_path}.statement[{stmt_index}]", section_id, "section_rule")
                    )
                elif isinstance(statement, dict):
                    fragments.extend(
                        _from_spans([statement], f"{base_path}.statement[{stmt_index}]", section_id, "section_rule")
                    )
        elif kind in ("example", "examples"):
            fragments.extend(_from_example_block(block, base_path, section_id))
        elif kind == "list":
            for item_index, item in enumerate(block.get("items") or []):
                if isinstance(item, list):
                    fragments.extend(
                        _from_spans(item, f"{base_path}.items[{item_index}]", section_id, "section_list_item")
                    )
        elif kind == "table":
            for header_index, header in enumerate(block.get("headers") or []):
                fragments.extend(
                    _from_spans(header, f"{base_path}.headers[{header_index}]", section_id, "table_header")
                )
            for row_index, row in enumerate(block.get("rows") or []):
                for cell_index, cell in enumerate(row):
                    fragments.extend(
                        _from_spans(cell, f"{base_path}.rows[{row_index}][{cell_index}]", section_id, "table_cell")
                    )
        elif kind == "callout":
            for inner_index, inner in enumerate(block.get("blocks") or []):
                if isinstance(inner, dict) and (inner.get("kind") in ("paragraph", "heading")):
                    fragments.extend(
                        _from_spans(
                            inner.get("spans") or [],
                            f"{base_path}.callout[{inner_index}]",
                            section_id,
                            "callout",
                        )
                    )
        elif kind == "word_list":
            for item_index, item in enumerate(block.get("items") or []):
                if isinstance(item, dict):
                    term = item.get("term")
                    form = item.get("form")
                    if isinstance(term, str):
                        fragments.append(
                            LearnerText(
                                text=term,
                                path=f"{base_path}.items[{item_index}].term",
                                unit_id=section_id,
                                context_kind="section_prose",
                            )
                        )
                    if isinstance(form, str):
                        fragments.append(
                            LearnerText(
                                text=form,
                                path=f"{base_path}.items[{item_index}].form",
                                unit_id=section_id,
                                context_kind="section_prose",
                            )
                        )
    return fragments


def _from_example_block(
    block: dict[str, Any], base_path: str, section_id: str
) -> list[LearnerText]:
    fragments: list[LearnerText] = []
    if "no" in block:
        fragments.extend(
            _from_spans(block["no"], f"{base_path}.no", section_id, "section_example")
        )
    if "en" in block:
        fragments.extend(
            _from_spans(block["en"], f"{base_path}.en", section_id, "section_example")
        )
    for item_index, item in enumerate(block.get("items") or []):
        if isinstance(item, dict):
            if "no" in item:
                fragments.extend(
                    _from_spans(item["no"], f"{base_path}.items[{item_index}].no", section_id, "section_example")
                )
            if "en" in item:
                fragments.extend(
                    _from_spans(item["en"], f"{base_path}.items[{item_index}].en", section_id, "section_example")
                )
    return fragments


def _extract_exercise(exercise: dict[str, Any]) -> list[LearnerText]:
    ex_id = str(exercise.get("id", ""))
    fragments: list[LearnerText] = []
    base_path = f"elements[{ex_id}]"
    fragments.extend(
        _from_spans(exercise.get("prompt") or [], f"{base_path}.prompt", ex_id, "exercise_prompt")
    )
    fragments.extend(
        _from_spans(
            exercise.get("explanation") or [], f"{base_path}.explanation", ex_id, "exercise_explanation"
        )
    )
    payload = exercise.get("payload") or {}
    operation = exercise.get("operation")
    if operation == "choose":
        for opt_index, option in enumerate(payload.get("options") or []):
            if isinstance(option, dict):
                if isinstance(option.get("text"), str):
                    fragments.append(
                        LearnerText(
                            text=option["text"],
                            path=f"{base_path}.payload.options[{opt_index}].text",
                            unit_id=ex_id,
                            context_kind="option_text",
                        )
                    )
                if isinstance(option.get("why"), str):
                    fragments.append(
                        LearnerText(
                            text=option["why"],
                            path=f"{base_path}.payload.options[{opt_index}].why",
                            unit_id=ex_id,
                            context_kind="option_why",
                        )
                    )
        stem = payload.get("stem")
        if isinstance(stem, list):
            fragments.extend(
                _from_spans(stem, f"{base_path}.payload.stem", ex_id, "exercise_prompt")
            )
    elif operation == "judge":
        fragments.extend(
            _from_spans(payload.get("sentence") or [], f"{base_path}.payload.sentence", ex_id, "judge_payload")
        )
        if isinstance(payload.get("feedback"), str):
            fragments.append(
                LearnerText(
                    text=payload["feedback"],
                    path=f"{base_path}.payload.feedback",
                    unit_id=ex_id,
                    context_kind="feedback",
                )
            )
    elif operation == "recall_fill":
        for seg_index, segment in enumerate(payload.get("segments") or []):
            if not isinstance(segment, dict):
                continue
            if segment.get("kind") == "span":
                fragments.extend(
                    _from_spans(
                        segment.get("spans") or [],
                        f"{base_path}.payload.segments[{seg_index}].spans",
                        ex_id,
                        "exercise_prompt",
                    )
                )
    elif operation == "match_pairs":
        for side in ("left", "right"):
            for item_index, item in enumerate(payload.get(side) or []):
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    fragments.append(
                        LearnerText(
                            text=item["text"],
                            path=f"{base_path}.payload.{side}[{item_index}].text",
                            unit_id=ex_id,
                            context_kind="match_text",
                        )
                    )
    elif operation == "categorize":
        for item_index, item in enumerate(payload.get("items") or []):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                fragments.append(
                    LearnerText(
                        text=item["text"],
                        path=f"{base_path}.payload.items[{item_index}].text",
                        unit_id=ex_id,
                        context_kind="categorize_text",
                    )
                )
    elif operation == "build":
        for token_index, token in enumerate(payload.get("tokens") or []):
            if isinstance(token, dict) and isinstance(token.get("text"), str):
                fragments.append(
                    LearnerText(
                        text=token["text"],
                        path=f"{base_path}.payload.tokens[{token_index}].text",
                        unit_id=ex_id,
                        context_kind="build_token",
                    )
                )
    elif operation == "find_fix":
        for token_index, token in enumerate(payload.get("tokens") or []):
            if isinstance(token, dict) and isinstance(token.get("text"), str):
                fragments.append(
                    LearnerText(
                        text=token["text"],
                        path=f"{base_path}.payload.tokens[{token_index}].text",
                        unit_id=ex_id,
                        context_kind="build_token",
                    )
                )
        if isinstance(payload.get("feedback"), str):
            fragments.append(
                LearnerText(
                    text=payload["feedback"],
                    path=f"{base_path}.payload.feedback",
                    unit_id=ex_id,
                    context_kind="feedback",
                )
            )
    return fragments


def _from_spans(
    spans: Any, path: str, unit_id: str, context_kind: ContextKind
) -> list[LearnerText]:
    fragments: list[LearnerText] = []
    if not isinstance(spans, list):
        return fragments
    text = _spans_to_text(spans)
    if text:
        fragments.append(
            LearnerText(text=text, path=path, unit_id=unit_id, context_kind=context_kind)
        )
    return fragments


def _spans_to_text(spans: list[Any]) -> str:
    """Flatten a portable-text span list to plain text, preserving order."""
    parts: list[str] = []
    for span in spans:
        if isinstance(span, dict) and isinstance(span.get("value"), str):
            parts.append(span["value"])
        elif isinstance(span, str):
            parts.append(span)
    return " ".join(parts)


__all__ = [
    "ContextKind",
    "ActiveTerminologyRule",
    "K_STYLE_GUIDE_PATH",
    "K_STYLE_GUIDE_RELATIVE_PATH",
    "LearnerText",
    "TerminologyBans",
    "extract_learner_text",
    "load_active_terminology_rules",
    "load_terminology_bans",
    "parse_active_terminology_rules",
    "parse_terminology_bans",
    "style_guide_path_for_repo",
    "upsert_active_terminology_rule",
    "write_terminology_bans",
]
