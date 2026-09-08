"""Entry point: `main` checks tracked authoring-prose debt without providers."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from lesson_builder.application.operations.authoring_prose_baseline import require_authoring_prose_baseline


def main() -> None:
    """Fail when canonical lesson source adds or changes known authoring prose."""
    parser = argparse.ArgumentParser(description="Check or report legacy lesson authoring prose")
    parser.add_argument("--report", action="store_true", help="print counts by lesson and finding code")
    args = parser.parse_args()
    inventory = require_authoring_prose_baseline(Path(__file__).resolve().parent.parent)
    print(f"Authoring-prose baseline passed ({len(inventory)} legacy lessons)")
    if args.report:
        for lesson_id, fingerprints in inventory.items():
            counts = Counter(fingerprint.split(":", 1)[0] for fingerprint in fingerprints)
            details = ", ".join(f"{code}={count}" for code, count in sorted(counts.items()))
            print(f"{lesson_id}: {details}")


if __name__ == "__main__":
    main()
