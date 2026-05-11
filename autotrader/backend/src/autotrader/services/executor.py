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
import math
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
from autotrader.services.event_bus import TradeEventBus
from autotrader.services.parsers.base import ParsedSignal
from autotrader.services.quotex_manager import (
    QuotexManager,
    QuotexManagerError,
)
from autotrader.services.risk_gate import RiskDecision, evaluate

log = structlog.get_logger(__name__)

# Extra grace before a pending row whose nominal settle window has
# passed gets marked ``expired`` with the clearer note. The 60s
# slack covers broker-side processing jitter — pyquotex sometimes
# emits ``order_closed`` a beat after the natural expiry.
_RECONCILE_SLACK_SECONDS = 60


def _wire_iso8601(value: datetime | None) -> str | None:
    """Format ``value`` as the broker's documented wire timestamp.

    Pyquotex's ``ws2.qxbroker.com`` capture pins the format to
    ``YYYY-MM-DDTHH:MM:SS.000Z``. Python's ``datetime.isoformat``
    yields the equivalent ``...+00:00`` representation, which the
    broker's parser silently mistakes for "broker-local naive time"
    in some accounts — the schedule then drifts by the broker's
    default offset. Use the documented form to remove that ambiguity.
    """
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _attempt_to_payload(
    attempt: TradeAttempt,
    *,
    state: object | None = None,
    cfg: object | None = None,
) -> dict[str, object]:
    """Mirror of TradeAttemptResponse for the event bus.

    Optional ``state`` (MartingaleState) + ``cfg`` (ParserConfig) embed
    a ``ladder`` snapshot so admin-bot notifications + frontend rows
    can render streak progress without a re-fetch. Both default to
    ``None`` for callers that don't have the state in hand (e.g.
    on insert, before the watcher has settled).
    """
    payload: dict[str, object] = {
        "id": attempt.id or 0,
        "chat_id": attempt.chat_id,
        "parser_config_id": attempt.parser_config_id,
        "asset": attempt.asset,
        "asset_raw": attempt.asset_raw,
        "direction": attempt.direction,
        "duration_seconds": attempt.duration_seconds,
        "stake": attempt.stake,
        "trade_mode": attempt.trade_mode,
        "fire_at": attempt.fire_at.isoformat() if attempt.fire_at else None,
        "status": attempt.status,
        "broker_order_id": attempt.broker_order_id,
        "profit": attempt.profit,
        "error": attempt.error,
        "received_at": attempt.received_at.isoformat(),
        "placed_at": attempt.placed_at.isoformat() if attempt.placed_at else None,
        "settled_at": attempt.settled_at.isoformat() if attempt.settled_at else None,
    }
    if state is not None and cfg is not None:
        # Compute the next-stake hint the same way risk_gate would on
        # the next signal: streak first, martingale second, base last.
        cur_win = getattr(state, "current_win_streak", 0)
        last_payout = getattr(state, "last_payout", 0.0)
        cur_loss = getattr(state, "current_streak", 0)
        if (
            getattr(cfg, "winning_streak_enabled", False)
            and cur_win > 0
            and last_payout > 0
        ):
            next_hint = math.ceil(last_payout)
        elif getattr(cfg, "martingale_enabled", False) and cur_loss > 0:
            next_hint = math.ceil(
                getattr(cfg, "default_stake", 0)
                * (getattr(cfg, "martingale_multiplier", 2.0) ** cur_loss),
            )
        else:
            next_hint = math.ceil(getattr(cfg, "default_stake", 0))
        payload["ladder"] = {
            "current_streak": cur_loss,
            "max_streak": getattr(cfg, "martingale_max_streak", 0),
            "current_win_streak": cur_win,
            "max_win_streak": getattr(cfg, "winning_streak_max_level", 0),
            "next_stake_hint": int(next_hint),
        }
    return payload


