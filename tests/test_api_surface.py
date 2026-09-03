"""Regression test: Quotex's public surface must not shrink during refactors."""

import json
from pathlib import Path

from pyquotex.stable_api import Quotex
from scripts.surface_utils import extract_surface

FIXTURE = Path(__file__).parent / "fixtures" / "api_surface.json"


def _current_surface() -> dict[str, dict]:
    return extract_surface(Quotex, full_signatures=False)


def test_public_methods_present():
    """Every public method in the snapshot must still exist on Quotex."""
    baseline = json.loads(FIXTURE.read_text())
    current = _current_surface()
    missing = sorted(set(baseline) - set(current))
    assert not missing, f"Public symbols removed: {missing}"


def test_public_method_params_unchanged():
    """Parameter names of public methods must not change (order matters)."""
    baseline = json.loads(FIXTURE.read_text())
    current = _current_surface()
    diffs: list[str] = []
    for name, baseline_entry in baseline.items():
        if baseline_entry.get("kind") != "method":
            continue
        if name not in current:
            continue  # caught by previous test
        baseline_params = [p["name"] for p in baseline_entry["signature"]["parameters"]]
        current_params = current[name].get("params", [])
        if baseline_params != current_params:
            diffs.append(f"{name}: baseline={baseline_params} current={current_params}")
    assert not diffs, "Parameter signatures changed:\n" + "\n".join(diffs)


def test_public_method_kinds_unchanged():
    """A symbol's kind (method/attribute) must not change between baseline and current."""
    baseline = json.loads(FIXTURE.read_text())
    current = _current_surface()
    changed = []
    for name in baseline:
        if name not in current:
            continue  # caught by test_public_methods_present
        baseline_kind = baseline[name].get("kind")
        current_kind = current[name].get("kind")
        if baseline_kind != current_kind:
            changed.append(f"{name}: baseline={baseline_kind} current={current_kind}")
    assert not changed, "Symbol kinds changed:\n" + "\n".join(changed)
