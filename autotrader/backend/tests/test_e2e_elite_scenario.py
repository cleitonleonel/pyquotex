"""End-to-end "Elite-channel" scenario.

Mirrors the user's real-world bug report: a real Telegram channel
(``-1002475564994`` in the bug ticket) was watched and parsed by an
"Elite"-style prep+trigger config, but trades from it never fired.
This test exercises the entire pipeline — login → connect broker →
watch chat → add two parsers (one template, one prep+trigger) →
dispatch synthetic messages exactly like Elite posts them →
settle watchers → tick the martingale ladder → fire auto-recovery →
flip the parser disabled mid-streak — without touching real Telegram
or a real broker.

Each ``async`` test runs the full FastAPI lifespan in-process via
httpx.ASGITransport (the same harness the existing
``tests/test_risk.py`` uses), so the codepaths are identical to what
the docker container runs in production. Every assertion checks
operator-visible state (trade attempt rows, broker buy_calls,
martingale streak counters, decision-feed entries).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from tests.test_broker import FakeQuotex
from tests.test_risk import (
    WatcherFakeQuotex,
    _activate,
    _add_watch,
    _connect_broker,
    _create_parser,
    _dispatch,
    _login,
    _settle_watchers,
    _wipe_db,
)

# The real channel id from the user's bug report. Using it verbatim
# stresses the 64-bit-channel-id Pyrogram patch in
# ``services/telegram_manager.py:50``.
ELITE_CHAT_ID = -1002475564994


@pytest.fixture(autouse=True)
def _reset_broker_state() -> None:
    """``WatcherFakeQuotex.buy_calls`` is a class-level list shared
    across the FakeQuotex hierarchy. Without resetting between tests
    in this module, an earlier test's buys leak into a later one's
    assertions. The same reset already runs autouse in test_risk.py;
    we mirror it here so this module is hermetic.
    """
    FakeQuotex.behavior = "ok"
    FakeQuotex.valid_otp = "654321"
    FakeQuotex.last_instance = None
    FakeQuotex.buy_calls = []
    FakeQuotex.pending_calls = []
    WatcherFakeQuotex.next_outcomes = []


@pytest.fixture
async def async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Lifespan + HTTP on one event loop with a fake Quotex broker."""
    monkeypatch.setattr(
        "autotrader.services.quotex_manager.Quotex",
        WatcherFakeQuotex,
    )

    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        try:
            yield client
        finally:
            await _wipe_db(AsyncSessionLocal, app)
            app.state.pipeline.invalidate_all()
            await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers specific to this scenario
# ---------------------------------------------------------------------------


