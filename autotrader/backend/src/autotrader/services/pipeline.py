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
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

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

    async def _dispatch_locked(self, message: RawMessage) -> None:
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
                await self._executor.submit(
                    signal=sig,
                    parser_config=cached.config_row,
                    settings=fresh,
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
