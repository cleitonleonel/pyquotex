"""Trade executor — turns ``ParsedSignal`` into a broker order.

The executor is the only piece that actually writes money-moving
calls to ``QuotexManager`` (and therefore the broker WebSocket). It
sits behind :mod:`risk_gate` so a misconfigured pipeline can't
accidentally fire a trade.

Every attempt — successful, blocked, or broker-rejected — is
persisted as a :class:`TradeAttempt` row so the dashboard has a
complete audit trail. Result tracking (win / loss / profit) runs in
a side task: we don't block the executor on a 60-second binary
expiry.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import structlog

from autotrader.db import AsyncSessionLocal
from autotrader.models.base import utc_now
from autotrader.models.martingale_state import record_outcome
from autotrader.models.parser_config import ParserConfig, get_config
from autotrader.models.settings import GlobalSettings
from autotrader.models.trade_attempt import (
    TradeAttempt,
    insert_attempt,
    list_pending,
    update_attempt,
)
from autotrader.services.parsers.base import ParsedSignal
from autotrader.services.quotex_manager import (
    QuotexManager,
    QuotexManagerError,
)
from autotrader.services.risk_gate import RiskDecision, evaluate

log = structlog.get_logger(__name__)

# Sentinel ``broker_order_id`` values that the pre-fix code stored when
# pyquotex's ``open_pending`` returned its ``pending_successful`` bool.
# A real ticket is a UUID or short token; these are placeholder strings
# the reconciler can never recover.
_BAD_TICKETS = frozenset({"", "True", "False", "None"})


def _is_real_ticket(value: str | None) -> bool:
    return value is not None and value not in _BAD_TICKETS


def _as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; treat naive timestamps as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TradeExecutor:
    """Drives the broker on a parser-emitted signal."""

    def __init__(
        self,
        *,
        manager: QuotexManager,
        live_trading_enabled_env: bool,
    ) -> None:
        self._manager = manager
        self._live_env = live_trading_enabled_env
        # Track in-flight result-watchers so we can await them on shutdown.
        self._watchers: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        *,
        signal: ParsedSignal,
        parser_config: ParserConfig,
        settings: GlobalSettings,
    ) -> TradeAttempt:
        """Run risk gate, place the trade, persist the attempt.

        Always returns a row — blocked attempts are stored with
        ``status="rejected"`` so the operator can see why nothing
        fired. Result tracking is fired off in a background task.
        """
        async with AsyncSessionLocal() as session:
            decision = await evaluate(
                session=session,
                signal=signal,
                parser_config=parser_config,
                settings=settings,
                account_mode=self._manager.status().account_mode,
                live_trading_enabled_env=self._live_env,
                broker_connected=self._manager.connected,
            )

        attempt = self._build_attempt(signal, parser_config, decision)
        async with AsyncSessionLocal() as session:
            attempt = await insert_attempt(session, attempt)

        if not decision.allowed:
            log.info(
                "executor.rejected",
                config_id=parser_config.id,
                asset=signal.asset,
                reason=decision.reason,
            )
            return attempt

        # Attempt is in DB; place the trade.
        return await self._place(attempt, signal, decision)

    async def shutdown(self) -> None:
        """Wait for in-flight result watchers to finish."""
        if not self._watchers:
            return
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._watchers, return_exceptions=True)
        self._watchers.clear()

    async def reconcile_pending(self) -> None:
        """Sweep ``pending`` rows after a restart.

        In-memory watchers don't survive a restart. Without this, every
        ``pending`` row from the previous process stays pending forever
        — they count against the concurrency cap and silently block
        every new signal.

        For each row we choose the safest action:

        * **Invalid broker_order_id** (None / empty / ``"True"`` /
          ``"False"``) — pre-fix scheduled trades that never captured
          a real ticket. Unrecoverable: mark expired.
        * **Past expected close time + grace** — broker has already
          settled, we missed the event during the downtime window.
          Mark expired.
        * **Still in flight** — re-spawn a watcher with a timeout
          covering the remaining wait so the trade settles cleanly.

        Idempotent: calling twice on the same DB does no harm.
        """
        now = datetime.now(UTC)
        grace = timedelta(minutes=5)

        async with AsyncSessionLocal() as session:
            rows = await list_pending(session)

        respawn = 0
        expired_invalid = 0
        expired_late = 0
        for row in rows:
            attempt_id = row.id or 0
            if not _is_real_ticket(row.broker_order_id):
                await self._mark_reconciled(
                    attempt_id,
                    "broker ticket missing; cannot reconcile after restart",
                )
                expired_invalid += 1
                continue

            placed = _as_utc(row.placed_at or row.created_at)
            duration = timedelta(seconds=row.duration_seconds)
            expected_close = (
                _as_utc(row.fire_at) + duration if row.fire_at is not None
                else placed + duration
            )
            if expected_close + grace < now:
                await self._mark_reconciled(
                    attempt_id,
                    "watcher lost on restart; settlement window already passed",
                )
                expired_late += 1
                continue

            # Still in flight — give the watcher whatever time is left
            # plus a generous slack so a slightly-late close still
            # lands. ``broker_order_id`` is a ``str`` here, narrowed by
            # the ``_is_real_ticket`` guard above.
            assert row.broker_order_id is not None
            remaining = (expected_close + grace - now).total_seconds()
            self._spawn_watcher(
                attempt_id=attempt_id,
                order_id=row.broker_order_id,
                duration=row.duration_seconds,
                timeout=max(remaining, 30.0),
            )
            respawn += 1

        if rows:
            log.info(
                "executor.reconcile",
                total=len(rows),
                respawned=respawn,
                expired_invalid=expired_invalid,
                expired_late=expired_late,
            )

    def _spawn_watcher(
        self,
        *,
        attempt_id: int,
        order_id: str,
        duration: int,
        timeout: float | None,
    ) -> None:
        """Schedule a result-watcher and track it for shutdown."""
        task = asyncio.create_task(
            self._watch_result(attempt_id, order_id, duration, timeout=timeout),
        )
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def _mark_reconciled(self, attempt_id: int, message: str) -> None:
        async with AsyncSessionLocal() as session:
            await update_attempt(
                session,
                attempt_id,
                status="expired",
                error=message,
                settled_at=utc_now(),
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _build_attempt(
        signal: ParsedSignal,
        parser_config: ParserConfig,
        decision: RiskDecision,
    ) -> TradeAttempt:
        return TradeAttempt(
            chat_id=parser_config.chat_id,
            parser_config_id=parser_config.id or 0,
            asset=signal.asset,
            asset_raw=signal.asset_raw,
            direction=signal.direction,
            duration_seconds=signal.duration_seconds,
            stake=decision.stake,
            trade_mode=decision.trade_mode,
            fire_at=signal.fire_at if decision.trade_mode == "scheduled" else None,
            status="pending" if decision.allowed else "rejected",
            error=None if decision.allowed else decision.reason,
            raw_text=signal.raw_text,
        )

    async def _place(
        self,
        attempt: TradeAttempt,
        signal: ParsedSignal,
        decision: RiskDecision,
    ) -> TradeAttempt:
        """Dispatch to ``buy`` (live) or ``open_pending`` (scheduled)."""
        is_scheduled = decision.trade_mode == "scheduled"
        try:
            if is_scheduled:
                # pyquotex.open_pending takes an *ISO 8601 string*, not
                # a datetime. Passing the raw object trips an
                # AttributeError inside pyquotex's normalisation
                # (it calls ``.split()`` on the value). Format here.
                open_time_iso = (
                    signal.fire_at.isoformat()
                    if signal.fire_at is not None
                    else None
                )
                ok, info = await self._manager._client.open_pending(  # type: ignore[union-attr]
                    amount=decision.stake,
                    asset=signal.asset,
                    direction=signal.direction,
                    duration=signal.duration_seconds,
                    open_time=open_time_iso,
                )
            else:
                ok, info = await self._manager._client.buy(  # type: ignore[union-attr]
                    amount=decision.stake,
                    asset=signal.asset,
                    direction=signal.direction,
                    duration=signal.duration_seconds,
                )
        except QuotexManagerError as exc:
            return await self._mark_error(attempt, str(exc))
        except Exception as exc:  # pragma: no cover - broker surfaces vary
            return await self._mark_error(attempt, f"{type(exc).__name__}: {exc}")

        # ``open_pending`` returns ``(ok, pending_successful=True)`` —
        # ``info`` is just a confirmation flag, not the ticket. The
        # actual id ``wait_for_order_close`` keys on lives at
        # ``client.api.pending_id``. ``buy`` returns a dict that
        # ``_extract_order_id`` already understands.
        order_id = (
            self._extract_pending_id() if is_scheduled
            else self._extract_order_id(info)
        )
        async with AsyncSessionLocal() as session:
            updated = await update_attempt(
                session,
                attempt.id or 0,
                status="pending" if ok else "broker_error",
                broker_order_id=order_id,
                placed_at=utc_now(),
                error=None if ok else f"broker rejected: {info!r}",
            )
        if updated is None:  # pragma: no cover - row was just inserted
            return attempt
        attempt = updated

        if ok and order_id:
            # Fire-and-forget result watcher. Live trades use the
            # default duration-based timeout; scheduled trades extend
            # the deadline to ``fire_at + duration + slack`` so the
            # watcher doesn't expire before the broker fires the
            # pending.
            timeout = self._watcher_timeout(signal, is_scheduled=is_scheduled)
            self._spawn_watcher(
                attempt_id=attempt.id or 0,
                order_id=order_id,
                duration=signal.duration_seconds,
                timeout=timeout,
            )

        log.info(
            "executor.placed",
            asset=signal.asset,
            direction=signal.direction,
            mode=decision.trade_mode,
            order_id=order_id,
            ok=ok,
        )
        return attempt

    @staticmethod
    def _extract_order_id(info: object) -> str | None:
        """pyquotex's ``buy`` returns a dict; pull out the trade UUID."""
        if isinstance(info, dict):
            value = info.get("id") or info.get("ticket") or info.get("orderId")
            return str(value) if value is not None else None
        if info is None:
            return None
        return str(info)

    def _extract_pending_id(self) -> str | None:
        """Read the most-recent pending ticket from the pyquotex client.

        ``open_pending`` writes the ticket to ``client.api.pending_id``
        right before returning successfully — that's what
        ``wait_for_order_close`` keys on. The bool we get back from
        ``open_pending`` is just a confirmation flag.
        """
        client = self._manager._client
        if client is None:
            return None
        api = getattr(client, "api", None)
        if api is None:
            return None
        pid = getattr(api, "pending_id", None)
        if pid is None:
            return None
        return str(pid)

    @staticmethod
    def _watcher_timeout(signal: ParsedSignal, *, is_scheduled: bool) -> float | None:
        """How long to wait for a settlement event.

        ``None`` means "use pyquotex's default" (``duration + 30s``),
        which is correct for live trades. Scheduled trades may fire
        far in the future, so we extend the deadline to cover the
        full ``(fire_at - now) + duration + slack`` window.
        """
        if not is_scheduled or signal.fire_at is None:
            return None
        now = datetime.now(UTC)
        fire_at = signal.fire_at
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=UTC)
        wait_secs = max(0.0, (fire_at - now).total_seconds())
        return wait_secs + signal.duration_seconds + 60.0

    async def _mark_error(
        self,
        attempt: TradeAttempt,
        message: str,
    ) -> TradeAttempt:
        async with AsyncSessionLocal() as session:
            updated = await update_attempt(
                session,
                attempt.id or 0,
                status="broker_error",
                error=message,
            )
        log.warning("executor.broker_error", attempt_id=attempt.id, error=message)
        return updated or attempt

    async def _watch_result(
        self,
        attempt_id: int,
        order_id: str,
        duration: int,
        timeout: float | None = None,  # noqa: ASYNC109  (forwarded to pyquotex)
    ) -> None:
        """Wait for the broker's win/loss event and persist."""
        if self._manager._client is None:
            return
        try:
            status, profit = await self._manager._client.wait_for_order_close(
                order_id, duration=duration, timeout=timeout,
            )
        except Exception as exc:  # pragma: no cover - broker timing surfaces vary
            log.warning(
                "executor.watch.failed",
                attempt_id=attempt_id,
                order_id=order_id,
                error=str(exc),
            )
            async with AsyncSessionLocal() as session:
                await update_attempt(
                    session,
                    attempt_id,
                    status="expired",
                    error=f"watch: {exc}",
                    settled_at=utc_now(),
                )
            return

        async with AsyncSessionLocal() as session:
            updated = await update_attempt(
                session,
                attempt_id,
                status="won" if status == "win" else "lost",
                profit=float(profit),
                settled_at=utc_now(),
            )

            # Tick the martingale ladder so the next trade for this
            # parser uses the right step. We re-fetch the parser
            # config because Phase 4 stored ``parser_config_id`` only —
            # the live ParserConfig may have been edited since.
            if updated is not None:
                cfg = await get_config(session, updated.parser_config_id)
                if cfg is not None and cfg.martingale_enabled:
                    await record_outcome(
                        session,
                        cfg.id or 0,
                        won=(status == "win"),
                        last_stake=updated.stake,
                        max_streak=cfg.martingale_max_streak,
                        reset_on_win=cfg.martingale_reset_on_win,
                    )
        log.info(
            "executor.settled",
            attempt_id=attempt_id,
            order_id=order_id,
            status=status,
            profit=profit,
        )
