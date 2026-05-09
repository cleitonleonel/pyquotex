"""Tests for /stats/v2/* endpoints (Phase 2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.test_broker import FakeQuotex

pytestmark = pytest.mark.asyncio


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
                    received_at=r.get("received_at", datetime.now(UTC)),
                    placed_at=r.get("placed_at"),
                    settled_at=r.get("settled_at"),
                ),
            )
        await s.commit()


# ---------------------------------------------------------------------------
# /stats/v2/timeseries — equity metric
# ---------------------------------------------------------------------------


async def test_timeseries_equity_empty(async_client: httpx.AsyncClient) -> None:
    """No trades -> empty points list, valid metric echo."""
    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "equity", "range": "24h"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "equity"
    assert body["points"] == []


async def test_timeseries_equity_accumulates(
    async_client: httpx.AsyncClient,
) -> None:
    """Equity points report cumulative P&L per bucket."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    await _seed_attempts(
        [
            {"status": "won", "profit": 1.0, "received_at": now - timedelta(hours=2)},
            {"status": "won", "profit": 0.5, "received_at": now - timedelta(hours=1)},
            {"status": "lost", "profit": -1.0, "received_at": now - timedelta(minutes=15)},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "equity", "range": "24h"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "equity"
    points = body["points"]
    assert len(points) >= 1
    # Last bucket's cumulative value: 1.0 + 0.5 - 1.0 = 0.5
    assert points[-1]["value"] == pytest.approx(0.5, abs=0.001)


# ---------------------------------------------------------------------------
# /stats/v2/timeseries — win_rate metric
# ---------------------------------------------------------------------------


async def test_timeseries_win_rate_empty(async_client: httpx.AsyncClient) -> None:
    """No trades -> empty points."""
    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "win_rate", "range": "24h"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["points"] == []


async def test_timeseries_win_rate_ratio(async_client: httpx.AsyncClient) -> None:
    """Win rate per bucket = won / (won + lost). Pending/rejected ignored."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    await _seed_attempts(
        [
            {"status": "won", "received_at": now - timedelta(hours=1)},
            {"status": "won", "received_at": now - timedelta(hours=1)},
            {"status": "lost", "received_at": now - timedelta(hours=1)},
            {"status": "rejected", "received_at": now - timedelta(hours=1)},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "win_rate", "range": "24h"},
        headers=headers,
    )
    body = r.json()
    points = body["points"]
    assert len(points) == 1
    assert points[0]["value"] == pytest.approx(2 / 3, abs=0.001)
    assert points[0]["n"] == 3  # settled count, not total count


async def test_timeseries_win_rate_skips_unsettled_buckets(
    async_client: httpx.AsyncClient,
) -> None:
    """A bucket with only pending/rejected/expired trades emits no point."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    await _seed_attempts(
        [
            {"status": "pending", "received_at": now - timedelta(hours=1)},
            {"status": "rejected", "received_at": now - timedelta(hours=1)},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "win_rate", "range": "24h"},
        headers=headers,
    )
    assert r.json()["points"] == []


# ---------------------------------------------------------------------------
# /stats/v2/timeseries — latency_p50 / latency_p99 metrics
# ---------------------------------------------------------------------------


async def test_timeseries_latency_p50_empty(async_client: httpx.AsyncClient) -> None:
    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "latency_p50", "range": "24h"},
        headers=headers,
    )
    assert r.json()["points"] == []


