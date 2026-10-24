"""Entry point: ``pandoc.parse_markdown`` reads lesson Markdown.

``markdown_rules``, ``markdown_attributes``, and ``markdown_ast`` parse syntax
and emit Pandoc-shaped JSON. ``pandoc_blocks`` and ``pandoc_inlines`` map it
into lesson values. ``frontmatter`` decodes front matter and YAML;
``attributes`` normalizes parsed attributes.
"""

import warnings

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"invalid escape sequence '\\\*'",
        category=SyntaxWarning,
        module=r"(?:panflute\.io|.*[/\\]panflute[/\\]io(?:\.py)?)$",
    )
    import panflute

__all__ = ["panflute"]
