"""Not a check itself — source-format patterns for workbench editing."""

import re

K_FORMATS_SECTION = re.compile(r"^## (.*?)\s*\{[^}]*#([^\s}]+)[^}]*\}\s*$")
K_FORMATS_ATTRIBUTE = re.compile(r"""([a-z_]+)=(?:"([^"]*)"|'([^']*)'|([^\s"'}]+))""")
K_FORMATS_EXERCISE = re.compile(r"^\{\{exercise:\s*([^}\s]+)\s*\}\}$")
K_FORMATS_EXAMPLE_ROW = re.compile(r"^(- (no|en):\s?)(.*)$")

K_FORMATS_VISIBLE_HEADING_MARKER = re.compile(r"^#{1,6}\s+")
K_FORMATS_VISIBLE_HEADING_ATTRIBUTES = re.compile(r"\s*\{[^{}]*\}\s*$")
K_FORMATS_VISIBLE_LIST_MARKER = re.compile(r"^(?:[-*]|\d+[.)])\s+")
K_FORMATS_VISIBLE_EMPHASIS_MARKER = re.compile(r"[*`]")

K_FORMATS_LEGACY_LABEL = re.compile(
    r"^(incorrect(?:\s+[^:]+)?|wrong|not normally|not standard|not)\s*:$", re.IGNORECASE
)

K_FORMATS_WORKBENCH_SCALAR_WIDTH = 1000000
K_FORMATS_WORKBENCH_DOCUMENT_WIDTH = 1000
