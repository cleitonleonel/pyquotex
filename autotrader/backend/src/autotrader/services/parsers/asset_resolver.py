"""Resolve channel-side asset names to broker codes.

Many signal channels write asset names in their own dialect — ``EUR/USD``,
``EURUSD``, ``EUR-USD``, sometimes with the ``_otc`` off-hours suffix, sometimes
without, and very often with ``OTC`` written as a *separate word* on the
end (``USD NGN OTC``, ``GOLD OTC``).  The broker has a fixed catalogue of
accepted codes (cached on the ``QuotexManager``).

Resolution strategy, in order:

    1. Manual alias map (case-insensitive) — explicit user override.
    2. Trailing ``OTC`` detection — if the raw asset has ``OTC`` as
       its last word/token, the rest is the *base* and the canonical
       form is ``<base>_otc``.
    3. Exact match against the broker's known-codes set.
    4. ``_otc`` suffix probe — try the OTC and non-OTC variants of
       the base.
    5. Fall back to the canonical form derived in step 2 (or the
       stripped-and-uppercased base when there's no OTC marker).

When the known set is empty (broker not connected yet, or running in
tests) only steps 1, 2, and 5 fire — the resolver still does the
right thing for OTC-marked names because step 2 doesn't need the
catalogue.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

# A "token" is a run of letters / digits — i.e. one word in the asset
# name. Slashes, hyphens, underscores, and spaces all separate tokens.
_TOKEN_RE: Final = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_OTC_SUFFIX: Final = "_otc"


@dataclass(frozen=True, slots=True)
class AssetResolution:
    """Outcome of an asset lookup. Surfaces what kind of match happened
    so the live tester can show ``raw → broker`` to the user.
    """

    raw: str
    code: str
    via: str  # "alias" | "exact" | "otc" | "fallback"


def _alias_lookup(raw: str, aliases: dict[str, str]) -> str | None:
    if not aliases:
        return None
    lookup = {k.casefold(): v for k, v in aliases.items()}
    return lookup.get(raw.strip().casefold())


def _split_otc(raw: str) -> tuple[str, bool]:
    """Tokenise ``raw`` and return ``(base_without_otc, is_otc)``.

    ``is_otc`` is True when the trailing token is ``"OTC"``
    (case-insensitive). The base is the remaining tokens joined into
    one uppercased string — e.g. ``"USD NGN OTC"`` → ``("USDNGN", True)``,
    ``"GOLD OTC"`` → ``("GOLD", True)``, ``"USD EUR"`` →
    ``("USDEUR", False)``.
    """
    tokens = [t.upper() for t in _TOKEN_RE.findall(raw)]
    if not tokens:
        return "", False
    if tokens[-1] == "OTC" and len(tokens) > 1:
        return "".join(tokens[:-1]), True
    return "".join(tokens), False


def resolve_asset(  # noqa: PLR0911  (one return per resolution-strategy step)
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

    base, is_otc = _split_otc(raw)
    if not base:
        return AssetResolution(raw=raw, code="", via="fallback")

    otc_form = base + _OTC_SUFFIX        # e.g. "USDNGN_otc"
    plain_form = base                    # e.g. "USDNGN"

    if known:
        # 2. Exact match against the unaltered raw text — covers users
        #    who write the broker code verbatim (rare but cheap).
        if raw.strip() in known:
            return AssetResolution(raw=raw, code=raw.strip(), via="exact")

        # 3. Prefer the form the channel announced.
        if is_otc and otc_form in known:
            return AssetResolution(raw=raw, code=otc_form, via="otc")
        if plain_form in known:
            return AssetResolution(raw=raw, code=plain_form, via="exact")

        # 4. Cross-probe: channel didn't say OTC but broker only has
        #    the OTC variant (or vice-versa).
        if otc_form in known:
            return AssetResolution(raw=raw, code=otc_form, via="otc")

    # 5. Fallback — preserve the channel's intent. OTC-marked names
    #    become ``<base>_otc``; bare names become ``<base>``.
    code = otc_form if is_otc else plain_form
    return AssetResolution(raw=raw, code=code, via="fallback")
