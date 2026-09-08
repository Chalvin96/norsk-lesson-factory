"""Not a check itself — the parser-registration surface shared by command families."""

from __future__ import annotations

import argparse
from typing import Protocol

__all__ = ["SubparserRegistrar"]


class SubparserRegistrar(Protocol):
    """Minimal parser-registration surface each command family requires."""

    def add_parser(self, name: str, *, help: str | None = ...) -> argparse.ArgumentParser:
        """Register one named subcommand and return its argument parser."""
