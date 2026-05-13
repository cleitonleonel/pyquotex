"""Live execution pipeline.

A single instance per app, owned by the FastAPI lifespan. Its job is
to take a raw Telegram message and route it through the right
parser(s) for that chat, with appropriate state preservation across
messages, then hand any emitted signals to the trade executor.

Parser instances are *cached per ParserConfig.id* so stateful types
(``prep_trigger``, the concat aggregator) keep their pending preps
and message buffers between dispatches. The cache is invalidated
when a config row is updated or deleted via the router.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import AsyncSessionLocal
from autotrader.models.base import utc_now
from autotrader.models.parser_config import (
    ParserConfig,
)
from autotrader.models.parser_config import (
    list_configs as _list_configs,
)
from autotrader.models.settings import GlobalSettings
from autotrader.models.trade_attempt import find_recent_by_tg_message_id
from autotrader.models.watched_channel import WatchedChannel
from autotrader.services.event_bus import TradeEventBus
from autotrader.services.executor import TradeExecutor
from autotrader.services.parsers import (
    Aggregator,
    BatchParser,
    ParsedSignal,
    ParseError,
    ParseOutcome,
    Parser,
    ParserBuildError,
    PrepTriggerParser,
    RawMessage,
    build_parser,
)
from autotrader.services.quotex_manager import QuotexManager

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class _CachedParser:
    """One row's parser plus any wrapper state."""

    config_revision: tuple[str, ...]   # snapshot used to detect drift
    parser_type: str
    parser: Parser                      # the inner parser
    aggregator: Aggregator | None       # set when concat aggregation is on
    config_row: ParserConfig            # held for stake/trade_mode lookup


def _config_signature(row: ParserConfig) -> tuple[str, ...]:
    """A stable string-tuple key for the parser-config 'shape'.

    If any of these fields changes the cached parser is rebuilt.
    """
    return (
        row.parser_type,
        row.parser_config_json,
        str(row.timezone_offset_minutes),
        row.asset_aliases_json,
        str(row.default_duration_seconds),
        str(row.aggregate_window_seconds),
    )