async def _create_prep_trigger_parser(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    chat_id: int,
    name: str = "Elite prep+trigger",
    priority: int = 50,
    martingale: dict[str, Any] | None = None,
    default_stake: float = 5.0,
) -> int:
    """Create a parser shaped like Elite: PAIR/TIME prep, sticker trigger.

    The prep is **regex** mode (not template) because real Elite
    messages put emoji decorators (``🌐``, ``⏱``, ``⏱️``) between the
    asset and time fields, and the template's ``\\s+`` separator
    can't bridge a non-whitespace emoji. The regex below captures
    asset on the ``PAIR:`` line and lets ``find_duration_in_text``
    pick up ``01 Minute`` from the body via the parser's documented
    fallback path.
    """
    body = {
        "chat_id": chat_id,
        "name": name,
        "priority": priority,
        "parser_type": "prep_trigger",
        "parser_config": {
            "prep_kind": "regex",
            "prep": r"PAIR\s*:\s*(?P<asset>[A-Z][A-Z\s/_-]*[A-Z])",
            "trigger_kind": "template",
            "trigger": "{DIRECTION}",
        },
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {},
        "aggregate_window_seconds": 120,
        "default_stake": default_stake,
        "default_duration_seconds": 60,
        "trade_mode": "live",
        "martingale": martingale
        or {
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
        },
        "enabled": True,
    }
    r = await client.post("/parsers/configs", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _list_trades(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    r = await client.get("/pipeline/trades?limit=200", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _list_decisions(
    client: httpx.AsyncClient,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    r = await client.get("/pipeline/decisions?limit=200", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _read_streak(parser_config_id: int) -> int:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.martingale_state import MartingaleState  # noqa: PLC0415

    async with AsyncSessionLocal() as s:
        row = await s.get(MartingaleState, parser_config_id)
        if row is None:
            return 0
        return row.current_streak


async def _send_prep_then_trigger(
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    pair: str,
    duration_min: int,
    direction_emoji: str,
) -> None:
    """Mimic two real Elite-channel messages: a PAIR/TIME prep then a sticker."""
    await _dispatch(
        client,
        chat_id=chat_id,
        text=f"🌐 PAIR: {pair}\n⏱ TIME: 0{duration_min} Minute",
    )
    await _dispatch(
        client,
        chat_id=chat_id,
        text=direction_emoji,
    )


# ===========================================================================
# Tests
# ===========================================================================


async def test_elite_scenario_prep_trigger_fires_first_signal(
    async_client: httpx.AsyncClient,
) -> None:
    """The original Elite regression repro — without our fixes the second
    message wouldn't even reach dispatch (peer-cache miss) and the
    parser would never fire. Here we drive ``Pipeline.dispatch``
    directly so we test the parsing+executor path; Tasks 1+2 cover
    the Pyrogram peer-cache primitive separately.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    await _create_prep_trigger_parser(
        async_client, headers, chat_id=ELITE_CHAT_ID, default_stake=5.0,
    )
    await _activate()

    # Real Elite cadence: prep message, then a 👍 sticker (resolved
    # to text="👍" by ``_extract_message_text``).
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _send_prep_then_trigger(
        async_client,
        chat_id=ELITE_CHAT_ID,
        pair="USD NGN OTC",
        duration_min=1,
        direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    # Exactly one buy was placed; OTC asset was resolved correctly.
    assert len(WatcherFakeQuotex.buy_calls) == 1
    call = WatcherFakeQuotex.buy_calls[0]
    assert call["amount"] == 5.0
    assert call["direction"] == "call", "👍 must normalise to call"
    assert call["asset"] == "USDNGN_otc", (
        f"trailing OTC should fold into asset code; got {call['asset']!r}"
    )
    assert call["duration"] == 60, "01 Minute → 60 seconds"

    # Trade row is in the audit log with the right status.
    trades = await _list_trades(async_client, headers)
    assert len(trades) == 1
    assert trades[0]["asset"] == "USDNGN_otc"
    assert trades[0]["status"] == "won"
    assert trades[0]["chat_id"] == ELITE_CHAT_ID

    # Decision feed records the matched dispatch (the prep stored,
    # then the trigger fired).
    decisions = await _list_decisions(async_client, headers)
    matched = [d for d in decisions if d["outcome"] == "matched"]
    assert len(matched) == 1, f"expected one matched decision; got {decisions}"
    assert matched[0]["chat_id"] == ELITE_CHAT_ID
    assert matched[0]["parser_type"] == "prep_trigger"


async def test_elite_scenario_two_parsers_both_fire_independently(
    async_client: httpx.AsyncClient,
) -> None:
    """Two parsers on the same chat = two independent subscribers, not
    alternatives. Both fire when their respective patterns match; the
    template parser ignores the prep+trigger pair and vice versa.
    Mirrors the real-world setup where an operator runs a primary
    Elite-style prep+trigger plus a backup template for clean text
    signals.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    prep_id = await _create_prep_trigger_parser(
        async_client, headers, chat_id=ELITE_CHAT_ID, default_stake=5.0,
    )
    template_id = await _create_parser(
        async_client,
        headers,
        chat_id=ELITE_CHAT_ID,
        default_stake=3.0,
        trade_mode="live",
        template="{DIRECTION} {ASSET} {DURATION}",
    )
    await _activate()

    # First: a clean template-style message. Only the template parser
    # should fire; the prep_trigger parser sees no prep + no trigger.
    WatcherFakeQuotex.next_outcomes = [("win", 2.55)]
    await _dispatch(async_client, chat_id=ELITE_CHAT_ID, text="BUY EURUSD 1m")
    await _settle_watchers(async_client)

    assert len(WatcherFakeQuotex.buy_calls) == 1
    assert WatcherFakeQuotex.buy_calls[0]["amount"] == 3.0
    assert WatcherFakeQuotex.buy_calls[0]["asset"] == "EURUSD"

    # Then: a prep+trigger pair. The prep_trigger parser fires; the
    # template parser sees neither a {DIRECTION} {ASSET} {DURATION}
    # match in the prep nor the bare emoji.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _send_prep_then_trigger(
        async_client,
        chat_id=ELITE_CHAT_ID,
        pair="EUR USD OTC",
        duration_min=2,
        direction_emoji="👎",
    )
    await _settle_watchers(async_client)

    assert len(WatcherFakeQuotex.buy_calls) == 2
    assert WatcherFakeQuotex.buy_calls[1]["amount"] == 5.0
    assert WatcherFakeQuotex.buy_calls[1]["direction"] == "put"
    assert WatcherFakeQuotex.buy_calls[1]["asset"] == "EURUSD_otc"
    assert WatcherFakeQuotex.buy_calls[1]["duration"] == 120

    # Both parsers are recorded in the trade audit, with their config
    # ids preserved.
    trades = await _list_trades(async_client, headers)
    by_parser = {t["parser_config_id"] for t in trades}
    assert by_parser == {prep_id, template_id}, (
        f"both parsers must own a trade row; got {by_parser}"
    )


async def test_elite_scenario_martingale_ladder_recovers_then_resets(
    async_client: httpx.AsyncClient,
) -> None:
    """A losing streak should walk up the ladder; a win should reset.
    With ``auto_recovery=False`` the multiplier applies to the next
    *channel* signal — the operator still has to send another message
    for each step. This is the legacy semantic that channel guidance
    like "IF LOSS WAIT FOR NEXT SIGNAL DOUBLE STAKE" expects.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    parser_id = await _create_prep_trigger_parser(
        async_client,
        headers,
        chat_id=ELITE_CHAT_ID,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
            "auto_recovery": False,
        },
        default_stake=5.0,
    )
    await _activate()

    # Step 0: base stake.
    WatcherFakeQuotex.next_outcomes = [("loss", -5.0)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    # Step 1: 5 × 2 = 10.
    WatcherFakeQuotex.next_outcomes = [("loss", -10.0)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    # Step 2: 5 × 4 = 20.
    WatcherFakeQuotex.next_outcomes = [("loss", -20.0)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    # Step 3: 5 × 8 = 40, but this one wins — ladder resets.
    WatcherFakeQuotex.next_outcomes = [("win", 34.0)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    # Step 4: back to base.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [5.0, 10.0, 20.0, 40.0, 5.0], (
        f"martingale ladder: base, ×2, ×4, ×8, reset; got {amounts}"
    )

    # And the persisted streak is 0 after the final win.
    streak = await _read_streak(parser_id)
    assert streak == 0


async def test_elite_scenario_auto_recovery_fires_without_channel_signal(
    async_client: httpx.AsyncClient,
) -> None:
    """``auto_recovery=True`` makes the bot fire the next ladder step
    itself, without waiting for the channel to send another prep+trigger.
    Mirrors how Elite-style channels phrase their guidance ("IF LOSS
    TAKE 1 STEP MTG, SAME DIRECTION DOUBLE AMOUNT").
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    await _create_prep_trigger_parser(
        async_client,
        headers,
        chat_id=ELITE_CHAT_ID,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 2,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        default_stake=5.0,
    )
    await _activate()

    # One channel signal that loses; recovery fires automatically and
    # also loses; second recovery hits the cap and wins. We only send
    # one prep+trigger pair — the rest is the bot recovering itself.
    WatcherFakeQuotex.next_outcomes = [
        ("loss", -5.0),    # channel signal
        ("loss", -10.0),   # auto-recovery step 1
        ("win", 17.0),     # auto-recovery step 2
    ]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    # The recovery's own watcher is registered DURING the original's
    # settle, so we drain three times.
    await _settle_watchers(async_client)
    await _settle_watchers(async_client)
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [5.0, 10.0, 20.0], (
        f"channel signal + 2 auto-recoveries; got {amounts}"
    )
    # Same direction + asset on every recovery (call from 👍).
    assets = {c["asset"] for c in WatcherFakeQuotex.buy_calls}
    directions = {c["direction"] for c in WatcherFakeQuotex.buy_calls}
    assert assets == {"USDNGN_otc"}, "recovery must reuse the original asset"
    assert directions == {"call"}, "recovery must reuse the original direction"


async def test_elite_scenario_disabling_parser_mid_streak_stops_recovery(
    async_client: httpx.AsyncClient,
) -> None:
    """Task 6 fix: an operator who disables the parser between the
    losing settle and the auto-recovery dispatch must NOT see a phantom
    rejected-trade row land — the new ``_fire_auto_recovery`` refetches
    the config and bails before ``submit()``.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    parser_id = await _create_prep_trigger_parser(
        async_client,
        headers,
        chat_id=ELITE_CHAT_ID,
        martingale={
            "enabled": True,
            "multiplier": 2.0,
            "max_streak": 3,
            "reset_on_win": True,
            "auto_recovery": True,
        },
        default_stake=5.0,
    )
    await _activate()

    # The original signal will lose, the recovery would fire, BUT
    # we disable the parser before the settle drain runs.
    WatcherFakeQuotex.next_outcomes = [("loss", -5.0)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )

    # Disable the parser via the API — the same path an operator uses
    # in the dashboard.
    r = await async_client.put(
        f"/parsers/configs/{parser_id}",
        headers=headers,
        json={
            "name": "Elite prep+trigger",
            "priority": 50,
            "parser_type": "prep_trigger",
            "parser_config": {
                "prep_kind": "regex",
                "prep": r"PAIR\s*:\s*(?P<asset>[A-Z][A-Z\s/_-]*[A-Z])",
                "trigger_kind": "template",
                "trigger": "{DIRECTION}",
            },
            "timezone": "UTC",
            "timezone_offset_minutes": 0,
            "asset_aliases": {},
            "aggregate_window_seconds": 120,
            "default_stake": 5.0,
            "default_duration_seconds": 60,
            "trade_mode": "live",
            "martingale": {
                "enabled": True,
                "multiplier": 2.0,
                "max_streak": 3,
                "reset_on_win": True,
                "auto_recovery": True,
            },
            "enabled": False,
        },
    )
    assert r.status_code == 200, r.text

    # Drain twice in case a recovery would have been queued.
    await _settle_watchers(async_client)
    await _settle_watchers(async_client)

    amounts = [c["amount"] for c in WatcherFakeQuotex.buy_calls]
    assert amounts == [5.0], (
        f"disabled parser must NOT fire a recovery; got buy_calls={amounts}"
    )

    # Critically: no phantom rejected row was inserted (Task 6's
    # contribution beyond what risk_gate already caught).
    trades = await _list_trades(async_client, headers)
    rejected = [t for t in trades if t["status"] == "rejected"]
    assert rejected == [], (
        "_fire_auto_recovery must short-circuit BEFORE submit() so no "
        f"rejected row exists; got {rejected}"
    )


async def test_elite_scenario_unwatch_drops_parser_cache(
    async_client: httpx.AsyncClient,
) -> None:
    """Task 3 fix: DELETE /telegram/watch must drop cached parsers for
    that chat. Verified by inspecting the pipeline's _parsers dict
    before and after.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)
    await _create_prep_trigger_parser(
        async_client, headers, chat_id=ELITE_CHAT_ID,
    )
    await _activate()

    # Dispatch one message to force the parser into the cache.
    WatcherFakeQuotex.next_outcomes = [("win", 4.25)]
    await _send_prep_then_trigger(
        async_client, chat_id=ELITE_CHAT_ID, pair="USD NGN OTC",
        duration_min=1, direction_emoji="👍",
    )
    await _settle_watchers(async_client)

    from autotrader.main import app  # noqa: PLC0415
    pipeline = app.state.pipeline
    assert any(
        cached.config_row.chat_id == ELITE_CHAT_ID
        for cached in pipeline._parsers.values()
    ), "parser should be cached after first dispatch"

    # Unwatch — cache must be dropped.
    r = await async_client.delete(
        f"/telegram/watch/{ELITE_CHAT_ID}", headers=headers,
    )
    assert r.status_code == 200, r.text

    assert not any(
        cached.config_row.chat_id == ELITE_CHAT_ID
        for cached in pipeline._parsers.values()
    ), "parser cache must be cleared by invalidate_for_chat"


async def test_elite_scenario_64bit_chat_id_round_trips(
    async_client: httpx.AsyncClient,
) -> None:
    """The user's bug-report channel id (-1002475564994) is a modern
    64-bit Telegram channel id — the Pyrogram fork in this repo had to
    patch ``MIN_CHANNEL_ID`` to even accept it. This test pins that the
    full stack (DB + API + pipeline) round-trips it correctly.
    """
    headers = await _login(async_client)
    await _connect_broker(async_client, headers)
    await _add_watch(async_client, headers, ELITE_CHAT_ID)

    r = await async_client.get("/telegram/watched", headers=headers)
    body = r.json()
    assert any(d["chat_id"] == ELITE_CHAT_ID for d in body), (
        f"watched list must round-trip the 64-bit chat id verbatim; got {body}"
    )

    # Pipeline.dispatch (the live message path) must also accept the
    # raw int without the patched MIN_CHANNEL_ID swallowing it.
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.services.parsers import RawMessage  # noqa: PLC0415

    await app.state.pipeline.dispatch(
        RawMessage(
            text="ignored — no parser yet",
            chat_id=ELITE_CHAT_ID,
            sender_id=123,
            received_at=datetime.now(UTC),
        ),
    )
    decisions = await _list_decisions(async_client, headers)
    assert any(d["chat_id"] == ELITE_CHAT_ID for d in decisions), (
        "dispatch with the 64-bit id must surface in the decision feed"
    )
