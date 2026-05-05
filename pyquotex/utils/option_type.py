"""Quotex option-type resolver shared by `buy` and `open_pending`.

Quotex distinguishes three option flavours on the wire via ``optionType``:

* ``1``  — Binary option (timer ≥ 60s on regular pairs).
* ``3``  — Blitz / fast option (5/10/15/30/60s on regular pairs).
* ``100`` — Digital / OTC option (scheduled expiration on ``*_otc`` pairs).

Both the immediate ``orders/open`` and the scheduled ``pending/create``
payloads need this field. Centralising the rule here keeps the two
codepaths in lock-step and prevents OTC pending orders from being
silently misclassified as regular binaries (the historical bug — the
broker rejects or mis-routes them when ``optionType`` is missing).
"""
from __future__ import annotations

from enum import IntEnum


class OptionType(IntEnum):
    """Quotex ``optionType`` enum on the wire."""

    BINARY = 1
    BLITZ = 3
    DIGITAL_OTC = 100


def is_otc_asset(asset: str) -> bool:
    """Return ``True`` if ``asset`` is an OTC pair (case-insensitive)."""
    return asset.lower().endswith("_otc")


def resolve_option_type(
        asset: str,
        duration: int,
        is_fast_option: bool,
        is_pending: bool = False,
) -> OptionType:
    """Pick the correct ``optionType`` for a given trade payload.

    Args:
        asset: Symbol (``"EURUSD"``, ``"EURUSD_otc"``).
        duration: Option duration in seconds.
        is_fast_option: Caller passed ``time_mode="TIME"`` (blitz mode).
        is_pending: Whether this is a scheduled pending order.

    Returns:
        ``OptionType.DIGITAL_OTC`` for OTC scheduled trades,
        ``OptionType.BLITZ`` for fast options, ``OptionType.BINARY``
        otherwise. Pending orders never use blitz (Quotex doesn't
        schedule sub-minute trades) — they always resolve to BINARY or
        DIGITAL_OTC.
    """
    otc = is_otc_asset(asset)

    if is_pending:
        # Scheduled trades are either OTC digital or regular binary;
        # blitz options can't be pre-scheduled.
        return OptionType.DIGITAL_OTC if otc else OptionType.BINARY

    if is_fast_option:
        return OptionType.BLITZ

    if otc:
        return OptionType.DIGITAL_OTC

    return OptionType.BINARY
