"""Entry point: `convert_blocks` maps panflute block elements to ``list[Block]``.

Follows the §4.2 normative block mapping table exactly. Every pandoc structure
with no mapping is a compile error (fail closed with a fix hint). Callout
children are recursed as blocks. Multi-block list items are rejected. Reading
units keep their learner-language spans and app-facing translation metadata
together.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from typing import Literal

import panflute

from lesson_builder.application.operations.convert_lesson_spans import convert_inlines
from lesson_builder.domain.lesson.models.blocks import Block
from lesson_builder.domain.lesson.models.blocks import CalloutBlock
from lesson_builder.domain.lesson.models.blocks import ExampleBlock
from lesson_builder.domain.lesson.models.blocks import ExampleItem
from lesson_builder.domain.lesson.models.blocks import ExamplesBlock
from lesson_builder.domain.lesson.models.blocks import ExampleTeachingRole
from lesson_builder.domain.lesson.models.blocks import HeadingBlock
from lesson_builder.domain.lesson.models.blocks import ListBlock
from lesson_builder.domain.lesson.models.blocks import ParagraphBlock
from lesson_builder.domain.lesson.models.blocks import ReadingBlock
from lesson_builder.domain.lesson.models.blocks import RuleBlock
from lesson_builder.domain.lesson.models.blocks import TableBlock
from lesson_builder.domain.lesson.models.blocks import WordListBlock
from lesson_builder.domain.lesson.models.blocks import WordListItem
from lesson_builder.formats.markdown.attributes import normalize_lang
from lesson_builder.formats.markdown.attributes import normalize_text

K_BLOCKS_SECTION_ROLES: frozenset[str] = frozenset({"orient", "model", "contrast", "recap"})
K_BLOCKS_EXAMPLE_TEACHING_ROLES: frozenset[str] = frozenset({"model", "incorrect", "caution"})
K_BLOCKS_INCORRECT_EXAMPLE_RE = re.compile(
    r"^(?:✗\s*|(?:incorrect(?:\s+[^:]+)?|wrong|common mistake|intended(?: neutral)? meaning|not)\s*:\s*)",
    re.IGNORECASE,
)
K_BLOCKS_CAUTION_EXAMPLE_RE = re.compile(r"^(?:(?:not normally|not standard)\s*:\s*)", re.IGNORECASE)
K_BLOCKS_EXAMPLE_STATUS_RE = re.compile(
    r"^[\s*`_'\"]*(?:✗\s*|(?:incorrect(?:\s+[^:]+)?|wrong|common mistake|intended(?: neutral)? meaning|"
    r"not normally|not standard|not)\s*:\s*)",
    re.IGNORECASE,
)
K_BLOCKS_LEGACY_EXAMPLE_LABEL_RE = re.compile(
    r"^(?P<label>incorrect(?:\s+[^:]+)?|wrong|not normally|not standard|not)\s*:$",
    re.IGNORECASE,
)

K_BLOCKS_CALLOUT_LEVELS: dict[str, Literal["tip", "warning", "note"]] = {
    "tip": "tip",
    "warning": "warning",
    "note": "note",
}

K_BLOCKS_LANG_LIST_SEP = ","

K_BLOCKS_SNIPPET_LIMIT = 80
K_BLOCKS_MAX_PANDOC_HEADING_LEVEL = 3


def convert_blocks(
    blocks: Iterable[Any],
    warnings: list[str],
    default_lang: str = "nb",
) -> list[Block]:
    """Convert a sequence of panflute block elements into ``list[Block]``."""
    source_blocks = list(blocks)
    result: list[Block] = []
    source_index = 0
    while source_index < len(source_blocks):
        el = source_blocks[source_index]
        inherited_role = _find_legacy_example_label_role(el)
        if inherited_role is not None and source_index + 1 < len(source_blocks):
            next_el = source_blocks[source_index + 1]
            if _is_example_container(next_el):
                result.append(
                    _convert_one(
                        next_el,
                        warnings,
                        default_lang,
                        inherited_example_role=inherited_role,
                    )
                )
                source_index += 2
                continue
        result.append(_convert_one(el, warnings, default_lang))
        source_index += 1
    return result


def _convert_one(
    el: object,
    warnings: list[str],
    default_lang: str,
    *,
    inherited_example_role: ExampleTeachingRole | None = None,
) -> Block:
    """Convert a single panflute block element into a ``Block``."""
    if isinstance(el, panflute.Header):
        return _convert_header(el, warnings, default_lang)
    if isinstance(el, panflute.Para):
        return _convert_paragraph(el, warnings, default_lang)
    if isinstance(el, panflute.Div):
        return _convert_div(
            el,
            warnings,
            default_lang,
            inherited_example_role=inherited_example_role,
        )
    if isinstance(el, (panflute.BulletList, panflute.OrderedList)):
        return _convert_list(el, warnings, default_lang)
    raise ValueError(
        f"Unsupported block element {type(el).__name__}. "
        f"Fix: encode this content using one of the 10 mapped kinds per §4.2. "
        f"Snippet: {_snippet(el)}"
    )


# ── Header ───────────────────────────────────────────────────────────────


def _convert_header(
    el: panflute.Header,
    warnings: list[str],
    default_lang: str,
) -> HeadingBlock:
    """Convert a pandoc Header (## or ###) to a ``HeadingBlock``.

    Pandoc level 2 maps to internal level 3; pandoc level 3 maps to internal
    level 4. The section-role prefix (``orient:`` etc.) is stripped from the
    heading text spans.
    """
    if el.level == 1:
        raise ValueError(
            f"Header level 1 (#) is reserved for the lesson title -- "
            f"use ## or ### for content headings. Snippet: {_snippet(el)}"
        )
    if el.level > K_BLOCKS_MAX_PANDOC_HEADING_LEVEL:
        raise ValueError(
            f"Header level {el.level} is too deep -- only ## (level 3) and "
            f"### (level 4) are supported. Snippet: {_snippet(el)}"
        )

    spans = convert_inlines(el.content, warnings, default_lang)

    # Strip the role prefix ("orient:", "model:", etc.) if present.
    spans = _strip_role_prefix(spans)

    internal_level: Literal[3, 4] = el.level + 1
    return HeadingBlock(kind="heading", level=internal_level, spans=spans)


def _strip_role_prefix(spans: list[Any]) -> list[Any]:
    """Remove a leading section-role prefix token from heading spans."""
    from lesson_builder.domain.lesson.models.inline import TextSpan

    if not spans:
        return spans
    first = spans[0]
    text = getattr(first, "value", None)
    if text is None:
        return spans
    for role in K_BLOCKS_SECTION_ROLES:
        prefix = role + ":"
        if text == prefix:
            return spans[1:] if len(spans) > 1 else []
        if text.startswith(prefix + " "):
            new_text = text[len(prefix) + 1 :]
            new_spans = list(spans)
            if new_text:
                new_spans[0] = TextSpan(kind="text", value=new_text)
            else:
                new_spans = new_spans[1:]
            return new_spans
    return spans


# ── Paragraph ────────────────────────────────────────────────────────────


def _convert_paragraph(
    el: panflute.Para,
    warnings: list[str],
    default_lang: str,
) -> ParagraphBlock:
    """Convert a pandoc Para to a ``ParagraphBlock``."""
    spans = convert_inlines(el.content, warnings, default_lang)
    return ParagraphBlock(kind="paragraph", spans=spans)


# ── Div ──────────────────────────────────────────────────────────────────


def _convert_div(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
    *,
    inherited_example_role: ExampleTeachingRole | None = None,
) -> Block:
    """Convert a pandoc Div to one of the typed-block kinds per §4.2."""
    classes = el.classes
    snippet = f"div .{'.'.join(classes) if classes else '(no class)'}"

    if "rule" in classes:
        return _convert_rule(el, warnings, default_lang)
    if "example" in classes:
        return _convert_example(
            el,
            warnings,
            default_lang,
            inherited_role=inherited_example_role,
        )
    if "examples" in classes:
        return _convert_examples(
            el,
            warnings,
            default_lang,
            inherited_role=inherited_example_role,
        )
    if "word_list" in classes:
        return _convert_word_list(el)
    if "reading" in classes:
        return _convert_reading(el, warnings, default_lang)
    if "callout" in classes:
        return _convert_callout(el, warnings, default_lang)
    if not classes and "col_langs" in el.attributes:
        return _convert_table_div(el, warnings, default_lang)

    raise ValueError(
        f"Div with classes {classes!r} has no §4.2 mapping. "
        f"Fix: use one of reading/rule/example/examples/word_list/callout, "
        f"or a bare col_langs=... wrapper for tables. "
        f"Snippet: {snippet}"
    )


def _convert_rule(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
) -> RuleBlock:
    """Convert ``::: rule`` div to ``RuleBlock``."""
    children = list(el.content)
    if len(children) != 1 or not isinstance(children[0], panflute.Para):
        raise ValueError(f"rule div must contain exactly one paragraph. Snippet: {_snippet(el)}")
    spans = convert_inlines(children[0].content, warnings, default_lang)
    return RuleBlock(kind="rule", statement=spans)


def _convert_example(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
    *,
    inherited_role: ExampleTeachingRole | None = None,
) -> ExampleBlock:
    """Convert ``::: example`` div to ``ExampleBlock`` (one no:/en: pair)."""
    pairs = _extract_example_pairs(el, warnings, default_lang, snippet="example div")
    if len(pairs) != 1:
        raise ValueError(
            f"example div must contain exactly one no:/en: pair, got {len(pairs)}. "
            f"Use ::: examples for multiple pairs. Snippet: {_snippet(el)}"
        )
    no_spans, en_spans = pairs[0]
    teaching_role = _resolve_example_teaching_role(el, no_spans, en_spans, inherited_role=inherited_role)
    return ExampleBlock(
        kind="example",
        no=_strip_example_role_prefix(no_spans, teaching_role),
        en=_strip_example_role_prefix(en_spans, teaching_role),
        teaching_role=teaching_role,
    )


def _convert_examples(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
    *,
    inherited_role: ExampleTeachingRole | None = None,
) -> ExamplesBlock:
    """Convert ``::: examples`` div to ``ExamplesBlock`` (multiple pairs)."""
    pairs = _extract_example_pairs(el, warnings, default_lang, snippet="examples div")
    explicit_role = _parse_explicit_example_teaching_role(el)
    block_role = _resolve_inherited_example_teaching_role(explicit_role, inherited_role, el)
    items = []
    for no_spans, en_spans in pairs:
        teaching_role = block_role or _infer_example_teaching_role(no_spans, en_spans)
        items.append(
            ExampleItem(
                no=_strip_example_role_prefix(no_spans, teaching_role),
                en=_strip_example_role_prefix(en_spans, teaching_role),
                teaching_role=teaching_role,
            )
        )
    return ExamplesBlock(kind="examples", items=items)


def _resolve_example_teaching_role(
    el: panflute.Div,
    no_spans: list[Any],
    en_spans: list[Any],
    *,
    inherited_role: ExampleTeachingRole | None,
) -> ExampleTeachingRole:
    """Resolve one explicit or legacy-inferred example teaching role."""
    explicit_role = _parse_explicit_example_teaching_role(el)
    block_role = _resolve_inherited_example_teaching_role(explicit_role, inherited_role, el)
    return block_role or _infer_example_teaching_role(no_spans, en_spans)


def _parse_explicit_example_teaching_role(el: panflute.Div) -> ExampleTeachingRole | None:
    """Validate an optional semantic teaching role on an example container."""
    value = _optional_attribute(el, "teaching_role")
    if value is None:
        return None
    if value not in K_BLOCKS_EXAMPLE_TEACHING_ROLES:
        expected = ", ".join(sorted(K_BLOCKS_EXAMPLE_TEACHING_ROLES))
        raise ValueError(f"example teaching_role must be one of {expected}, got {value!r}. Snippet: {_snippet(el)}")
    return value  # type: ignore[return-value]


def _resolve_inherited_example_teaching_role(
    explicit_role: ExampleTeachingRole | None,
    inherited_role: ExampleTeachingRole | None,
    el: panflute.Div,
) -> ExampleTeachingRole | None:
    """Combine an explicit role with an immediately preceding legacy label."""
    if explicit_role is not None and inherited_role is not None and explicit_role != inherited_role:
        raise ValueError(
            f"example teaching_role {explicit_role!r} conflicts with preceding "
            f"{inherited_role!r} label. Snippet: {_snippet(el)}"
        )
    return explicit_role or inherited_role


def _infer_example_teaching_role(no_spans: list[Any], en_spans: list[Any]) -> ExampleTeachingRole:
    """Map legacy textual status markers to the typed contract during migration."""
    norwegian = _flatten_example_spans(no_spans).lstrip("* `_\t'\"")
    english = _flatten_example_spans(en_spans).lstrip("* `_\t'\"")
    if K_BLOCKS_CAUTION_EXAMPLE_RE.match(norwegian) or K_BLOCKS_CAUTION_EXAMPLE_RE.match(english):
        return "caution"
    if K_BLOCKS_INCORRECT_EXAMPLE_RE.match(norwegian) or K_BLOCKS_INCORRECT_EXAMPLE_RE.match(english):
        return "incorrect"
    return "model"


def _flatten_example_spans(spans: list[Any]) -> str:
    """Flatten internal spans only for legacy role migration."""
    pieces: list[str] = []
    for span in spans:
        value = getattr(span, "value", None)
        if isinstance(value, str):
            pieces.append(value)
        children = getattr(span, "children", None)
        if isinstance(children, list):
            pieces.append(_flatten_example_spans(children))
    return "".join(pieces)


def _strip_example_role_prefix(spans: list[Any], role: ExampleTeachingRole) -> list[Any]:
    """Remove legacy status text now represented by ``teaching_role``."""
    if role == "model" or not spans:
        return spans
    return _strip_example_status_spans(spans)


def _strip_example_status_spans(spans: list[Any]) -> list[Any]:
    """Remove a possibly formatted or quoted legacy status prefix."""
    updated = list(spans)
    removed_status = False
    quote_wrapper = _example_quote_wrapper(updated)
    while updated:
        first = updated[0]
        value = getattr(first, "value", None)
        if not isinstance(value, str):
            break
        stripped = _strip_example_status_value(value, quote_wrapper)
        if stripped is None:
            if removed_status:
                cleaned = _remove_example_quote(value.lstrip(), quote_wrapper)
                updated[0] = first.model_copy(update={"value": cleaned})
            break
        removed_status = True
        if stripped:
            updated[0] = first.model_copy(update={"value": stripped})
        else:
            updated.pop(0)
    return updated


def _example_quote_wrapper(spans: list[Any]) -> str | None:
    """Return a quote opened by the first text-bearing example span."""
    for span in spans:
        value = getattr(span, "value", None)
        if isinstance(value, str):
            leading = value.lstrip()
            if leading[:1] in {"'", '"'}:
                return leading[0]
    return None


def _strip_example_status_value(value: str, quote_wrapper: str | None) -> str | None:
    """Strip one status marker from a span, or return ``None`` when absent."""
    stripped = K_BLOCKS_EXAMPLE_STATUS_RE.sub("", value, count=1)
    if stripped == value:
        return None
    return _remove_example_quote(stripped.lstrip(), quote_wrapper)


def _remove_example_quote(value: str, quote_wrapper: str | None) -> str:
    """Remove the closing quote paired with a migrated status prefix."""
    if quote_wrapper is not None and value.endswith(quote_wrapper):
        return value[: -len(quote_wrapper)]
    return value


def _find_legacy_example_label_role(el: object) -> ExampleTeachingRole | None:
    """Find an exact standalone legacy role label before an example container."""
    if not isinstance(el, panflute.Para):
        return None
    label_match = K_BLOCKS_LEGACY_EXAMPLE_LABEL_RE.fullmatch(_snippet(el).strip())
    if label_match is None:
        return None
    label = label_match.group("label").casefold()
    if label in {"not normally", "not standard"}:
        return "caution"
    return "incorrect"


def _is_example_container(el: object) -> bool:
    """Return whether a Pandoc block is a singular or grouped example div."""
    return isinstance(el, panflute.Div) and bool({"example", "examples"}.intersection(el.classes))


def _extract_example_pairs(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
    snippet: str,
) -> list[tuple[list[Any], list[Any]]]:
    """Extract (no, en) span pairs from a BulletList inside an example(s) div."""
    children = list(el.content)
    blist = _find_single_bullet_list(children, snippet)
    if blist is None:
        raise ValueError(f"{snippet} must contain a single bullet list of no:/en: items. Snippet: {_snippet(el)}")

    pairs: list[tuple[list[Any], list[Any]]] = []
    current_no: list[Any] | None = None

    for item in blist.content:
        inline_children = _plain_inlines(item, snippet=f"{snippet} item")
        label, rest = _extract_label(inline_children)

        rest_spans = convert_inlines(rest, warnings, default_lang)

        if label == "no":
            if current_no is not None:
                raise ValueError(f"Consecutive no: items without en: in {snippet}. Snippet: {_snippet(el)}")
            current_no = rest_spans
        elif label == "en":
            if current_no is None:
                raise ValueError(f"en: item without preceding no: in {snippet}. Snippet: {_snippet(el)}")
            pairs.append((current_no, rest_spans))
            current_no = None
        else:
            raise ValueError(f"Items in {snippet} must start with no: or en:, got {label!r}. Snippet: {_snippet(el)}")

    if current_no is not None:
        raise ValueError(f"Dangling no: item without matching en: in {snippet}. Snippet: {_snippet(el)}")

    return pairs


def _convert_word_list(el: panflute.Div) -> WordListBlock:
    """Convert ``::: word_list`` div to ``WordListBlock``."""
    snippet_prefix = "word_list div"
    children = list(el.content)
    blist = _find_single_bullet_list(children, snippet_prefix)
    if blist is None:
        raise ValueError(f"{snippet_prefix} must contain a single bullet list. Snippet: {_snippet(el)}")

    items: list[WordListItem] = []
    for item in blist.content:
        inline_children = _plain_inlines(item, snippet=f"{snippet_prefix} item")
        kv = _parse_kv_pairs(inline_children)
        term = kv.get("term")
        form = kv.get("form")
        if term is None or form is None:
            raise ValueError(
                f"word_list item must have term: and form: keys, got {sorted(kv.keys())}. Snippet: {_snippet(el)}"
            )
        items.append(WordListItem(term=normalize_text(term), form=normalize_text(form)))

    return WordListBlock(kind="word_list", items=items)


def _convert_reading(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
) -> ReadingBlock:
    """Convert ``::: {.reading translation=...}`` to a reading unit.

    A reading unit deliberately contains one paragraph so the app can attach
    one translation to one learner-language item. Optional dialogue attributes
    make named turns inspectable without imposing a turn count on ordinary
    reading blocks.
    """
    translation = normalize_text(el.attributes.get("translation", "").strip())
    if not translation:
        raise ValueError(f"reading div requires a non-empty translation attribute. Snippet: {_snippet(el)}")

    children = list(el.content)
    if len(children) != 1 or not isinstance(children[0], panflute.Para):
        raise ValueError(f"reading div must contain exactly one paragraph. Snippet: {_snippet(el)}")

    spans = convert_inlines(children[0].content, warnings, default_lang)
    speaker_id = _optional_attribute(el, "speaker_id")
    speaker_name = _optional_attribute(el, "speaker_name")
    dialogue_id = _optional_attribute(el, "dialogue_id")
    character_id = _optional_attribute(el, "character_id")
    voice_profile = _optional_attribute(el, "voice_profile")
    return ReadingBlock(
        kind="reading",
        spans=spans,
        translation=translation,
        speaker_id=speaker_id,
        speaker_name=speaker_name,
        dialogue_id=dialogue_id,
        character_id=character_id,
        voice_profile=voice_profile,
    )


def _optional_attribute(el: panflute.Div, name: str) -> str | None:
    """Return one non-empty Pandoc attribute, or ``None`` when omitted."""
    value = el.attributes.get(name)
    if value is None:
        return None
    normalized = normalize_text(str(value).strip())
    return normalized or None


def _convert_callout(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
) -> CalloutBlock:
    """Convert ``::: callout {variant=...}`` div to ``CalloutBlock`` (recursive)."""
    variant = el.attributes.get("variant", "note")
    level = K_BLOCKS_CALLOUT_LEVELS.get(variant)
    if level is None:
        raise ValueError(f"callout variant must be one of tip/warning/note, got {variant!r}. Snippet: {_snippet(el)}")
    child_blocks = convert_blocks(el.content, warnings, default_lang)
    return CalloutBlock(kind="callout", level=level, blocks=child_blocks)


def _convert_table_div(
    el: panflute.Div,
    warnings: list[str],
    default_lang: str,
) -> TableBlock:
    """Convert a ``Div {col_langs=...}`` wrapping a Table to ``TableBlock``."""
    raw_langs = el.attributes.get("col_langs", "")
    if not raw_langs:
        raise ValueError(f"Table wrapper div must have a col_langs attribute. Snippet: {_snippet(el)}")
    col_langs: list[Literal["en", "no"]] = [
        normalize_lang(lang_code.strip()) for lang_code in raw_langs.split(K_BLOCKS_LANG_LIST_SEP) if lang_code.strip()
    ]

    children = list(el.content)
    table = None
    for child in children:
        if isinstance(child, panflute.Table):
            table = child
            break
    if table is None:
        raise ValueError(f"col_langs div must wrap a pandoc Table. Snippet: {_snippet(el)}")

    headers = _extract_table_headers(table, warnings, default_lang)
    rows = _extract_table_rows(table, warnings, default_lang)

    return TableBlock(kind="table", col_langs=col_langs, headers=headers, rows=rows)


def _extract_table_headers(
    table: panflute.Table,
    warnings: list[str],
    default_lang: str,
) -> list[list[Any]]:
    """Extract header cell spans from a panflute Table."""
    head_rows = list(table.head.content)
    if not head_rows:
        return []
    first_row = head_rows[0]
    headers: list[list[Any]] = []
    for cell in first_row.content:
        headers.append(_convert_cell(cell, warnings, default_lang))
    return headers


def _extract_table_rows(
    table: panflute.Table,
    warnings: list[str],
    default_lang: str,
) -> list[list[list[Any]]]:
    """Extract body row spans from a panflute Table."""
    rows: list[list[list[Any]]] = []
    for body in table.content:
        for row in body.content:
            row_cells: list[list[Any]] = []
            for cell in row.content:
                row_cells.append(_convert_cell(cell, warnings, default_lang))
            rows.append(row_cells)
    return rows


def _convert_cell(
    cell: panflute.TableCell,
    warnings: list[str],
    default_lang: str,
) -> list[Any]:
    """Convert a table cell's content (Plain/Para blocks) into spans."""
    inlines: list[Any] = []
    for block in cell.content:
        if isinstance(block, (panflute.Plain, panflute.Para)):
            inlines.extend(block.content)
        else:
            raise TypeError(
                f"Table cell contains {type(block).__name__}, expected Plain/Para. Snippet: {_snippet(block)}"
            )
    return convert_inlines(inlines, warnings, default_lang)


# ── List ─────────────────────────────────────────────────────────────────


def _convert_list(
    el: panflute.BulletList | panflute.OrderedList,
    warnings: list[str],
    default_lang: str,
) -> ListBlock:
    """Convert a pandoc BulletList or OrderedList to ``ListBlock``."""
    ordered = isinstance(el, panflute.OrderedList)
    items: list[list[Any]] = []
    for item in el.content:
        inline_children = _plain_inlines(item, snippet="list item")
        spans = convert_inlines(inline_children, warnings, default_lang)
        items.append(spans)
    return ListBlock(kind="list", ordered=ordered, items=items)


# ── Shared helpers ───────────────────────────────────────────────────────


def _plain_inlines(item: panflute.ListItem, snippet: str) -> list[Any]:
    """Extract inline elements from a ListItem, rejecting multi-block items."""
    blocks = list(item.content)
    if len(blocks) != 1:
        block_types = [type(b).__name__ for b in blocks]
        raise ValueError(
            f"Multi-block {snippet} is not allowed -- list items must be "
            f"inline-only (single Plain). Found blocks: {block_types}. "
            f"Fix: keep each list item on one line."
        )
    first = blocks[0]
    if not isinstance(first, panflute.Plain):
        raise TypeError(f"{snippet.capitalize()} must contain a Plain block, got {type(first).__name__}.")
    return list(first.content)


def _find_single_bullet_list(
    children: list[Any],
    snippet: str,
) -> panflute.BulletList | None:
    """Find the single BulletList in a list of block children, or None."""
    bullet_lists = [c for c in children if isinstance(c, panflute.BulletList)]
    if len(bullet_lists) != 1:
        if not bullet_lists:
            return None
        raise ValueError(f"{snippet} must contain exactly one bullet list, found {len(bullet_lists)}.")
    return bullet_lists[0]


def _extract_label(inlines: list[Any]) -> tuple[str, list[Any]]:
    """Extract a leading ``label:`` prefix from inline elements.

    Returns ``(label, rest_inlines)`` with the label and the separating space
    removed. If the first element is not a label, returns ``("", inlines)``.
    """
    if not inlines:
        return "", list(inlines)
    first = inlines[0]
    text = getattr(first, "text", None)
    if text is None:
        return "", list(inlines)

    # Pandoc may join "no:" as a single Str, or "no" + ":" as separate tokens.
    for label in ("no", "en"):
        if text == label + ":":
            rest_start = 1
            if rest_start < len(inlines) and isinstance(inlines[rest_start], panflute.Space):
                rest_start += 1
            return label, list(inlines[rest_start:])
        if text == label and len(inlines) > 1:
            second = inlines[1]
            second_text = getattr(second, "text", None)
            if second_text == ":":
                rest_start = 2
                if rest_start < len(inlines) and isinstance(inlines[rest_start], panflute.Space):
                    rest_start += 1
                return label, list(inlines[rest_start:])

    return "", list(inlines)


def _parse_kv_pairs(inlines: list[Any]) -> dict[str, str]:
    """Parse ``key: value`` pairs from inline elements (SoftBreak-separated)."""
    segments: list[list[Any]] = [[]]
    for el in inlines:
        if isinstance(el, panflute.SoftBreak):
            segments.append([])
        else:
            segments[-1].append(el)

    result: dict[str, str] = {}
    for seg in segments:
        key, value = _parse_kv_segment(seg)
        if key is not None:
            result[key] = value
    return result


def _parse_kv_segment(segment: list[Any]) -> tuple[str | None, str]:
    """Parse one ``key: value`` segment from inline elements."""
    if not segment:
        return None, ""

    key: str | None = None
    key_end = 0

    for i, el in enumerate(segment):
        text = getattr(el, "text", None)
        if text is not None and text.endswith(":") and key is None:
            key = text[:-1]
            key_end = i + 1
            break

    if key is None:
        return None, ""

    # Remaining elements after the key form the value.
    value_parts: list[str] = []
    for el in segment[key_end:]:
        text = getattr(el, "text", None)
        if text is not None:
            value_parts.append(text)
        elif isinstance(el, (panflute.Space, panflute.SoftBreak)):
            value_parts.append(" ")

    value = "".join(value_parts).strip()
    return key, value


def _snippet(el: object) -> str:
    """Extract a short snippet string from a panflute block for error messages."""
    from lesson_builder.application.operations.convert_lesson_spans import stringify_inlines

    if isinstance(el, (panflute.Para, panflute.Plain, panflute.Header)):
        text = stringify_inlines(el.content)
    elif isinstance(el, panflute.Div):
        texts: list[str] = []
        for child in el.content:
            if isinstance(child, (panflute.Para, panflute.Plain)):
                texts.append(stringify_inlines(child.content))
        text = " ".join(texts)
    elif isinstance(el, (panflute.BulletList, panflute.OrderedList)):
        texts = []
        for item in el.content:
            for b in item.content:
                if isinstance(b, (panflute.Para, panflute.Plain)):
                    texts.append(stringify_inlines(b.content))
        text = " ".join(texts)
    else:
        text = type(el).__name__

    if len(text) > K_BLOCKS_SNIPPET_LIMIT:
        return text[:K_BLOCKS_SNIPPET_LIMIT] + "..."
    return text
