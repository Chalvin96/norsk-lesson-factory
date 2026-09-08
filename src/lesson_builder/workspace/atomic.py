"""Entry point: `write_text_atomically` replaces UTF-8 text through a fsynced temporary file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomically(
    path: Path,
    content: str,
    *,
    create_parent: bool = False,
    suffix: str = "",
) -> None:
    """Replace one text file after flushing its temporary contents to disk."""
    if create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=suffix,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


__all__ = ["write_text_atomically"]
