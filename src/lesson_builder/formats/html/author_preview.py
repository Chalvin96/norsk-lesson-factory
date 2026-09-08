"""Entry point: render_author_preview serializes preview data with packaged Jinja templates."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any
from typing import Protocol

from jinja2 import Environment
from jinja2 import PackageLoader
from jinja2 import select_autoescape
from markupsafe import Markup


class PreviewView(Protocol):
    """Application-owned values consumed by the format renderer."""

    @property
    def lesson_id(self) -> str: ...

    @property
    def lesson_source(self) -> str: ...

    @property
    def exercise_source(self) -> str: ...

    @property
    def plan_source(self) -> str: ...

    @property
    def packet(self) -> dict[str, Any] | None: ...

    @property
    def audit(self) -> dict[str, Any]: ...

    @property
    def errors(self) -> tuple[str, ...]: ...


def render_author_preview(preview: PreviewView) -> str:
    """Return a self-contained author-only review page."""
    lesson = preview.packet or {}
    title = str(lesson.get("title", preview.lesson_id))
    sections_by_id = {item.get("id"): item for item in lesson.get("sections", [])}
    exercises_by_id = {item.get("id"): item for item in lesson.get("exercises", [])}
    elements: list[dict[str, Any]] = []
    for content_item in lesson.get("content", []):
        source = sections_by_id if content_item.get("kind") == "section" else exercises_by_id
        element = source.get(content_item.get("id"))
        if isinstance(element, dict):
            prepared = dict(element)
            prepared["anchor_id"] = _make_attribute_token(element.get("id", "section"))
            elements.append(prepared)

    sections = list(sections_by_id.values())
    findings = list(preview.audit.get("findings", []))
    finding_count = len(findings) + len(preview.errors)
    finding_count_class = "finding-count clean-count" if finding_count == 0 else "finding-count"
    kind_label = str(lesson.get("kind", "source")).replace("_", " ").title()
    template = _build_template_environment().get_template("author_preview.html")
    return template.render(
        lesson=lesson,
        title=title,
        elements=elements,
        sections=sections,
        findings=findings,
        errors=preview.errors,
        finding_count=finding_count,
        finding_count_class=finding_count_class,
        kind_label=kind_label,
        lesson_source=preview.lesson_source,
        exercise_source=preview.exercise_source,
        plan_source=preview.plan_source,
        packet_json=json.dumps(preview.packet, ensure_ascii=False, indent=2),
        export_available=preview.packet is not None,
    )


def _build_template_environment() -> Environment:
    """Create the autoescaping environment from packaged template resources."""
    static_root = files("lesson_builder.formats.html").joinpath("static")
    environment = Environment(
        loader=PackageLoader("lesson_builder.formats.html", "templates"),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.globals["static_asset"] = lambda name: _render_static_asset(static_root, name)
    environment.globals["humanize_identifier"] = _humanize_identifier
    return environment


def _humanize_identifier(value: object) -> str:
    """Turn internal ids into readable labels without changing their anchors."""
    words = str(value or "").replace("_", " ").replace("-", " ").split()
    label = " ".join(words)
    return label[:1].upper() + label[1:] if label else "Untitled"


def _render_static_asset(static_root: Traversable, name: str) -> Markup:
    """Read a fixed, packaged asset after its trusted boundary is checked."""
    if name not in {"author_preview.css", "author_preview.js"}:
        raise ValueError(f"Unknown preview asset: {name}")
    return Markup(static_root.joinpath(name).read_text(encoding="utf-8"))


def _make_attribute_token(value: object) -> str:
    """Normalize an authored identifier before the template escapes it."""
    return str(value).replace(" ", "-")


__all__ = ["render_author_preview"]
