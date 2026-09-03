"""Generate a baseline snapshot of Quotex's public API surface.

Run once before the refactor and commit tests/fixtures/api_surface.json.
The regression test in tests/test_api_surface.py compares the live class
against this snapshot.
"""

import json
from pathlib import Path

from pyquotex.stable_api import Quotex
from scripts.surface_utils import extract_surface

OUTPUT = Path(__file__).parent.parent / "tests" / "fixtures" / "api_surface.json"


def main() -> None:
    surface = extract_surface(Quotex, full_signatures=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(surface)} public symbols to {OUTPUT}")


if __name__ == "__main__":
    main()
