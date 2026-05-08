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
from collections.abc import Iterable
from dataclasses import dataclass

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from autotrader.db import AsyncSessionLocal
from autotrader.models.parser_config import (
    ParserConfig,
)
from autotrader.models.parser_config import (
    list_configs as _list_configs,
)
from autotrader.models.settings import GlobalSettings
from autotrader.services.executor import TradeExecutor
from autotrader.services.parsers import (
    Aggregator,
    BatchParser,
    ParsedSignal,
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

    def __init__(self, *, manager: QuotexManager, executor: TradeExecutor) -> None:
        self._manager = manager
        self._executor = executor
        self._parsers: dict[int, _CachedParser] = {}
        # Serialise dispatch *per chat* so a flurry of messages from the
        # same channel doesn't race the stateful parsers (Aggregator,
        # PrepTriggerParser).
        self._chat_locks: dict[int, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate(self, config_id: int) -> None:
        self._parsers.pop(config_id, None)

    def invalidate_all(self) -> None:
        self._parsers.clear()

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
            configs = await self._enabled_configs_for(session, message.chat_id)
            settings = await self._get_settings(session)

        if not configs:
            return

        # Bail early when the master switch is off — saves us from
        # building parsers and walking the priority list.
        if not settings.pipeline_active:
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
                continue

            outcomes = self._dispatch_to_cached(cached, message)
            signals = [o for o in outcomes if isinstance(o, ParsedSignal)]
            if not signals:
                # First-match-wins applies *between* configs at this
                # chat, but the same parser can yield no signal for one
                # message and then fire on the next (e.g. prep+trigger).
                # So we just continue down the priority list.
                continue

            log.info(
                "pipeline.matched",
                config_id=cfg.id,
                name=cfg.name,
                signals=len(signals),
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
            # First matching config wins; lower-priority configs don't
            # also fire on the same message.
            return

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