class TradeExecutor:
    """Drives the broker on a parser-emitted signal."""

    def __init__(
        self,
        *,
        manager: QuotexManager,
        live_trading_enabled_env: bool,
        event_bus: TradeEventBus | None = None,
    ) -> None:
        self._manager = manager
        self._live_env = live_trading_enabled_env
        # Track in-flight result-watchers so we can await them on shutdown.
        self._watchers: set[asyncio.Task[None]] = set()
        # Live dashboard fan-out. Optional so unit tests that don't
        # care about the feed can leave it ``None``.
        self._event_bus = event_bus

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

        self._publish(attempt)

        if not decision.allowed:
            log.info(
                "executor.rejected",
                config_id=parser_config.id,
                asset=signal.asset,
                reason=decision.reason,
            )
            # Fan out to the admin-bot notifier (and any other
            # consumer). The bus is fire-and-forget; missing
            # subscribers silently no-op.
            if self._event_bus is not None:
                self._event_bus.publish("risk.rejected", {
                    "chat_id": parser_config.chat_id,
                    "parser_config_id": parser_config.id,
                    "parser_name": parser_config.name,
                    "asset": signal.asset,
                    "direction": signal.direction,
                    "reason": decision.reason,
                })
            return attempt

        # Attempt is in DB; place the trade.
        return await self._place(attempt, signal, decision)

    async def shutdown(self) -> None:
        """Cancel and await in-flight watchers so the lifespan exits cleanly.

        Result-watchers (``_watch_result``) and deferred-reconcile
        runners are both tracked in ``_watchers``. Deferred runners
        sleep on a binary-options settle window — without an explicit
        cancel they would block shutdown for the remaining
        ``placed_at + duration + slack`` seconds.
        """
        if not self._watchers:
            return
        for task in self._watchers:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._watchers, return_exceptions=True)
        self._watchers.clear()

    async def reconcile_pending(self) -> None:
        """Reclassify ``pending`` rows after a restart.

        Three buckets:

        * ``placed_at is None`` — broker never accepted the order.
          Mark ``expired`` immediately with the historic
          "watcher lost on restart" note.
        * ``placed_at + duration_seconds + slack > utcnow()`` — the
          broker is still inside the binary-options window. Leave
          the row ``pending`` and spawn a deferred task that sleeps
          until ``placed_at + duration + slack`` and then marks
          ``expired`` with the clearer note.
        * ``placed_at + duration_seconds + slack <= utcnow()`` — the
          broker has already settled. Mark ``expired`` immediately
          with the clearer note.

        In every "settled but unrecoverable" case the martingale
        ladder is **not** ticked — we don't know the outcome and
        guessing would silently corrupt recovery sequences.
        """
        async with AsyncSessionLocal() as session:
            rows = await list_pending(session)
        if not rows:
            return

        legacy_note = (
            "watcher lost on restart — pyquotex doesn't track tickets "
            "across reconnects, so the outcome can't be tied back. "
            "Check broker history if needed; reset the martingale "
            "ladder if the recovery sequence got out of sync"
        )
        clearer_note = (
            "settle window passed; broker likely settled this trade "
            "but pyquotex couldn't tie the result back across the "
            "restart. Check broker history if the outcome matters; "
            "the martingale ladder is left untouched"
        )

        now = datetime.now(UTC)
        deferred = 0
        immediate = 0
        for row in rows:
            placed = row.placed_at
            if placed is None:
                await self._mark_reconciled(row.id or 0, legacy_note)
                immediate += 1
                continue

            placed_aware = (
                placed if placed.tzinfo is not None
                else placed.replace(tzinfo=UTC)
            )
            settle_at = placed_aware + timedelta(
                seconds=row.duration_seconds + _RECONCILE_SLACK_SECONDS,
            )
            wait_seconds = (settle_at - now).total_seconds()
            if wait_seconds <= 0:
                await self._mark_reconciled(row.id or 0, clearer_note)
                immediate += 1
            else:
                self._spawn_deferred_reconcile(
                    attempt_id=row.id or 0,
                    wait_seconds=wait_seconds,
                    note=clearer_note,
                )
                deferred += 1

        log.info(
            "executor.reconcile",
            immediate_expired=immediate,
            deferred=deferred,
        )

    def _spawn_deferred_reconcile(
        self,
        *,
        attempt_id: int,
        wait_seconds: float,
        note: str,
    ) -> None:
        """Schedule a delayed mark-expired so in-flight trades aren't
        nuked the moment the API restarts mid-window. Tracked in the
        same ``_watchers`` set as result-watchers so ``shutdown()``
        awaits cancellation cleanly."""
        async def _runner() -> None:
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return
            await self._mark_reconciled(attempt_id, note)

        task = asyncio.create_task(_runner())
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

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
            updated = await update_attempt(
                session,
                attempt_id,
                status="expired",
                error=message,
                settled_at=utc_now(),
            )
        if updated is not None:
            self._publish(updated)

    def _publish(
        self,
        attempt: TradeAttempt,
        *,
        state: object | None = None,
        cfg: object | None = None,
    ) -> None:
        """Fire-and-forget broadcast of a trade row to dashboard subscribers.

        The payload mirrors ``TradeAttemptResponse`` so the frontend can
        merge it into the existing list by ``id`` without re-fetching.
        Datetimes are ISO-8601 strings (the wire format the REST
        endpoint already uses).
        """
        if self._event_bus is None:
            return
        self._event_bus.publish(
            "trade.upserted", _attempt_to_payload(attempt, state=state, cfg=cfg),
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
                # Pyquotex's wire format is the strict ISO-8601 UTC
                # form ``YYYY-MM-DDTHH:MM:SS.000Z`` — verified against
                # ``ws2.qxbroker.com``. Python's ``datetime.isoformat``
                # produces the equivalent ``...+00:00`` form, but the
                # broker's parser doesn't always treat that as UTC; in
                # the wild we've seen it fall back to "broker-local
                # time", which silently shifts the schedule by the
                # broker's default offset (commonly +2h).
                open_time_iso = _wire_iso8601(signal.fire_at)
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
        self._publish(attempt)

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
        if updated is not None:
            self._publish(updated)
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
            # Broker disconnected between placement and watch start.
            # Don't leave the row ``pending`` forever — the
            # concurrency cap would silently block every new signal.
            await self._mark_reconciled(
                attempt_id,
                "broker disconnected before watcher could attach",
            )
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
                expired = await update_attempt(
                    session,
                    attempt_id,
                    status="expired",
                    error=f"watch: {exc}",
                    settled_at=utc_now(),
                )
            if expired is not None:
                self._publish(expired)
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
            new_state = None
            cfg = None
            if updated is not None:
                cfg = await get_config(session, updated.parser_config_id)
                if cfg is not None and (cfg.martingale_enabled or cfg.winning_streak_enabled):
                    new_state = await record_outcome(
                        session,
                        cfg.id or 0,
                        won=(status == "win"),
                        last_stake=updated.stake,
                        last_profit=float(profit),
                        max_streak=cfg.martingale_max_streak,
                        reset_on_win=cfg.martingale_reset_on_win,
                        winning_streak_enabled=cfg.winning_streak_enabled,
                        winning_streak_max_level=cfg.winning_streak_max_level,
                    )
        if updated is not None:
            # Pass state+cfg so the admin-bot notification has ladder
            # context. Both came from the same session above.
            self._publish(updated, state=new_state, cfg=cfg)
        log.info(
            "executor.settled",
            attempt_id=attempt_id,
            order_id=order_id,
            status=status,
            profit=profit,
        )

        # Auto-recovery: when a trade *loses* and the parser is opted
        # into auto-recovery, immediately fire a same-asset / same-
        # direction trade with the multiplied stake. This mirrors how
        # binary-options channels phrase their gale rule (e.g. *"IF
        # LOSS TAKE 1 STEP MTG (Same Direction Double Amount)"*) — the
        # channel doesn't repost the signal, it expects the bot to
        # fire the recovery itself. We gate on ``current_streak > 0``
        # so we don't fire when ``record_outcome`` just hit
        # ``max_streak`` and reset the ladder (recovery exhausted).
        # Diagnostic log shows every condition value so a "recovery
        # didn't fire" question can be answered without reading source.
        log.info(
            "executor.auto_recovery.gate",
            attempt_id=attempt_id,
            updated_present=updated is not None,
            status=status,
            cfg_present=cfg is not None,
            martingale_enabled=getattr(cfg, "martingale_enabled", None),
            auto_recovery=getattr(cfg, "martingale_auto_recovery", None),
            new_state_present=new_state is not None,
            current_streak=getattr(new_state, "current_streak", None),
            max_streak=getattr(cfg, "martingale_max_streak", None),
        )
        if (
            updated is not None
            and status != "win"
            and cfg is not None
            and cfg.martingale_enabled
            and cfg.martingale_auto_recovery
            and new_state is not None
            and new_state.current_streak > 0
        ):
            await self._fire_auto_recovery(
                original=updated,
                cfg=cfg,
                streak=new_state.current_streak,
            )

    async def _fire_auto_recovery(
        self,
        *,
        original: TradeAttempt,
        cfg: ParserConfig,
        streak: int,
    ) -> None:
        """Submit a recovery trade derived from the lost ``original``.

        Refetches the parser config inside this method so an operator
        who disables the parser mid-loss-streak doesn't get an extra
        recovery trade. The cached ``cfg`` from the calling settle
        path may be stale by the time we reach here.

        Goes through the full ``submit`` path so the same risk gate
        guards (kill switch, daily loss cap, max-concurrent, REAL-mode
        env flag) apply. Stake is left ``None`` on the synthesised
        signal so the risk gate computes ``base × multiplier^streak``
        from the freshly-incremented martingale state — a single
        source of truth for "what's the stake right now".
        """
        from autotrader.services.parsers import ParsedSignal  # noqa: PLC0415

        log.info(
            "executor.auto_recovery.entered",
            config_id=cfg.id,
            original_attempt_id=original.id,
            streak=streak,
        )
        async with AsyncSessionLocal() as session:
            fresh_cfg = (
                await get_config(session, cfg.id or 0)
                if cfg.id is not None
                else None
            )
            settings_row = await session.get(GlobalSettings, 1)
            if settings_row is None:
                settings_row = GlobalSettings(id=1)

        if fresh_cfg is None:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="parser_config deleted",
            )
            return
        if not fresh_cfg.enabled:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="parser_config disabled",
            )
            return
        if not fresh_cfg.martingale_enabled or not fresh_cfg.martingale_auto_recovery:
            log.info(
                "executor.auto_recovery.skipped",
                config_id=cfg.id,
                original_attempt_id=original.id,
                reason="martingale toggles flipped off mid-streak",
            )
            return

        signal = ParsedSignal(
            asset=original.asset,
            direction=original.direction,  # type: ignore[arg-type]
            duration_seconds=original.duration_seconds,
            stake=None,                          # risk gate computes
            fire_at=None,                        # ASAP / live
            raw_text=f"[auto-recovery for trade #{original.id}]",
            parser_id=f"cfg-{fresh_cfg.id}-recovery-{streak}",
            asset_raw=original.asset_raw,
        )
        # Auto-recovery is by definition immediate — the next ladder
        # step has to fire NOW, not at the channel's scheduled time.
        # Channels that post scheduled signals (``trade_mode=scheduled``
        # or ``auto`` with a parsed ``fire_at``) would otherwise see the
        # synthesized ``fire_at=None`` signal rejected by the risk gate
        # with "trade_mode=scheduled but signal has no fire_at" — every
        # recovery becoming a phantom rejected row. Override to ``live``
        # on this in-memory copy only; the row is detached from its
        # session so the mutation never reaches the DB.
        if fresh_cfg.trade_mode != "live":
            log.info(
                "executor.auto_recovery.mode_override",
                config_id=fresh_cfg.id,
                original_attempt_id=original.id,
                cfg_trade_mode=fresh_cfg.trade_mode,
                effective_trade_mode="live",
            )
            fresh_cfg.trade_mode = "live"
        try:
            attempt = await self.submit(
                signal=signal,
                parser_config=fresh_cfg,
                settings=settings_row,
            )
        except Exception as exc:  # pragma: no cover - belt + braces
            log.exception(
                "executor.auto_recovery.failed",
                config_id=fresh_cfg.id,
                original_attempt_id=original.id,
                streak=streak,
                error=str(exc),
            )
            return
        log.info(
            "executor.auto_recovery.fired",
            config_id=fresh_cfg.id,
            original_attempt_id=original.id,
            streak=streak,
            asset=original.asset,
            direction=original.direction,
            recovery_attempt_id=attempt.id,
            recovery_status=attempt.status,
            recovery_stake=attempt.stake,
        )
