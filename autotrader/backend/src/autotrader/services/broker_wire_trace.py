"""Debug-flag-gated WS forensic logger for pyquotex broker calls.

Wraps ``client.api.send_websocket_request`` for the duration of one
broker call and emits two structured logs:

* ``executor.broker_wire.preflight`` — before the call: snapshot of
  pyquotex state we suspect on a silent ``TimeoutError`` (is the
  asset already in :attr:`_subscribed_assets`?  does
  ``realtime_price[asset]`` already hold ticks?  has the
  ``price_ready_{asset}`` event ever fired?  what's
  ``current_asset``?).
* ``executor.broker_wire.postmortem`` — after the call: every
  outgoing socket.io frame captured during the call (with monotonic
  offsets from ``__aenter__``), the same state snapshot taken
  again, plus the call's exception type if it raised.

This converts the otherwise opaque 30 s ``Timeout waiting for
realtime price data`` into a wire-level forensic record we can act
on: did pyquotex actually send the subscribe?  did the broker push
any ticks back in the window?  did ``start_candles_stream``'s
idempotency cache short-circuit the resubscribe entirely?

Off by default — :func:`BrokerWireTrace.if_enabled` short-circuits
to a no-op ctx manager when ``settings.debug_broker_wire`` is false
(or its explicit override is false), so production trades pay
nothing.  When enabled, the only added work is appending a tuple
per outgoing frame.

The wrap is reversible on every exit path (success *and* exception)
— restoring ``send_websocket_request`` in ``__aexit__``'s ``try /
finally`` semantics is the property that protects every subsequent
trade from a leaked monkey-patch.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Cap each captured frame so a runaway log payload can't blow up
# the JSON renderer.  Real subscribe / buy frames are << 300 chars.
_FRAME_TRUNCATE_LEN = 500


def _event_fired(api: Any, key: str) -> bool | None:
    """Read ``api.event_registry._events[key]._has_fired`` defensively.

    Returns:
        ``True`` / ``False`` if the event registry surfaces the key
        and exposes the ``_has_fired`` attribute pyquotex's
        :class:`AsyncEvent` uses (see ``pyquotex.utils.async_utils``).
        ``None`` if the registry shape doesn't match — the tracer is
        a diagnostic; an unfamiliar registry never blocks logging the
        rest.
    """
    try:
        registry = api.event_registry
        events = registry._events
    except AttributeError:
        return None
    event = events.get(key)
    if event is None:
        return False
    return bool(getattr(event, "_has_fired", False))


def _realtime_price_len(api: Any, asset: str) -> int:
    """Length of pyquotex's per-asset price deque, defensively."""
    try:
        deque_ = api.realtime_price.get(asset)
    except AttributeError:
        return 0
    if deque_ is None:
        return 0
    try:
        return len(deque_)
    except TypeError:
        return 0


def _asset_subscribed(client: Any, asset: str) -> bool:
    """Whether ``start_candles_stream``'s idempotency cache holds
    *any* (asset, period) key for this asset.

    pyquotex stores subscribe keys as ``"<asset>,<period>"`` (see
    ``pyquotex/stable_api.py:1525``).  We don't care which period —
    if the asset's name appears at all the next call to
    ``start_candles_stream(asset, ...)`` may short-circuit and skip
    the WS frames entirely.  That's a candidate for the silent-feed
    failure mode and worth recording.
    """
    try:
        keys = client._subscribed_assets
    except AttributeError:
        return False
    if not isinstance(keys, (set, frozenset, list, tuple)):
        return False
    prefix = f"{asset},"
    return any(
        isinstance(k, str) and k.startswith(prefix) for k in keys
    )


@contextlib.asynccontextmanager
async def _noop_ctx() -> Any:
    """Zero-allocation no-op context manager for the disabled path."""
    yield None


