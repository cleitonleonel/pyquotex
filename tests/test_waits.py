"""Unit tests for WaitableSlot and wait_until."""
import asyncio

import pytest

from pyquotex._api._waits import SlotRegistry, WaitableSlot, wait_until


@pytest.mark.asyncio
async def test_slot_resolves_with_set_value():
    slot: WaitableSlot[int] = WaitableSlot()

    async def setter():
        await asyncio.sleep(0.01)
        slot.set(42)

    asyncio.create_task(setter())
    assert await slot.wait(timeout=1.0) == 42


@pytest.mark.asyncio
async def test_slot_times_out_when_never_set():
    slot: WaitableSlot[int] = WaitableSlot()
    with pytest.raises(asyncio.TimeoutError):
        await slot.wait(timeout=0.05)


@pytest.mark.asyncio
async def test_slot_can_be_cleared_and_reused():
    slot: WaitableSlot[str] = WaitableSlot()
    slot.set("first")
    assert await slot.wait(timeout=0.1) == "first"
    slot.clear()
    with pytest.raises(asyncio.TimeoutError):
        await slot.wait(timeout=0.05)
    slot.set("second")
    assert await slot.wait(timeout=0.1) == "second"


@pytest.mark.asyncio
async def test_slot_set_before_wait_resolves_immediately():
    slot: WaitableSlot[int] = WaitableSlot()
    slot.set(7)
    assert await slot.wait(timeout=0.1) == 7


@pytest.mark.asyncio
async def test_wait_until_resolves_when_predicate_true():
    counter = {"n": 0}

    async def increment():
        await asyncio.sleep(0.01)
        counter["n"] = 5

    asyncio.create_task(increment())
    await wait_until(lambda: counter["n"] >= 5, timeout=1.0)
    assert counter["n"] == 5


@pytest.mark.asyncio
async def test_wait_until_times_out():
    with pytest.raises(asyncio.TimeoutError):
        await wait_until(lambda: False, timeout=0.05)


def test_slot_registry_has_named_slots():
    reg = SlotRegistry()
    assert reg.balance is not None
    assert reg.balance_update is not None
    assert reg.candle_v2_ready is not None
    assert reg.historical_ready is not None
    assert reg.pending_confirm is not None
    assert reg.sold_option_confirm is not None
    assert reg.training_balance_edit is not None
    assert reg.auth_status is not None


def test_slot_registry_has_buy_confirm():
    reg = SlotRegistry()
    assert reg.buy_confirm is not None


def test_slot_registry_keyed_slots_create_on_access():
    reg = SlotRegistry()
    slot_a = reg.order_confirm("req-1")
    slot_b = reg.order_confirm("req-1")
    slot_c = reg.order_confirm("req-2")
    assert slot_a is slot_b  # same key returns same slot
    assert slot_a is not slot_c  # different key returns different slot


def test_slot_registry_keyed_slot_release():
    reg = SlotRegistry()
    slot = reg.order_confirm("req-1")
    slot.set({"id": 1})
    reg.release_order_confirm("req-1")
    new_slot = reg.order_confirm("req-1")
    assert new_slot is not slot


@pytest.mark.asyncio
async def test_slot_rejects_none():
    """set(None) is invalid — use clear() to reset."""
    slot: WaitableSlot[dict] = WaitableSlot()
    with pytest.raises(ValueError):
        slot.set(None)
    assert not slot.is_set()


@pytest.mark.asyncio
async def test_slot_double_set_uses_latest_value():
    """Second set replaces the first; wait returns the latest value."""
    slot: WaitableSlot[int] = WaitableSlot()
    slot.set(1)
    slot.set(2)
    assert await slot.wait(timeout=0.1) == 2


@pytest.mark.asyncio
async def test_slot_two_consumers_both_resolve():
    """Multiple awaiters on the same slot all receive the value."""
    slot: WaitableSlot[str] = WaitableSlot()

    async def consumer() -> str:
        return await slot.wait(timeout=1.0)

    t1 = asyncio.create_task(consumer())
    t2 = asyncio.create_task(consumer())
    await asyncio.sleep(0.01)  # ensure both are blocked on _event
    slot.set("hello")
    assert await t1 == "hello"
    assert await t2 == "hello"


def test_slot_registry_win_result_release():
    """release_win_result must drop the slot so a fresh one is created next."""
    reg = SlotRegistry()
    slot = reg.win_result("op-1")
    slot.set({"result": "win"})
    reg.release_win_result("op-1")
    new_slot = reg.win_result("op-1")
    assert new_slot is not slot


def test_quotex_api_has_slot_registry():
    """QuotexAPI must expose a SlotRegistry as .slots."""
    from pyquotex.api import QuotexAPI

    api = QuotexAPI(
        host="qxbroker.com",
        username="x",
        password="x",
        lang="en",
        proxies=None,
        resource_path=".",
        user_data_dir="browser",
        on_otp_callback=None,
    )
    assert isinstance(api.slots, SlotRegistry)
    assert api.slots.balance is not None


def test_slot_registry_candle_v2_keyed():
    reg = SlotRegistry()
    slot_a = reg.candle_v2("EURUSD")
    slot_b = reg.candle_v2("EURUSD")
    slot_c = reg.candle_v2("GBPUSD")
    assert slot_a is slot_b
    assert slot_a is not slot_c


def test_slot_registry_candle_v2_release():
    reg = SlotRegistry()
    slot = reg.candle_v2("EURUSD")
    slot.set({"foo": "bar"})
    reg.release_candle_v2("EURUSD")
    new_slot = reg.candle_v2("EURUSD")
    assert new_slot is not slot


@pytest.mark.asyncio
async def test_backoff_sleep_respects_base():
    """attempt=0 with base=0.01 should sleep ~0.01s (within jitter)."""
    import time

    from pyquotex._api._waits import backoff_sleep
    start = time.monotonic()
    await backoff_sleep(0, base=0.01, cap=0.1, jitter=0)
    elapsed = time.monotonic() - start
    assert 0.005 <= elapsed <= 0.05


@pytest.mark.asyncio
async def test_backoff_sleep_caps_at_max():
    """A large attempt should not exceed cap (within jitter)."""
    import time

    from pyquotex._api._waits import backoff_sleep
    start = time.monotonic()
    await backoff_sleep(5, base=0.01, cap=0.05, jitter=0)
    elapsed = time.monotonic() - start
    assert 0.04 <= elapsed <= 0.15
