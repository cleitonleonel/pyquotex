"""Resolve channel-side asset names to broker codes.

Many signal channels write asset names in their own dialect — ``EUR/USD``,
``EURUSD``, ``EUR-USD``, sometimes with the ``_otc`` off-hours suffix, sometimes
without. The broker has a fixed catalogue of accepted codes (cached on the
``QuotexManager``).

Resolution strategy, in order:

    1. Manual alias map (case-insensitive) — explicit user override.
    2. Exact match against the broker's known-codes set.
    3. Stripped-and-uppercased candidate against the known set.
    4. ``_otc`` suffix probe — try adding / removing the suffix.
    5. Fall back to the stripped-and-uppercased form (broker may
       still accept it; if not the executor's pre-flight check
       catches that).

Every step is tried; the first non-``None`` candidate that exists in the
known set wins. When the known set is empty (broker not connected yet, or
running in tests) only steps 1 and 5 apply — i.e. behaviour matches the
old simple normaliser.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

_NOISE_RE: Final = re.compile(r"[^A-Za-z0-9_]")
_OTC_SUFFIX: Final = "_otc"


@dataclass(frozen=True, slots=True)
class AssetResolution:
    """Outcome of an asset lookup. Surfaces what kind of match happened
    so the live tester can show ``raw → broker`` to the user.
    """

    raw: str
    code: str
    via: str  # "alias" | "exact" | "otc" | "fallback"


def _strip_upper(s: str) -> str:
    return _NOISE_RE.sub("", s).upper()


def _alias_lookup(raw: str, aliases: dict[str, str]) -> str | None:
    if not aliases:
        return None
    lookup = {k.casefold(): v for k, v in aliases.items()}
    return lookup.get(raw.strip().casefold())


def resolve_asset(
    raw: str,
    *,
    aliases: dict[str, str] | None = None,
    known_assets: Iterable[str] | None = None,
) -> AssetResolution:
    """Resolve ``raw`` to a broker asset code, returning *what we picked*."""
    aliases = aliases or {}
    known: set[str] = {a for a in (known_assets or ()) if a}

    # 1. Manual alias
    if (mapped := _alias_lookup(raw, aliases)) is not None:
        return AssetResolution(raw=raw, code=mapped, via="alias")

    stripped = _strip_upper(raw)

    # 2 + 3. Exact / stripped match against known codes
    if known:
        for candidate in (raw.strip(), stripped):
            if candidate and candidate in known:
                return AssetResolution(raw=raw, code=candidate, via="exact")

        # 4. _otc probe
        if stripped:
            otc_variant = (
                stripped.removesuffix(_OTC_SUFFIX.upper())
                if stripped.upper().endswith(_OTC_SUFFIX.upper())
                else stripped + _OTC_SUFFIX
            )
            for candidate in (
                stripped + _OTC_SUFFIX,
                otc_variant,
                stripped.lower() + _OTC_SUFFIX,
            ):
                if candidate in known:
                    return AssetResolution(raw=raw, code=candidate, via="otc")

    # 5. Fallback — return the stripped form (broker either accepts
    #    it or rejects later).
    return AssetResolution(raw=raw, code=stripped, via="fallback")