class BrokerWireTrace:
    """Async context manager — taps pyquotex's WS send for one call.

    Construct via :meth:`if_enabled` which returns a no-op when the
    debug flag is off.  The ``enabled=True`` flow:

    1. ``__aenter__`` — log a ``preflight`` snapshot, then replace
       ``client.api.send_websocket_request`` with a thin tap that
       records ``(monotonic_offset, frame_str)`` tuples.
    2. Body runs (the broker call).  Every outgoing frame is
       captured.
    3. ``__aexit__`` — restore the original send function (always,
       even on exception), then log a ``postmortem`` with the
       captured frames, the post-call state, and the exception type
       if any.
    """

    @classmethod
    def if_enabled(
        cls,
        client: Any,
        asset: str,
        *,
        enabled: bool,
    ) -> Any:
        """Build the tracer if ``enabled``, else return a no-op ctx.

        The no-op path doesn't even instantiate :class:`BrokerWireTrace`
        — when the operator hasn't flipped the debug flag we pay zero
        allocations beyond a context manager call.  This is the
        contract the rest of the system relies on for the "no added
        latency on the happy path" promise.
        """
        if not enabled:
            return _noop_ctx()
        return cls(client=client, asset=asset)

    def __init__(self, *, client: Any, asset: str) -> None:
        self.client = client
        self.asset = asset
        # ``(monotonic_offset_seconds, raw_frame_string)`` — populated
        # by the send-tap installed in ``__aenter__``.
        self.frames: list[tuple[float, str]] = []
        self._start: float = 0.0
        # ``None`` when the tap couldn't be installed (e.g. no client /
        # no api).  ``__aexit__`` keys on this to decide whether to
        # restore.
        self._original_send: Any = None
        self._api: Any = None

    async def __aenter__(self) -> BrokerWireTrace:
        api = getattr(self.client, "api", None) if self.client is not None else None
        if api is None:
            # Client raced offline between submission and dispatch.
            # Stay a no-op; ``__aexit__`` will see ``_original_send``
            # is ``None`` and do nothing.
            return self
        self._api = api
        self._start = time.monotonic()
        self._log_preflight(api)
        # Snapshot then wrap.  Capture the original in a local closure
        # so concurrent traces (rare; executor serialises trades) can
        # still nest in LIFO order without losing the bottom of the
        # stack.
        self._original_send = api.send_websocket_request
        original = self._original_send
        start = self._start
        frames = self.frames

        async def _tapped(data: str) -> None:
            offset = time.monotonic() - start
            payload = data if len(data) <= _FRAME_TRUNCATE_LEN else (
                data[:_FRAME_TRUNCATE_LEN] + "…<truncated>"
            )
            frames.append((offset, payload))
            await original(data)

        api.send_websocket_request = _tapped
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        # Defensive: snapshot what we'll need before touching ``_api``.
        # The post-mortem must run even if the body raised, but a
        # raise here must not mask the body's exception — ``__aexit__``
        # returning falsy lets the original exception propagate.
        api = self._api
        original_send = self._original_send
        if api is None or original_send is None:
            return None
        # Restore *first*, so even if the postmortem logger throws
        # (it shouldn't, but defensive) we don't leak the wrapper.
        try:
            api.send_websocket_request = original_send
        finally:
            self._log_postmortem(api, exc_type, exc_val)
        return None

    # ------------------------------------------------------------------
    # Structured-log payloads
    # ------------------------------------------------------------------

    def _log_preflight(self, api: Any) -> None:
        log.info(
            "executor.broker_wire.preflight",
            asset=self.asset,
            asset_subscribed=_asset_subscribed(self.client, self.asset),
            realtime_price_len=_realtime_price_len(api, self.asset),
            price_ready_fired=_event_fired(
                api, f"price_ready_{self.asset}",
            ),
            instruments_ready_fired=_event_fired(api, "instruments_ready"),
            buy_confirmed_fired=_event_fired(api, "buy_confirmed"),
            current_asset=getattr(api, "current_asset", None),
            pending_id=getattr(api, "pending_id", None),
            ws_status=str(getattr(getattr(api, "state", None), "status", "?")),
        )

    def _log_postmortem(
        self,
        api: Any,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
    ) -> None:
        elapsed = time.monotonic() - self._start
        outcome = "error" if exc_type is not None else "ok"
        # Render frames as ``[t_offset, payload]`` 2-tuples — structlog
        # encodes them as a JSON array, which is what we want for
        # post-hoc grep / jq inspection.
        rendered_frames = [
            [round(t, 4), payload] for t, payload in self.frames
        ]
        log.info(
            "executor.broker_wire.postmortem",
            asset=self.asset,
            outcome=outcome,
            elapsed_sec=round(elapsed, 4),
            exception=(
                f"{exc_type.__name__}: {exc_val}"
                if exc_type is not None else None
            ),
            frame_count=len(self.frames),
            frames=rendered_frames,
            realtime_price_len=_realtime_price_len(api, self.asset),
            price_ready_fired=_event_fired(
                api, f"price_ready_{self.asset}",
            ),
            buy_confirmed_fired=_event_fired(api, "buy_confirmed"),
            current_asset=getattr(api, "current_asset", None),
            pending_id=getattr(api, "pending_id", None),
            asset_subscribed=_asset_subscribed(self.client, self.asset),
        )
