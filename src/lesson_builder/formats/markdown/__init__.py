"""Not a check itself — the external Markdown document adapter.

``pandoc`` wraps the pinned pypandoc reader used by lesson-source parsing.
Front-matter splitting and YAML decoding live in ``frontmatter``; Pandoc
attribute normalization lives in ``attributes``. Application operations convert
the parsed structure into lesson values and load the final lesson.
"""
