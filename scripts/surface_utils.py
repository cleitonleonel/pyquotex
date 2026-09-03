"""Shared surface-introspection helpers used by the snapshot script and the regression tests.

Public-method discovery is intentionally underscore-prefixed: we treat any name
starting with `_` as private and exclude it from the public surface.
"""

import inspect
from typing import Any


def serialize_signature(sig: inspect.Signature) -> dict:
    """Serialize an inspect.Signature into a JSON-friendly dict."""
    params = []
    for name, param in sig.parameters.items():
        params.append(
            {
                "name": name,
                "kind": str(param.kind),
                "default": (
                    "<no-default>"
                    if param.default is inspect.Parameter.empty
                    else repr(param.default)
                ),
                "annotation": (
                    "<no-annotation>"
                    if param.annotation is inspect.Parameter.empty
                    else str(param.annotation)
                ),
            }
        )
    return {
        "parameters": params,
        "return_annotation": (
            "<no-annotation>"
            if sig.return_annotation is inspect.Signature.empty
            else str(sig.return_annotation)
        ),
    }


def extract_surface(cls: type, *, full_signatures: bool) -> dict[str, dict[str, Any]]:
    """Walk dir(cls) and return a public-surface dict.

    full_signatures=True (used by the snapshot script) records each method's full
    signature payload. full_signatures=False (used by the regression test) records
    only the ordered parameter names — enough to detect signature drift without the
    test having to depend on Python-version-specific annotation rendering.
    """
    surface: dict[str, dict[str, Any]] = {}
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        attr = getattr(cls, name)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
            except (TypeError, ValueError):
                entry: dict[str, Any] = {"kind": "callable"}
                if full_signatures:
                    entry["signature"] = None
                surface[name] = entry
                continue
            entry = {"kind": "method"}
            if full_signatures:
                entry["signature"] = serialize_signature(sig)
            else:
                entry["params"] = [p.name for p in sig.parameters.values()]
            surface[name] = entry
        else:
            entry = {"kind": "attribute"}
            if full_signatures:
                entry["type"] = type(attr).__name__
            surface[name] = entry
    return surface
