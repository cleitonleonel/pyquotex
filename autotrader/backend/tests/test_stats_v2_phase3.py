"""Phase 3 stats/v2 additions: assets endpoint, parser streaks, funnel ring wiring.

Kept in its own module so the Phase 3 changes are reviewable in isolation
from the Phase 2 baseline in ``test_stats_v2.py``. Test plumbing mirrors
the conventions in that file (``async_client`` lifespan fixture, DB wipe
on teardown, ``_seed_attempts`` helper for fixture-style row inserts).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.test_broker import FakeQuotex


@pytest.fixture(autouse=True)
def _reset_fake_quotex_state() -> None:
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []


@pytest.fixture
async def async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Same shape as the Phase 2 ``async_client`` fixture."""
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        FakeQuotex,
    )
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),  # type: ignore[arg-type]
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        try:
            yield client
        finally:
            await _wipe_db(AsyncSessionLocal)
            await engine.dispose()


async def _wipe_db(session_factory) -> None:
    from sqlmodel import delete  # noqa: PLC0415

    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    async with session_factory() as s:
        for model in (TradeAttempt, ParserConfig, WatchedChannel):
            await s.exec(delete(model))  # type: ignore[call-overload]
        await s.commit()


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post("/auth/login", json={"passcode": "test-passcode"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _seed_attempts(rows: list[dict]) -> None:
    """Insert pre-built TradeAttempt rows. Each dict overrides defaults."""
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.trade_attempt import TradeAttempt  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        for r in rows:
            s.add(
                TradeAttempt(
                    chat_id=r.get("chat_id", 1),
                    parser_config_id=r.get("parser_config_id", 1),
                    asset=r.get("asset", "EURUSD"),
                    asset_raw=r.get("asset_raw", "EUR/USD"),
                    direction=r.get("direction", "call"),
                    duration_seconds=r.get("duration_seconds", 60),
                    stake=r.get("stake", 1.0),
                    trade_mode=r.get("trade_mode", "live"),
                    status=r.get("status", "won"),
                    profit=r.get("profit"),
                    error=r.get("error"),
                    received_at=r.get("received_at", datetime.now(UTC)),
                    placed_at=r.get("placed_at"),
                    settled_at=r.get("settled_at"),
                ),
            )
        await s.commit()


# ---------------------------------------------------------------------------
# /stats/v2/assets — distinct symbols inside the time window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assets_endpoint_returns_sorted_distinct_symbols(
    async_client: httpx.AsyncClient,
) -> None:
    """Distinct asset symbols within the window, alphabetical (case-insensitive)."""
    now = datetime.now(UTC)
    await _seed_attempts(
        [
            {"asset": "EURUSD", "received_at": now - timedelta(hours=1)},
            {"asset": "audcad", "received_at": now - timedelta(hours=2),
             "direction": "put"},
            {"asset": "EURUSD", "received_at": now - timedelta(hours=3)},
        ],
    )

    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/assets", params={"range": "24h"}, headers=headers,
    )

    assert r.status_code == 200
    body = r.json()
    # Case-insensitive sort, deduped. "audcad" < "EURUSD" via str.lower.
    assert body == {"assets": ["audcad", "EURUSD"]}


@pytest.mark.asyncio
async def test_assets_endpoint_empty_window_returns_empty_list(
    async_client: httpx.AsyncClient,
) -> None:
    """No trades in window -> empty assets list, still 200."""
    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/assets", params={"range": "24h"}, headers=headers,
    )

    assert r.status_code == 200
    assert r.json() == {"assets": []}


# ---------------------------------------------------------------------------
# compute_parser_streaks helper — pure function, no DB required
# ---------------------------------------------------------------------------


def test_compute_parser_streaks_basic() -> None:
    """L L L W L L W L (trailing losses excluded — no recovery yet)."""
    from autotrader.services.filters import compute_parser_streaks  # noqa: PLC0415

    outcomes = ["lost", "lost", "lost", "won", "lost", "lost", "won", "lost"]
    result = compute_parser_streaks(outcomes)

    assert result["longest_loss"] == 3
    assert result["histogram"] == {"2": 1, "3": 1}
    assert result["recovered_count"] == 2
    assert result["recovery_rate"] == 1.0


def test_compute_parser_streaks_no_losses() -> None:
    from autotrader.services.filters import compute_parser_streaks  # noqa: PLC0415

    result = compute_parser_streaks(["won", "won", "expired"])
    assert result["longest_loss"] == 0
    assert result["histogram"] == {}
    assert result["recovered_count"] == 0
    assert result["recovery_rate"] == 0.0


def test_compute_parser_streaks_single_loss_recovered_by_non_won() -> None:
    """Recovery only counts if the next outcome is exactly 'won' —
    expired/rejected close the streak but don't count as recovery."""
    from autotrader.services.filters import compute_parser_streaks  # noqa: PLC0415

    result = compute_parser_streaks(["lost", "expired"])
    assert result["longest_loss"] == 1
    assert result["histogram"] == {"1": 1}
    assert result["recovered_count"] == 0
    assert result["recovery_rate"] == 0.0


# ---------------------------------------------------------------------------
# /stats/v2/breakdown?dim=parser — streaks sub-stat plumbed in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breakdown_parser_includes_streaks(
    async_client: httpx.AsyncClient,
) -> None:
    """dim=parser response carries per-parser streak roll-up.

    Three losses then a win = one closed streak of length 3, fully
    recovered (next outcome is 'won').
    """
    headers = await _login(async_client)
    now = datetime.now(UTC)
    rows = []
    for i, status in enumerate(["lost", "lost", "lost", "won"]):
        rows.append(
            {
                "parser_config_id": 1,
                "status": status,
                "received_at": now - timedelta(minutes=10 - i),
                # Realised P&L only matters for committed_stake / pnl
                # rollups elsewhere; not asserted here.
                "profit": 1.0 if status == "won" else -1.0,
            },
        )
    await _seed_attempts(rows)

    r = await async_client.get(
        "/stats/v2/breakdown",
        params={"dim": "parser", "range": "24h"},
        headers=headers,
    )

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    streaks = body["rows"][0]["streaks"]
    assert streaks["longest_loss"] == 3
    assert streaks["histogram"] == {"3": 1}
    assert streaks["recovered_count"] == 1
    assert streaks["recovery_rate"] == 1.0