class Pipeline:
    """Routes raw messages → parsers → executor."""

    # Cap on the in-memory ring buffer of recent parsing decisions
    # streamed to the dashboard. Sized so a live operator catching up
    # after a 10-minute coffee break still sees the last few minutes
    # of channel chatter at typical signal-channel volumes.
    _DECISION_RING_SIZE = 200

    def __init__(
        self,
        *,
        manager: QuotexManager,
        executor: TradeExecutor,
        event_bus: TradeEventBus | None = None,
    ) -> None:
        self._manager = manager
        self._executor = executor
        # Optional fan-out for live dashboard observability. Decisions
        # publish ``pipeline.decision`` events alongside the existing
        # ``trade.upserted`` events the executor emits — same WS frame
        # shape, different ``type`` discriminator on the wire.
        self._event_bus = event_bus
        self._parsers: dict[int, _CachedParser] = {}
        # Most-recent-N parsing decisions, in chronological order
        # (oldest first; the router reverses for display). ``deque``
        # with ``maxlen`` is the right primitive — bounded memory,
        # O(1) append, no manual eviction.
        self._recent_decisions: deque[dict[str, object]] = deque(
            maxlen=self._DECISION_RING_SIZE,
        )
        # Serialise dispatch *per chat* so a flurry of messages from the
        # same channel doesn't race the stateful parsers (Aggregator,
        # PrepTriggerParser).
        self._chat_locks: dict[int, asyncio.Lock] = {}
        # Phase 0 instrumentation (audit 2026-05-13, H1): track recent
        # message fingerprints per chat so a Pyrogram replay (or any
        # other source of duplicate delivery) surfaces in the log
        # **without** changing dispatch behaviour. Phase 2 replaces
        # this with a real ``(chat_id, message_id)`` dedup gate that
        # short-circuits the duplicate; Phase 0 just measures whether
        # it actually happens in production.
        #
        # Fingerprint = ``(text[:200], sender_id)``. Two messages with
        # the same text from the same sender within
        # ``_DUPLICATE_WINDOW`` are flagged. False-positive risk: a
        # channel that legitimately posts the same content twice
        # (e.g. a repeated daily greeting) will fire one alert per
        # repeat — acceptable signal-to-noise for an observation pass.
        self._recent_fingerprints: dict[
            int, OrderedDict[tuple[str, int], datetime],
        ] = {}

    # Tuning constants for the Phase 0 duplicate detector. Sized so
    # the per-chat memory stays bounded under a chatty channel: 200
    # entries * ~250B/entry ≈ 50KB worst case per active chat.
    _DUPLICATE_WINDOW: timedelta = timedelta(minutes=10)
    _DUPLICATE_FINGERPRINT_CAP: int = 200

    # ------------------------------------------------------------------
    # Decision feed
    # ------------------------------------------------------------------

    @property
    def recent_decisions(self) -> list[dict[str, object]]:
        """Snapshot of the in-memory decision ring, newest-first.

        Late-loading dashboards read this once over HTTP, then the WS
        feed keeps them current. Always returns a fresh list so callers
        can't mutate the deque from underneath us.
        """
        return list(reversed(self._recent_decisions))

    def _record_decision(self, payload: dict[str, object]) -> None:
        """Append to the ring + fan-out to live subscribers.

        ``ts`` is set here so producers don't have to remember. Synchronous
        on purpose: this runs on the dispatch hot path, behind the per-
        chat lock, and shouldn't yield.
        """
        payload = {**payload, "ts": utc_now().isoformat()}
        self._recent_decisions.append(payload)
        if self._event_bus is not None:
            self._event_bus.publish("pipeline.decision", payload)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate(self, config_id: int) -> None:
        self._parsers.pop(config_id, None)

    def invalidate_all(self) -> None:
        self._parsers.clear()

    def invalidate_for_chat(self, chat_id: int) -> None:
        """Drop every cached parser whose config row's chat_id matches.

        Called by the unwatch endpoint so cached parsers belonging to
        a no-longer-watched chat don't occupy memory until the next
        signature-drift rebuild. Dispatch already filters out
        unwatched chats via ``WatchedChannel.enabled``, so this is
        memory-only hygiene; behaviour is unchanged either way.
        """
        for cfg_id in [
            cfg_id
            for cfg_id, cached in self._parsers.items()
            if cached.config_row.chat_id == chat_id
        ]:
            self._parsers.pop(cfg_id, None)

    async def warm_up(self) -> dict[str, int]:
        """Materialise every enabled parser_config into the cache.

        Called by the lifespan after reconcile_pending and before the
        Telegram message handler is attached, so by the time messages
        flow in every parser is ready. Failures (bad regex / missing
        required field) record a ``build_failed`` decision and
        continue — the lifespan is not aborted.

        Returns ``{built: N, failed: M}`` for log + telemetry.
        Idempotent: re-running re-validates configs.
        """
        async with AsyncSessionLocal() as session:
            configs = await _list_configs(session)
        built = 0
        failed = 0
        for cfg in configs:
            if not cfg.enabled:
                continue
            if self.prebuild(cfg):
                built += 1
            else:
                failed += 1
        log.info("pipeline.warm_up", built=built, failed=failed)
        return {"built": built, "failed": failed}

    def prebuild(self, cfg: ParserConfig) -> bool:
        """Build a single parser into the cache. Returns True on
        success, False on ParserBuildError. Failures emit a
        ``build_failed`` decision so the dashboard surfaces them
        immediately, not on first message arrival.
        """
        try:
            self._get_or_build(cfg)
        except ParserBuildError as exc:
            log.error(
                "pipeline.parser_build_failed",
                config_id=cfg.id,
                name=cfg.name,
                error=str(exc),
            )
            self._record_decision(
                {
                    "chat_id": cfg.chat_id,
                    "parser_config_id": cfg.id,
                    "parser_name": cfg.name,
                    "parser_type": cfg.parser_type,
                    "outcome": "build_failed",
                    "reasons": [str(exc)],
                    "signals": 0,
                    "text_preview": "(warm-up)",
                },
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, message: RawMessage) -> None:
        """Run every applicable parser for ``message.chat_id`` and
        forward emitted signals to the executor.

        Async-safe and idempotent: callable from the Pyrogram update
        handler with no extra synchronisation.
        """
        async with self._chat_locks.setdefault(message.chat_id, asyncio.Lock()):
            await self._dispatch_locked(message)

    def _check_duplicate_candidate(self, message: RawMessage) -> None:
        """Phase 0 instrumentation: log ``pipeline.duplicate_candidate``
        when ``message`` looks like a replay of one we recently dispatched
        for the same chat. Side-effect: updates the per-chat fingerprint
        LRU. Does **not** suppress the message — Phase 2 will do that
        once we know how often this actually fires in production.

        Runs synchronously under the per-chat dispatch lock, so the
        check and update are atomic for this chat.
        """
        key = ((message.text or "")[:200], message.sender_id)
        now = datetime.now(UTC)
        seen = self._recent_fingerprints.setdefault(message.chat_id, OrderedDict())
        # Evict entries older than the window. ``OrderedDict`` preserves
        # insertion order so this is O(1) per evicted item.
        cutoff = now - self._DUPLICATE_WINDOW
        while seen:
            oldest_key, oldest_ts = next(iter(seen.items()))
            if oldest_ts >= cutoff:
                break
            seen.pop(oldest_key)
        prior = seen.get(key)
        if prior is not None:
            log.warning(
                "pipeline.duplicate_candidate",
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                elapsed_seconds=round((now - prior).total_seconds(), 3),
                text_preview=(message.text or "")[:120],
            )
        # Refresh / insert with the latest timestamp. ``move_to_end`` on
        # the existing key would keep the *prior* timestamp; we want the
        # most-recent-seen so the next replay is timed against this one.
        if key in seen:
            seen.pop(key)
        seen[key] = now
        # Bound memory per chat. Eviction is FIFO by insertion order
        # (== seen order) which approximates LRU well enough for a
        # short-window cache.
        while len(seen) > self._DUPLICATE_FINGERPRINT_CAP:
            seen.popitem(last=False)

    async def _dispatch_locked(self, message: RawMessage) -> None:
        # Phase 0 instrumentation runs FIRST so duplicate replay is
        # observed regardless of whether the message ultimately matches
        # a parser. The check is cheap (O(1) dict ops, one log emit on
        # hit) and side-effect-free outside the per-chat fingerprint
        # cache.
        self._check_duplicate_candidate(message)
        async with AsyncSessionLocal() as session:
            # Drop the message early when it comes from a chat the
            # operator hasn't opted into. Pyrogram's MessageHandler
            # fires for *every* incoming update on the user's account
            # (bot DMs, unrelated groups, friends) — most of those
            # are noise from the trader's perspective. Filtering here
            # keeps the dispatch log + the recent-decisions panel
            # focussed on the chats the operator actually cares about.
            #
            # An *enabled* watch but with zero parsers is still a
            # legitimate state that flows through (we want the
            # ``no_configs`` decision to surface so the operator
            # knows they need to add a parser). A *disabled* watch
            # row is treated the same as no row at all.
            watched = await session.get(WatchedChannel, message.chat_id)
            if watched is None or not watched.enabled:
                log.debug(
                    "pipeline.skip.unwatched",
                    chat_id=message.chat_id,
                )
                return
            # Phase 2 idempotency gate (audit 2026-05-13, H1). Run
            # before parser build so a Pyrogram replay never wastes
            # CPU on parsing or hits the executor. Keys on the
            # persisted ``(chat_id, tg_message_id)`` of any
            # ``TradeAttempt`` from the last 10 min — including
            # ``rejected`` ones, because "we already processed this
            # message" is the right invariant regardless of outcome.
            #
            # No ``message_id`` (synthetic test replay, batch passes)
            # falls through; the Phase 0 fingerprint warning above
            # already flagged any obvious replay shape.
            message_id = getattr(message, "message_id", None)
            if message_id is not None:
                existing = await find_recent_by_tg_message_id(
                    session,
                    chat_id=message.chat_id,
                    tg_message_id=int(message_id),
                )
                if existing is not None:
                    self._record_decision(
                        {
                            "chat_id": message.chat_id,
                            "parser_config_id": existing.parser_config_id,
                            "parser_name": None,
                            "parser_type": None,
                            "outcome": "duplicate",
                            "reasons": [
                                f"tg_message_id={message_id} already "
                                f"processed as TradeAttempt #{existing.id} "
                                f"(status={existing.status})",
                            ],
                            "signals": 0,
                            "text_preview": (message.text or "")[:120],
                        },
                    )
                    log.warning(
                        "pipeline.duplicate_blocked",
                        chat_id=message.chat_id,
                        tg_message_id=message_id,
                        prior_attempt_id=existing.id,
                        prior_status=existing.status,
                    )
                    return
            configs = await self._enabled_configs_for(session, message.chat_id)
            settings = await self._get_settings(session)

        # Observability: log the routing decision *before* the early
        # returns. The two most common silent failures here are
        # (a) ``configs == []`` because no ParserConfig row matches
        # this chat_id (often a -100… sign-convention mismatch with
        # what the dashboard saved) and (b) ``pipeline_active=False``
        # because the master switch is off. Both used to vanish into
        # ``return`` with no trace.
        log.info(
            "pipeline.dispatch",
            chat_id=message.chat_id,
            enabled_configs=len(configs),
            config_ids=[c.id for c in configs],
            pipeline_active=settings.pipeline_active,
            kill_switch=settings.kill_switch_engaged,
        )
        if not configs:
            # Surface "message arrived but no parser is bound to this
            # chat" — the silent-drop class of failure the dashboard
            # previously had no way to reveal. We capture a short
            # text preview so operators can spot wrong-chat messages
            # without scraping logs.
            self._record_decision(
                {
                    "chat_id": message.chat_id,
                    "parser_config_id": None,
                    "parser_name": None,
                    "parser_type": None,
                    "outcome": "no_configs",
                    "reasons": [],
                    "signals": 0,
                    "text_preview": (message.text or "")[:120],
                },
            )
            return

        # Bail early when the master switch is off — saves us from
        # building parsers and walking the priority list.
        if not settings.pipeline_active:
            self._record_decision(
                {
                    "chat_id": message.chat_id,
                    "parser_config_id": None,
                    "parser_name": None,
                    "parser_type": None,
                    "outcome": "pipeline_inactive",
                    "reasons": ["master switch off"],
                    "signals": 0,
                    "text_preview": (message.text or "")[:120],
                },
            )
            return

        for cfg in configs:
            try:
                cached = self._get_or_build(cfg)
            except ParserBuildError as exc:
                log.error(
                    "pipeline.parser_build_failed",
                    config_id=cfg.id,
                    name=cfg.name,
                    error=str(exc),
                )
                self._record_decision(
                    {
                        "chat_id": message.chat_id,
                        "parser_config_id": cfg.id,
                        "parser_name": cfg.name,
                        "parser_type": cfg.parser_type,
                        "outcome": "build_failed",
                        "reasons": [str(exc)],
                        "signals": 0,
                        "text_preview": (message.text or "")[:120],
                    },
                )
                continue

            # ``_dispatch_to_cached`` declares ``Iterable[ParseOutcome]``
            # — materialise once so we can both filter for signals and
            # later pull rejection reasons without re-iterating a
            # potentially-spent generator.
            outcomes = list(self._dispatch_to_cached(cached, message))
            signals = [o for o in outcomes if isinstance(o, ParsedSignal)]
            if not signals:
                # Stateful parsers (Aggregator, PrepTriggerParser) can
                # yield no signal on this message and still fire on the
                # next, so just skip ahead to the next config — but
                # surface it so users can see "the parser ran and
                # rejected the message" vs "the parser never ran".
                # ``ParseError.reason`` is the tightest hint at *why*.
                reasons = [
                    o.reason for o in outcomes if isinstance(o, ParseError)
                ]
                log.info(
                    "pipeline.no_match",
                    config_id=cfg.id,
                    name=cfg.name,
                    parser_type=cfg.parser_type,
                    reasons=reasons,
                )
                self._record_decision(
                    {
                        "chat_id": message.chat_id,
                        "parser_config_id": cfg.id,
                        "parser_name": cfg.name,
                        "parser_type": cfg.parser_type,
                        "outcome": "no_match",
                        "reasons": reasons,
                        "signals": 0,
                        "text_preview": (message.text or "")[:120],
                    },
                )
                continue

            log.info(
                "pipeline.matched",
                config_id=cfg.id,
                name=cfg.name,
                signals=len(signals),
            )
            self._record_decision(
                {
                    "chat_id": message.chat_id,
                    "parser_config_id": cfg.id,
                    "parser_name": cfg.name,
                    "parser_type": cfg.parser_type,
                    "outcome": "matched",
                    "reasons": [],
                    "signals": len(signals),
                    "text_preview": (message.text or "")[:120],
                },
            )
            for sig in signals:
                # Re-fetch settings before each submit so a kill-switch
                # flip mid-batch takes effect immediately.
                async with AsyncSessionLocal() as session:
                    fresh = await self._get_settings(session)
                # Phase 2 (audit 2026-05-13, H1): pass the source
                # Pyrogram message id so the persisted TradeAttempt is
                # findable by the next dispatch's dedup gate. Batch
                # parsers fan one message out to N signals — every row
                # carries the SAME ``tg_message_id`` (correct: they
                # all derive from one inbound message).
                tg_message_id = getattr(message, "message_id", None)
                await self._executor.submit(
                    signal=sig,
                    parser_config=cached.config_row,
                    settings=fresh,
                    tg_message_id=int(tg_message_id) if tg_message_id is not None else None,
                )
            # Priority orders the walk, but every matching enabled
            # parser fires its own trade — they're independent
            # subscribers, not alternatives.

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _enabled_configs_for(
        session: AsyncSession,
        chat_id: int,
    ) -> list[ParserConfig]:
        all_configs = await _list_configs(session, chat_id=chat_id)
        # ``list_configs`` already orders by priority then id.
        return [c for c in all_configs if c.enabled]

    @staticmethod
    async def _get_settings(session: AsyncSession) -> GlobalSettings:
        row = await session.get(GlobalSettings, 1)
        if row is None:
            row = GlobalSettings(id=1)
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row

    def _get_or_build(self, cfg: ParserConfig) -> _CachedParser:
        sig = _config_signature(cfg)
        cached = self._parsers.get(cfg.id or 0)
        if cached is not None and cached.config_revision == sig:
            cached.config_row = cfg  # refresh stake/trade_mode without rebuild
            return cached

        gap_seconds = cfg.aggregate_window_seconds or 120
        parser = build_parser(
            parser_type=cfg.parser_type,
            parser_config=cfg.parser_config_dict(),
            timezone_offset_minutes=cfg.timezone_offset_minutes,
            asset_aliases=cfg.asset_aliases(),
            known_assets=self._manager.assets,
            default_duration_seconds=cfg.default_duration_seconds,
            parser_id=f"cfg-{cfg.id}",
            gap_seconds=gap_seconds,
        )

        aggregator: Aggregator | None = None
        if (
            cfg.parser_type in ("template", "regex")
            and cfg.aggregate_window_seconds > 0
        ):
            aggregator = Aggregator(
                parser,
                window_seconds=cfg.aggregate_window_seconds,
            )

        cached = _CachedParser(
            config_revision=sig,
            parser_type=cfg.parser_type,
            parser=parser,
            aggregator=aggregator,
            config_row=cfg,
        )
        self._parsers[cfg.id or 0] = cached
        return cached

    @staticmethod
    def _dispatch_to_cached(
        cached: _CachedParser,
        message: RawMessage,
    ) -> Iterable[ParseOutcome]:
        """Per-type dispatch — the only place that branches on parser_type."""
        if cached.parser_type == "batch":
            assert isinstance(cached.parser, BatchParser)
            return cached.parser.parse_all([message])

        if cached.parser_type == "prep_trigger":
            assert isinstance(cached.parser, PrepTriggerParser)
            return [cached.parser.feed(message)]

        if cached.aggregator is not None:
            return [cached.aggregator.feed(message)]

        # Single-shot template/regex.
        return [cached.parser.parse([message])]
