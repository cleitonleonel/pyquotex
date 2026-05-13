"""Phase 4 cleanup tests (audit 2026-05-13).

* L2 — dead ``QuotexManager.account_type_int`` property removed.
* L6 — log-mute list configurable via env (``AUTOTRADER_MUTE_LOGGERS``
       to replace, ``AUTOTRADER_MUTE_LOGGERS_EXTRA`` to extend).
* M5 — ``record_outcome`` rounds ``last_payout`` to 2 decimal places.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# L2 — confirm property is gone (regression guard against accidental re-add)
# ---------------------------------------------------------------------------


def test_quotex_manager_no_longer_exposes_account_type_int() -> None:
    """Dead property removed. The codebase grepped clean of call sites
    before removal; this assertion stays so future imports of
    ``AccountType`` don't quietly resurrect it without checking."""
    from autotrader.services.quotex_manager import QuotexManager  # noqa: PLC0415
    assert not hasattr(QuotexManager, "account_type_int"), (
        "audit-2026-05-13 L2: property was confirmed dead; do not "
        "re-introduce without a real call site"
    )


# ---------------------------------------------------------------------------
# L6 — env-driven mute-list overrides
# ---------------------------------------------------------------------------


def test_resolve_muted_loggers_uses_defaults_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With neither env set, the production defaults apply unchanged."""
    monkeypatch.delenv("AUTOTRADER_MUTE_LOGGERS", raising=False)
    monkeypatch.delenv("AUTOTRADER_MUTE_LOGGERS_EXTRA", raising=False)

    from autotrader.logging_setup import (  # noqa: PLC0415
        _DEFAULT_MUTED_LOGGERS,
        _resolve_muted_loggers,
    )

    assert _resolve_muted_loggers() == _DEFAULT_MUTED_LOGGERS


def test_resolve_muted_loggers_extra_extends_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AUTOTRADER_MUTE_LOGGERS_EXTRA`` appends to the defaults."""
    monkeypatch.delenv("AUTOTRADER_MUTE_LOGGERS", raising=False)
    monkeypatch.setenv(
        "AUTOTRADER_MUTE_LOGGERS_EXTRA", "foo, bar.baz ,quux",
    )

    from autotrader.logging_setup import (  # noqa: PLC0415
        _DEFAULT_MUTED_LOGGERS,
        _resolve_muted_loggers,
    )

    result = _resolve_muted_loggers()
    assert result[:len(_DEFAULT_MUTED_LOGGERS)] == _DEFAULT_MUTED_LOGGERS
    assert result[-3:] == ("foo", "bar.baz", "quux")


def test_resolve_muted_loggers_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    """``AUTOTRADER_MUTE_LOGGERS`` (no EXTRA) REPLACES the defaults."""
    monkeypatch.setenv("AUTOTRADER_MUTE_LOGGERS", "only-this-one")
    monkeypatch.delenv("AUTOTRADER_MUTE_LOGGERS_EXTRA", raising=False)

    from autotrader.logging_setup import _resolve_muted_loggers  # noqa: PLC0415

    assert _resolve_muted_loggers() == ("only-this-one",)


# ---------------------------------------------------------------------------
# M5 — last_payout rounding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_outcome_rounds_last_payout_to_two_decimals() -> None:
    """``last_payout = last_stake + last_profit`` used to carry float
    drift forward into the next streak step's ``ceil(payout)``. The
    audit's M5 fix rounds to broker precision at the persistence
    boundary so long winning streaks stay 1¢-honest."""
    from sqlalchemy import text  # noqa: PLC0415

    from autotrader.db import AsyncSessionLocal, engine, init_db  # noqa: PLC0415
    from autotrader.models.martingale_state import (  # noqa: PLC0415
        record_outcome,
    )

    await init_db()
    # Clean slate so the streak counters are zero.
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM martingale_states"))

    async with AsyncSessionLocal() as s:
        # 1 / 3 produces float noise; round(x, 2) collapses it.
        state = await record_outcome(
            s,
            parser_config_id=999,
            won=True,
            last_stake=1.0,
            last_profit=1.0 / 3.0,
            max_streak=5,
            reset_on_win=True,
            winning_streak_enabled=True,
            winning_streak_max_level=10,
        )

    # The unrounded value is 1.3333333333333335; rounded it's 1.33.
    assert state.last_payout == round(1.0 + 1.0 / 3.0, 2) == 1.33