async def test_timeseries_latency_p50_known_samples(
    async_client: httpx.AsyncClient,
) -> None:
    """p50 of [100, 200, 300] ms = 200 ms."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    base = now - timedelta(hours=1)
    await _seed_attempts(
        [
            {"status": "won", "received_at": base,
             "placed_at": base + timedelta(milliseconds=100)},
            {"status": "won", "received_at": base,
             "placed_at": base + timedelta(milliseconds=200)},
            {"status": "lost", "received_at": base,
             "placed_at": base + timedelta(milliseconds=300)},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "latency_p50", "range": "24h"},
        headers=headers,
    )
    points = r.json()["points"]
    assert len(points) == 1
    assert points[0]["value"] == pytest.approx(200.0, abs=0.5)
    assert points[0]["n"] == 3


async def test_timeseries_latency_p99_known_samples(
    async_client: httpx.AsyncClient,
) -> None:
    """p99 of [100, 200, 300, 400, 500] is close to 500."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    base = now - timedelta(hours=1)
    await _seed_attempts(
        [
            {
                "status": "won",
                "received_at": base,
                "placed_at": base + timedelta(milliseconds=ms),
            }
            for ms in (100, 200, 300, 400, 500)
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "latency_p99", "range": "24h"},
        headers=headers,
    )
    points = r.json()["points"]
    assert len(points) == 1
    assert points[0]["value"] == pytest.approx(500.0, abs=10.0)


async def test_timeseries_latency_skips_unplaced(
    async_client: httpx.AsyncClient,
) -> None:
    """Rows without placed_at don't contribute samples."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    await _seed_attempts(
        [
            {"status": "pending", "received_at": now - timedelta(hours=1)},
            {"status": "rejected", "received_at": now - timedelta(hours=1)},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "latency_p50", "range": "24h"},
        headers=headers,
    )
    assert r.json()["points"] == []


# ---------------------------------------------------------------------------
# /stats/v2/timeseries — outcomes metric
# ---------------------------------------------------------------------------


async def test_timeseries_outcomes_empty(async_client: httpx.AsyncClient) -> None:
    headers = await _login(async_client)
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "outcomes", "range": "24h", "bucket": "hour"},
        headers=headers,
    )
    assert r.json()["points"] == []


async def test_timeseries_outcomes_returns_counts_dict(
    async_client: httpx.AsyncClient,
) -> None:
    """Outcomes metric returns counts dict instead of scalar value."""
    headers = await _login(async_client)
    now = datetime.now(UTC)
    base = now - timedelta(hours=1)
    await _seed_attempts(
        [
            {"status": "won", "received_at": base},
            {"status": "won", "received_at": base},
            {"status": "lost", "received_at": base},
            {"status": "rejected", "received_at": base},
            {"status": "expired", "received_at": base},
            {"status": "pending", "received_at": base},
            {"status": "broker_error", "received_at": base},
        ],
    )
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={"metric": "outcomes", "range": "24h", "bucket": "hour"},
        headers=headers,
    )
    points = r.json()["points"]
    assert len(points) == 1
    p = points[0]
    assert p["value"] is None  # outcomes uses counts, not value
    assert p["n"] == 7
    assert p["counts"] == {
        "won": 2,
        "lost": 1,
        "rejected": 1,
        "broker_error": 1,
        "expired": 1,
        "pending": 1,
    }


async def test_timeseries_custom_range_with_naive_datetimes(
    async_client: httpx.AsyncClient,
) -> None:
    """range=custom with naive ISO datetimes should not 500.

    The Phase 2 frontend sends ISO strings without offsets via
    react-day-picker; FastAPI parses them as naive. Without UTC
    normalisation, _floor_to_bucket raises TypeError on the first
    DB row with an aware filter bound. This regression test pins
    the fix at the resolver boundary.
    """
    headers = await _login(async_client)
    now = datetime.now(UTC)
    # Seed a single row inside the custom range.
    await _seed_attempts(
        [{"status": "won", "profit": 1.0, "received_at": now - timedelta(hours=2)}],
    )
    # Use ISO strings without offset (the FastAPI/Pydantic naive case).
    from_iso = (now - timedelta(hours=24)).replace(tzinfo=None).isoformat()
    to_iso = now.replace(tzinfo=None).isoformat()
    r = await async_client.get(
        "/stats/v2/timeseries",
        params={
            "metric": "equity",
            "range": "custom",
            "from": from_iso,
            "to": to_iso,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metric"] == "equity"
    assert len(body["points"]) == 1
    assert body["points"][0]["value"] == pytest.approx(1.0, abs=0.001)
