"""Parser engine + router tests.

Three layers:
  1. ``test_normalisers``    — direction / duration / asset / fire_at
  2. ``test_template_regex`` — TemplateParser + RegexParser end-to-end
  3. ``test_aggregator``     — sliding-window multi-message buffering
  4. ``test_router``         — config CRUD + /parsers/test live tester

Everything runs in-process; no Telegram or broker mocking needed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from autotrader.services.parsers import (
    BUILTIN_TEMPLATES,
    Aggregator,
    BatchParser,
    ParsedSignal,
    ParseError,
    PrepTriggerParser,
    RawMessage,
    RegexParser,
    TemplateParser,
    build_parser,
    resolve_asset,
    template_to_regex,
)
from autotrader.services.parsers.factory import ParserBuildError
from autotrader.services.parsers.normalize import (
    normalise_asset,
    normalise_direction,
    normalise_duration,
    normalise_fire_at,
)

# ===========================================================================
# Normalisers
# ===========================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BUY", "call"),
        ("buy", "call"),
        ("UP", "call"),
        ("CALL", "call"),
        ("LONG", "call"),
        ("🟢", "call"),
        ("🟢 BUY", "call"),
        ("📈 LONG", "call"),
        ("SELL", "put"),
        ("DOWN", "put"),
        ("PUT", "put"),
        ("🔴", "put"),
        ("🔴 SELL", "put"),
        ("📉 SHORT", "put"),
        ("nothing", None),
        ("", None),
    ],
)
def test_normalise_direction(raw: str, expected: str | None) -> None:
    assert normalise_direction(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60", 3600),  # default unit minutes -> 60 minutes -> 3600s
        ("60s", 60),
        ("60 sec", 60),
        ("1m", 60),
        ("1 min", 60),
        ("1 minute", 60),
        ("5 mins", 300),
        ("M1", 60),
        ("M5", 300),
        ("M15", 900),
        ("2h", 7200),
        ("nope", None),
        ("", None),
    ],
)
def test_normalise_duration(raw: str, expected: int | None) -> None:
    assert normalise_duration(raw) == expected


def test_normalise_duration_default_unit_seconds() -> None:
    assert normalise_duration("60", default_unit="s") == 60


@pytest.mark.parametrize(
    ("raw", "aliases", "expected"),
    [
        ("EURUSD", None, "EURUSD"),
        ("EUR/USD", None, "EURUSD"),
        ("eur-usd", None, "EURUSD"),
        ("EUR USD", None, "EURUSD"),
        ("EUR/USD", {"EUR/USD": "EURUSD_otc"}, "EURUSD_otc"),
        # Case-insensitive alias lookup.
        ("eur/usd", {"EUR/USD": "EURUSD_otc"}, "EURUSD_otc"),
        ("Gold", {"GOLD": "XAUUSD"}, "XAUUSD"),
    ],
)
def test_normalise_asset(raw: str, aliases: dict[str, str] | None, expected: str) -> None:
    assert normalise_asset(raw, aliases) == expected


def test_normalise_fire_at_resolves_today() -> None:
    now = datetime(2025, 5, 7, 10, 0, tzinfo=UTC)
    out = normalise_fire_at("14:30", now=now, tz_offset_minutes=0)
    assert out == datetime(2025, 5, 7, 14, 30, tzinfo=UTC)


def test_normalise_fire_at_rolls_to_tomorrow() -> None:
    now = datetime(2025, 5, 7, 18, 0, tzinfo=UTC)
    out = normalise_fire_at("14:30", now=now, tz_offset_minutes=0)
    assert out == datetime(2025, 5, 8, 14, 30, tzinfo=UTC)


def test_normalise_fire_at_with_timezone_offset() -> None:
    # Channel is UTC+5; user types 14:30 (= 09:30 UTC).
    now = datetime(2025, 5, 7, 5, 0, tzinfo=UTC)  # 10:00 channel-local
    out = normalise_fire_at("14:30", now=now, tz_offset_minutes=300)
    assert out == datetime(2025, 5, 7, 9, 30, tzinfo=UTC)


def test_normalise_fire_at_bare_strings() -> None:
    now = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    assert normalise_fire_at("now", now=now, tz_offset_minutes=0) is None
    assert normalise_fire_at("asap", now=now, tz_offset_minutes=0) is None
    assert normalise_fire_at("garbage", now=now, tz_offset_minutes=0) is None


# ===========================================================================
# Asset resolver
# ===========================================================================


def test_resolve_asset_alias_overrides_everything() -> None:
    res = resolve_asset(
        "EUR/USD",
        aliases={"EUR/USD": "EURUSD_otc"},
        known_assets=("EURUSD",),
    )
    assert res.code == "EURUSD_otc"
    assert res.via == "alias"


def test_resolve_asset_exact_match_against_known() -> None:
    res = resolve_asset("EURUSD", known_assets=("EURUSD", "GBPUSD"))
    assert res.code == "EURUSD"
    assert res.via == "exact"


def test_resolve_asset_strips_then_matches() -> None:
    res = resolve_asset("EUR/USD", known_assets=("EURUSD",))
    assert res.code == "EURUSD"
    assert res.via == "exact"


def test_resolve_asset_otc_suffix_probe() -> None:
    res = resolve_asset("EUR/USD", known_assets=("EURUSD_otc",))
    assert res.code == "EURUSD_otc"
    assert res.via == "otc"


def test_resolve_asset_fallback_when_unknown() -> None:
    res = resolve_asset("EUR/USD", known_assets=("GBPUSD",))
    assert res.code == "EURUSD"
    assert res.via == "fallback"


def test_resolve_asset_no_known_set_falls_back() -> None:
    res = resolve_asset("EUR/USD", aliases=None, known_assets=None)
    assert res.code == "EURUSD"
    assert res.via == "fallback"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Trailing OTC token → ``<base>_otc`` regardless of catalogue.
        ("USD NGN OTC", "USDNGN_otc"),
        ("GOLD OTC", "GOLD_otc"),
        ("EUR/USD OTC", "EURUSD_otc"),
        ("USD-NGN-OTC", "USDNGN_otc"),
        ("USDJPY otc", "USDJPY_otc"),  # case-insensitive
        # No OTC token → just join the words.
        ("USD EUR", "USDEUR"),
        ("EUR USD", "EURUSD"),
        ("EUR/USD", "EURUSD"),
        ("EURUSD", "EURUSD"),
        # Sole "OTC" can't be a meaningful asset.
        ("OTC", "OTC"),
    ],
)
def test_resolve_asset_otc_token_handling(raw: str, expected: str) -> None:
    """OTC as a separate trailing word becomes the ``_otc`` suffix."""
    res = resolve_asset(raw)
    assert res.code == expected


def test_resolve_asset_otc_with_catalogue_match() -> None:
    """Catalogue lookup finds the OTC-suffixed code when channel says OTC."""
    res = resolve_asset(
        "USD NGN OTC",
        known_assets=("USDNGN_otc", "GBPUSD"),
    )
    assert res.code == "USDNGN_otc"
    assert res.via == "otc"


def test_resolve_asset_no_otc_marker_with_otc_only_catalogue() -> None:
    """Channel didn't say OTC but broker only has the OTC variant.

    Cross-probe still finds it (e.g. weekend trading where the spot
    market is closed but the OTC desk is open).
    """
    res = resolve_asset("EUR/USD", known_assets=("EURUSD_otc",))
    assert res.code == "EURUSD_otc"
    assert res.via == "otc"


# ===========================================================================
# Template + Regex parsers
# ===========================================================================


def _msg(text: str, *, ts: datetime | None = None) -> RawMessage:
    return RawMessage(
        text=text,
        chat_id=-1001,
        sender_id=42,
        received_at=ts or datetime(2025, 5, 7, 12, 0, tzinfo=UTC),
    )


def test_template_to_regex_includes_named_groups() -> None:
    pattern = template_to_regex("{DIRECTION} {ASSET} {DURATION}")
    assert "(?P<direction>" in pattern
    assert "(?P<asset>" in pattern
    assert "(?P<duration>" in pattern


def test_template_parser_simple_match() -> None:
    p = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    out = p.parse([_msg("BUY EURUSD 1m")])
    assert isinstance(out, ParsedSignal)
    assert out.asset == "EURUSD"
    assert out.direction == "call"
    assert out.duration_seconds == 60


def test_template_parser_emoji_direction_with_aliases() -> None:
    p = TemplateParser(
        "{DIRECTION} {ASSET} expiry {DURATION}",
        asset_aliases={"EUR/USD": "EURUSD_otc"},
    )
    out = p.parse([_msg("🟢 EUR/USD expiry 1m")])
    assert isinstance(out, ParsedSignal)
    assert out.asset == "EURUSD_otc"
    assert out.asset_via == "alias"
    assert out.direction == "call"
    assert out.duration_seconds == 60


def test_template_parser_auto_resolves_against_known_assets() -> None:
    """No manual alias — broker known-set picks ``_otc`` automatically."""
    p = TemplateParser(
        "{DIRECTION} {ASSET} {DURATION}",
        known_assets=("EURUSD_otc", "GBPUSD"),
    )
    out = p.parse([_msg("BUY EUR/USD 1m")])
    assert isinstance(out, ParsedSignal)
    assert out.asset == "EURUSD_otc"
    assert out.asset_via == "otc"
    assert out.asset_raw == "EUR/USD"


def test_template_parser_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        TemplateParser("{NONSENSE}")


def test_template_parser_no_match_returns_parse_error() -> None:
    p = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    out = p.parse([_msg("hello world")])
    assert isinstance(out, ParseError)
    assert "regex" in out.reason


def test_regex_parser_invalid_pattern_raises() -> None:
    with pytest.raises(ValueError, match="invalid regex"):
        RegexParser("(?P<direction>BUY)(?P<asset>(?P<duration>")  # bad syntax


def test_regex_parser_missing_required_group_raises() -> None:
    with pytest.raises(ValueError, match="missing required"):
        RegexParser(r"(?P<asset>EURUSD)")  # no direction


def test_regex_parser_with_stake_and_scheduled_time() -> None:
    pattern = (
        r"(?P<direction>BUY|SELL)\s+"
        r"(?P<asset>[A-Z/]+)\s+"
        r"(?P<duration>\d+m)\s+@\s*"
        r"(?P<fire_at>\d{1,2}:\d{2})\s+\$"
        r"(?P<stake>\d+(?:[.,]\d+)?)"
    )
    parser = RegexParser(pattern, default_duration_seconds=60, timezone_offset_minutes=0)
    now = datetime(2025, 5, 7, 10, 0, tzinfo=UTC)
    out = parser.parse([_msg("BUY EUR/USD 5m @ 14:30 $25.50", ts=now)])
    assert isinstance(out, ParsedSignal)
    assert out.asset == "EURUSD"
    assert out.direction == "call"
    assert out.duration_seconds == 300
    assert out.stake == 25.5
    assert out.fire_at == datetime(2025, 5, 7, 14, 30, tzinfo=UTC)


def test_regex_parser_invalid_direction_token() -> None:
    parser = RegexParser(r"(?P<direction>WAT)\s+(?P<asset>EUR)")
    out = parser.parse([_msg("WAT EUR")])
    assert isinstance(out, ParseError)
    assert "direction" in out.reason


def test_regex_parser_invalid_duration() -> None:
    parser = RegexParser(
        r"(?P<direction>BUY)\s+(?P<asset>EUR)\s+(?P<duration>\S+)",
    )
    out = parser.parse([_msg("BUY EUR foo")])
    assert isinstance(out, ParseError)
    assert "duration" in out.reason


def test_builtin_templates_all_compile() -> None:
    for entry in BUILTIN_TEMPLATES:
        TemplateParser(entry["template"])  # raises on invalid template


# ===========================================================================
# Aggregator
# ===========================================================================


def test_aggregator_emits_when_full_run_parses() -> None:
    parser = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    agg = Aggregator(parser, window_seconds=10)

    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)

    out1 = agg.feed(RawMessage("BUY", chat_id=1, sender_id=1, received_at=base))
    assert isinstance(out1, ParseError)

    out2 = agg.feed(
        RawMessage("EURUSD", chat_id=1, sender_id=1, received_at=base + timedelta(seconds=2)),
    )
    assert isinstance(out2, ParseError)

    out3 = agg.feed(
        RawMessage("1m", chat_id=1, sender_id=1, received_at=base + timedelta(seconds=4)),
    )
    assert isinstance(out3, ParsedSignal)
    assert out3.asset == "EURUSD"
    assert out3.direction == "call"

    # Buffer cleared after emission.
    assert agg.buffer_size() == 0


def test_aggregator_window_expiry_drops_buffer() -> None:
    parser = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    agg = Aggregator(parser, window_seconds=5)

    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    agg.feed(RawMessage("BUY", chat_id=1, sender_id=1, received_at=base))
    agg.feed(RawMessage("EURUSD", chat_id=1, sender_id=1, received_at=base + timedelta(seconds=1)))

    # Now jump past the window — adding a new unrelated message should
    # start a fresh buffer instead of completing the old one.
    out = agg.feed(
        RawMessage(
            "BUY GBPUSD 1m",
            chat_id=1,
            sender_id=1,
            received_at=base + timedelta(seconds=10),
        ),
    )
    assert isinstance(out, ParsedSignal)
    assert out.asset == "GBPUSD"


def test_aggregator_keeps_separate_buffers_per_sender() -> None:
    parser = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    agg = Aggregator(parser, window_seconds=10)

    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    agg.feed(RawMessage("BUY", chat_id=1, sender_id=1, received_at=base))
    out_other = agg.feed(
        RawMessage("BUY EURUSD 1m", chat_id=1, sender_id=2, received_at=base),
    )
    assert isinstance(out_other, ParsedSignal)
    # Sender 1's buffer is untouched.
    assert agg.buffer_size() == 1


def test_aggregator_invalid_window_rejected() -> None:
    parser = TemplateParser("{DIRECTION} {ASSET} {DURATION}")
    with pytest.raises(ValueError, match="window_seconds"):
        Aggregator(parser, window_seconds=0)


# ===========================================================================
# Prep + Trigger
# ===========================================================================


def _pt_parser(**overrides: object) -> PrepTriggerParser:
    base: dict[str, object] = {
        "prep": "PAIR\\s*:\\s*(?P<asset>[A-Z\\s/-]+?)\\s+TIME\\s*:\\s*(?P<duration>\\d+)\\s*Minute",
        "trigger": "(?P<direction>👍|👎)",
        "prep_kind": "regex",
        "trigger_kind": "regex",
        "gap_seconds": 120,
    }
    base.update(overrides)
    return PrepTriggerParser(**base)  # type: ignore[arg-type]


def test_prep_trigger_fires_on_trigger() -> None:
    parser = _pt_parser()
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)

    out = parser.feed(
        RawMessage(
            "PAIR: USD-NGN OTC TIME: 1 Minute",
            chat_id=-1001,
            sender_id=200,
            received_at=base,
        ),
    )
    assert isinstance(out, ParseError)
    assert "prep stored" in out.reason
    assert parser.pending_size() == 1

    out = parser.feed(
        RawMessage(
            "👍",
            chat_id=-1001,
            sender_id=200,
            received_at=base + timedelta(seconds=5),
        ),
    )
    assert isinstance(out, ParsedSignal)
    assert out.direction == "call"
    # OTC token detection: even without a broker catalogue, the
    # resolver's fallback produces the canonical ``<base>_otc`` form.
    assert out.asset == "USDNGN_otc"
    assert out.asset_via == "fallback"
    assert out.duration_seconds == 60
    assert parser.pending_size() == 0


def test_prep_trigger_trigger_alone_no_emit() -> None:
    parser = _pt_parser()
    out = parser.feed(
        RawMessage(
            "👎",
            chat_id=-1001,
            sender_id=200,
            received_at=datetime(2025, 5, 7, 12, 0, tzinfo=UTC),
        ),
    )
    assert isinstance(out, ParseError)
    assert "no recent prep" in out.reason


def test_prep_trigger_gap_timeout_drops_stale() -> None:
    parser = _pt_parser(gap_seconds=10)
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)

    parser.feed(
        RawMessage(
            "PAIR: GBP/USD TIME: 5 Minute",
            chat_id=-1001,
            sender_id=200,
            received_at=base,
        ),
    )
    # Trigger arrives well past the gap.
    out = parser.feed(
        RawMessage(
            "👍",
            chat_id=-1001,
            sender_id=200,
            received_at=base + timedelta(seconds=30),
        ),
    )
    assert isinstance(out, ParseError)
    assert "no recent prep" in out.reason


def test_prep_trigger_asset_auto_resolves() -> None:
    """Catalogue match honours the channel's OTC marker."""
    parser = _pt_parser(known_assets=("USDNGN_otc",))
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)

    parser.feed(
        RawMessage(
            "PAIR: USD-NGN OTC TIME: 1 Minute",
            chat_id=-1001,
            sender_id=200,
            received_at=base,
        ),
    )
    out = parser.feed(
        RawMessage("👍", chat_id=-1001, sender_id=200, received_at=base),
    )
    assert isinstance(out, ParsedSignal)
    assert out.asset == "USDNGN_otc"
    assert out.asset_via == "otc"


def test_prep_trigger_real_world_eliteclassbinary_format() -> None:
    """The exact format from the user's EliteClassBinary channel.

    Three samples — one with OTC and spaces, one with OTC and one
    word, one without OTC — all need to resolve cleanly even though
    the prep is across several lines and decorated with emojis.
    """
    # Regex-based prep that captures everything on the PAIR line
    # (up to end-of-line) and skips arbitrary noise before TIME.
    parser = PrepTriggerParser(
        prep=(
            r"PAIR\s*:\s*(?P<asset>[^\n]+?)\s*$"
            r"[\s\S]*?TIME\s*:\s*(?P<duration>\d+)\s*Minute"
        ),
        trigger=r"(?P<direction>👍|👎)",
        prep_kind="regex",
        trigger_kind="regex",
        gap_seconds=120,
    )

    cases = [
        (
            "🌐 PAIR: USD NGN OTC\n\n⏱️ TIME: 01 Minute\n\n🚨 IF LOSS TAKE 1 STEP MTG",
            "USDNGN_otc",
        ),
        (
            "🌐 PAIR:  GOLD  OTC \n\n⏱️ TIME:   01 Minute",  # extra whitespace
            "GOLD_otc",
        ),
        (
            "🌐 PAIR: USD EUR\n\n⏱️ TIME: 01 Minute\n\n🚨 IF LOSS TAKE 1 STEP MTG",
            "USDEUR",
        ),
    ]
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    for i, (prep_text, expected_asset) in enumerate(cases):
        chat = -1000 - i  # different chat_ids so cases don't share state
        parser.feed(
            RawMessage(prep_text, chat_id=chat, sender_id=200, received_at=base),
        )
        out = parser.feed(
            RawMessage("👍", chat_id=chat, sender_id=200, received_at=base),
        )
        assert isinstance(out, ParsedSignal), (
            f"case {i} ({expected_asset!r}) didn't fire: {out}"
        )
        assert out.asset == expected_asset, f"case {i}: got {out.asset!r}"
        assert out.duration_seconds == 60
        assert out.direction == "call"


def test_prep_trigger_eliteclassbinary_template() -> None:
    """The same channel via the loosened {ASSET} template placeholder.

    Works only when the format is well-behaved (no emoji-prefixed
    TIME line) — for the messy real-world case use the regex.
    """
    parser = PrepTriggerParser(
        prep="PAIR: {ASSET} TIME: {DURATION} Minute",
        trigger="{DIRECTION}",
        prep_kind="template",
        trigger_kind="template",
        gap_seconds=120,
    )
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    parser.feed(
        RawMessage(
            "PAIR: USD NGN OTC TIME: 1 Minute",
            chat_id=-1001,
            sender_id=200,
            received_at=base,
        ),
    )
    out = parser.feed(
        RawMessage("👍", chat_id=-1001, sender_id=200, received_at=base),
    )
    assert isinstance(out, ParsedSignal)
    assert out.asset == "USDNGN_otc"
    assert out.duration_seconds == 60


def test_prep_trigger_template_kind() -> None:
    parser = _pt_parser(
        prep="PAIR: {ASSET} TIME: {DURATION} Minute",
        trigger="{DIRECTION}",
        prep_kind="template",
        trigger_kind="template",
    )
    base = datetime(2025, 5, 7, 12, 0, tzinfo=UTC)
    parser.feed(
        RawMessage(
            "PAIR: GBPUSD TIME: 1 Minute",
            chat_id=-1001,
            sender_id=200,
            received_at=base,
        ),
    )
    out = parser.feed(RawMessage("👎", chat_id=-1001, sender_id=200, received_at=base))
    assert isinstance(out, ParsedSignal)
    assert out.direction == "put"
    assert out.asset == "GBPUSD"


def test_prep_trigger_validates_required_groups() -> None:
    with pytest.raises(ValueError, match="asset"):
        PrepTriggerParser(
            prep=r"hello",  # no asset group
            trigger=r"(?P<direction>👍)",
            prep_kind="regex",
            trigger_kind="regex",
        )
    with pytest.raises(ValueError, match="direction"):
        PrepTriggerParser(
            prep=r"(?P<asset>EUR)",
            trigger=r"hello",  # no direction group
            prep_kind="regex",
            trigger_kind="regex",
        )


def test_prep_trigger_via_test_endpoint(client: TestClient) -> None:
    """End-to-end: endpoint feeds messages sequentially through the parser."""
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "prep_trigger",
                "parser_config": {
                    "prep": "PAIR: {ASSET} TIME: {DURATION} Minute",
                    "prep_kind": "template",
                    "trigger": "{DIRECTION}",
                    "trigger_kind": "template",
                },
                "aggregate_window_seconds": 120,
            },
            "messages": [
                # Hyphenated asset fits {ASSET}'s single-token shape.
                {"text": "PAIR: USDNGN-OTC TIME: 1 Minute"},
                {"text": "👍"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True
    sig = body["signal"]
    assert sig["direction"] == "call"
    assert sig["duration_seconds"] == 60


# ===========================================================================
# Batch (one message → many signals)
# ===========================================================================


_BATCH_MESSAGE = """\
DATE: 07.05.2026
TIMEZONE : UTC/GMT (+06:00)
FUTURE SIGNALS 🕯
📏📏📏📏📏📏📏📏📏📏

01:51 USDBDT-OTC PUT
01:53 USDBDT-OTC PUT
01:55 USDBDT-OTC PUT
01:58 USDBDT-OTC CALL


📏📏📏📏📏📏📏📏📏📏

➗➗➗➗➗➗➗
CALL MEAN UP🔼
PUT MEAN DOWN🔽
"""


def _batch_parser(**overrides: object) -> BatchParser:
    base: dict[str, object] = {
        "row_pattern": (
            r"^(?P<time>\d{1,2}:\d{2})\s+"
            r"(?P<asset>\S+)\s+"
            r"(?P<direction>CALL|PUT)\s*$"
        ),
        "header_pattern": (
            r"DATE\s*:\s*(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4}).*?"
            r"TIMEZONE\s*:\s*UTC/GMT\s*\((?P<tz_offset>[+-]\d{1,2}(?::?\d{2})?)\)"
        ),
        "row_kind": "regex",
        "header_kind": "regex",
    }
    base.update(overrides)
    return BatchParser(**base)  # type: ignore[arg-type]


def test_batch_parses_all_rows_with_header() -> None:
    """The exact format from the user's USDBDT scheduled-batch channel."""
    parser = _batch_parser()
    msgs = [RawMessage(_BATCH_MESSAGE, chat_id=-1, sender_id=1)]

    outs = parser.parse_all(msgs)
    assert len(outs) == 4

    sigs = [o for o in outs if isinstance(o, ParsedSignal)]
    assert len(sigs) == 4

    # All rows resolve to the same asset (no broker catalogue → fallback
    # form is "USDBDT_otc" because resolver detects trailing OTC).
    assert all(s.asset == "USDBDT_otc" for s in sigs)
    # Direction in row order: PUT, PUT, PUT, CALL.
    assert [s.direction for s in sigs] == ["put", "put", "put", "call"]

    # fire_at: 01:51 channel-local in UTC+6 → 19:51 UTC the previous day.
    expected_first = datetime(2026, 5, 6, 19, 51, tzinfo=UTC)
    assert sigs[0].fire_at == expected_first
    expected_last = datetime(2026, 5, 6, 19, 58, tzinfo=UTC)
    assert sigs[3].fire_at == expected_last


def test_batch_no_header_uses_today_in_channel_tz() -> None:
    """Without a header, schedule each row for today in the channel TZ."""
    parser = _batch_parser(
        header_pattern=None,
        timezone_offset_minutes=0,
    )
    received = datetime(2026, 5, 7, 12, 0, tzinfo=UTC)
    msg = RawMessage(
        "01:55 EURUSD CALL\n01:57 EURUSD PUT",
        chat_id=-1,
        sender_id=1,
        received_at=received,
    )
    outs = parser.parse_all([msg])
    sigs = [o for o in outs if isinstance(o, ParsedSignal)]
    assert len(sigs) == 2
    assert sigs[0].fire_at == datetime(2026, 5, 7, 1, 55, tzinfo=UTC)
    assert sigs[1].fire_at == datetime(2026, 5, 7, 1, 57, tzinfo=UTC)


def test_batch_finditer_skips_non_matching_rows() -> None:
    """Rows the regex doesn't match (e.g. unknown direction) are dropped."""
    parser = _batch_parser(header_pattern=None, timezone_offset_minutes=0)
    msg = RawMessage(
        "01:55 EURUSD CALL\n01:57 EURUSD WAT\n01:59 EURUSD PUT",
        chat_id=-1,
        sender_id=1,
        received_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )
    outs = parser.parse_all([msg])
    # CALL|PUT alternation excludes "WAT" → only the two valid rows
    # survive finditer.
    sigs = [o for o in outs if isinstance(o, ParsedSignal)]
    assert len(sigs) == 2
    assert [s.direction for s in sigs] == ["call", "put"]


def test_batch_per_row_error_on_invalid_time() -> None:
    """A row that finditer captures but is internally bad → ParseError row."""
    parser = BatchParser(
        # Permissive row regex — time isn't validated by the row regex
        # itself, so a bad time reaches the per-row builder.
        row_pattern=(
            r"^(?P<time>\d{1,2}:\d{2})\s+"
            r"(?P<asset>\S+)\s+"
            r"(?P<direction>CALL|PUT)\s*$"
        ),
        row_kind="regex",
    )
    msg = RawMessage(
        "25:99 EURUSD CALL",
        chat_id=-1,
        sender_id=1,
        received_at=datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )
    outs = parser.parse_all([msg])
    err_count = sum(1 for o in outs if isinstance(o, ParseError))
    assert err_count >= 1


def test_batch_asset_auto_resolution() -> None:
    """Catalogue-aware resolution applies per row."""
    parser = _batch_parser(known_assets=("USDBDT_otc", "EURUSD"))
    msgs = [RawMessage(_BATCH_MESSAGE, chat_id=-1, sender_id=1)]
    sigs = [o for o in parser.parse_all(msgs) if isinstance(o, ParsedSignal)]
    assert all(s.asset == "USDBDT_otc" for s in sigs)
    # Channel said OTC, catalogue has the OTC form → "otc" via.
    assert all(s.asset_via == "otc" for s in sigs)


def test_batch_required_row_groups() -> None:
    with pytest.raises(ValueError, match="time"):
        BatchParser(
            row_pattern=r"(?P<asset>\S+)\s+(?P<direction>CALL)",
            row_kind="regex",
        )


def test_batch_via_test_endpoint(client: TestClient) -> None:
    """End-to-end: /parsers/test returns ``signals`` list with 4 entries."""
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "batch",
                "parser_config": {
                    "row": (
                        r"^(?P<time>\d{1,2}:\d{2})\s+"
                        r"(?P<asset>\S+)\s+"
                        r"(?P<direction>CALL|PUT)\s*$"
                    ),
                    "row_kind": "regex",
                    "header": (
                        r"DATE\s*:\s*(?P<date>\d{1,2}[./-]\d{1,2}[./-]\d{2,4}).*?"
                        r"TIMEZONE\s*:\s*UTC/GMT\s*\((?P<tz_offset>[+-]\d{1,2}(?::?\d{2})?)\)"
                    ),
                    "header_kind": "regex",
                },
                "trade_mode": "scheduled",
            },
            "messages": [{"text": _BATCH_MESSAGE}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True
    assert len(body["signals"]) == 4
    first = body["signal"]
    assert first["direction"] == "put"
    assert first["asset"] == "USDBDT_otc"
    assert first["fire_at"] is not None


# ===========================================================================
# Factory
# ===========================================================================


def test_build_parser_template() -> None:
    parser = build_parser(
        parser_type="template",
        parser_config={"template": "{DIRECTION} {ASSET} {DURATION}"},
    )
    assert isinstance(parser, TemplateParser)


def test_build_parser_regex() -> None:
    parser = build_parser(
        parser_type="regex",
        parser_config={
            "pattern": r"(?P<direction>BUY)\s+(?P<asset>EUR)",
        },
    )
    assert isinstance(parser, RegexParser)


def test_build_parser_unknown_type_raises() -> None:
    with pytest.raises(ParserBuildError, match="unknown parser_type"):
        build_parser(parser_type="llm", parser_config={})


def test_build_parser_missing_template_raises() -> None:
    with pytest.raises(ParserBuildError, match="non-empty"):
        build_parser(parser_type="template", parser_config={})


# ===========================================================================
# Router (live tester + config CRUD)
# ===========================================================================


@pytest.fixture
def client() -> Iterator[TestClient]:
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.parser_config import ParserConfig  # noqa: PLC0415

    with TestClient(app) as c:
        yield c

    from sqlmodel import delete  # noqa: PLC0415

    async def _wipe() -> None:
        async with AsyncSessionLocal() as s:
            await s.exec(delete(ParserConfig))  # type: ignore[call-overload]
            await s.commit()
        await engine.dispose()

    asyncio.new_event_loop().run_until_complete(_wipe())


def _login(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"passcode": "test-passcode"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_templates_endpoint(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/parsers/templates", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert any(t["id"] == "direction-asset-duration" for t in body)


def test_test_endpoint_template_match(client: TestClient) -> None:
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
            },
            "messages": [{"text": "BUY EURUSD 1m"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True
    assert body["signal"]["asset"] == "EURUSD"
    assert body["signal"]["direction"] == "call"
    assert body["signal"]["duration_seconds"] == 60


def test_test_endpoint_no_match(client: TestClient) -> None:
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
            },
            "messages": [{"text": "hello world"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["error"] is not None


def test_test_endpoint_aggregator_match(client: TestClient) -> None:
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
                "aggregate_window_seconds": 10,
            },
            "messages": [
                {"text": "BUY"},
                {"text": "EURUSD"},
                {"text": "1m"},
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["matched"] is True


def test_test_endpoint_invalid_regex_returns_error(client: TestClient) -> None:
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "regex",
                "parser_config": {"pattern": "((((("},
            },
            "messages": [{"text": "ignored"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert "regex" in body["error"].lower() or "invalid" in body["error"].lower()


def _new_config_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "chat_id": -1001,
        "name": "default",
        "priority": 100,
        "parser_type": "template",
        "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
        "timezone": "UTC",
        "timezone_offset_minutes": 0,
        "asset_aliases": {"EUR/USD": "EURUSD_otc"},
        "default_stake": 25,
        "default_duration_seconds": 60,
        "trade_mode": "auto",
        "aggregate_window_seconds": 0,
        "martingale": {
            "enabled": False,
            "multiplier": 2.0,
            "max_streak": 5,
            "reset_on_win": True,
        },
        "enabled": True,
    }
    body.update(overrides)
    return body


def test_config_crud(client: TestClient) -> None:
    headers = _login(client)

    # No configs yet.
    r = client.get("/parsers/configs", headers=headers)
    assert r.status_code == 200
    assert r.json() == []

    # Create.
    r = client.post(
        "/parsers/configs",
        headers=headers,
        json=_new_config_body(),
    )
    assert r.status_code == 201
    created = r.json()
    config_id = created["id"]
    assert created["chat_id"] == -1001
    assert created["name"] == "default"
    assert created["asset_aliases"] == {"EUR/USD": "EURUSD_otc"}
    assert created["martingale"]["enabled"] is False

    # Get by id.
    r = client.get(f"/parsers/configs/{config_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == config_id

    # Update — turn martingale on, change name.
    r = client.put(
        f"/parsers/configs/{config_id}",
        headers=headers,
        json=_new_config_body(
            name="aggressive",
            martingale={
                "enabled": True,
                "multiplier": 2.5,
                "max_streak": 3,
                "reset_on_win": True,
            },
        ),
    )
    assert r.status_code == 200
    updated = r.json()
    assert updated["name"] == "aggressive"
    assert updated["martingale"]["enabled"] is True
    assert updated["martingale"]["multiplier"] == 2.5

    # Filter list by chat_id.
    r = client.get("/parsers/configs?chat_id=-1001", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Delete.
    r = client.delete(f"/parsers/configs/{config_id}", headers=headers)
    assert r.status_code == 200
    r = client.get(f"/parsers/configs/{config_id}", headers=headers)
    assert r.status_code == 404


def test_multiple_configs_per_chat(client: TestClient) -> None:
    """A single chat hosts several priority-ordered parsers."""
    headers = _login(client)

    r = client.post(
        "/parsers/configs",
        headers=headers,
        json=_new_config_body(name="primary", priority=10),
    )
    assert r.status_code == 201
    primary_id = r.json()["id"]

    r = client.post(
        "/parsers/configs",
        headers=headers,
        json=_new_config_body(
            name="fallback",
            priority=200,
            parser_config={"template": "{ASSET} {DIRECTION} {DURATION}"},
        ),
    )
    assert r.status_code == 201
    fallback_id = r.json()["id"]
    assert fallback_id != primary_id

    r = client.get("/parsers/configs?chat_id=-1001", headers=headers)
    body = r.json()
    assert [c["name"] for c in body] == ["primary", "fallback"]


def test_config_create_validates_pattern(client: TestClient) -> None:
    headers = _login(client)
    r = client.post(
        "/parsers/configs",
        headers=headers,
        json=_new_config_body(parser_type="regex", parser_config={"pattern": "((((("}),
    )
    assert r.status_code == 400
    assert "regex" in r.json()["detail"].lower() or "invalid" in r.json()["detail"].lower()


def test_update_unknown_id_returns_404(client: TestClient) -> None:
    headers = _login(client)
    r = client.put(
        "/parsers/configs/9999",
        headers=headers,
        json={
            "name": "x",
            "priority": 100,
            "parser_type": "template",
            "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
            "timezone": "UTC",
            "timezone_offset_minutes": 0,
            "asset_aliases": {},
            "aggregate_window_seconds": 0,
            "default_stake": 1,
            "default_duration_seconds": 60,
            "trade_mode": "auto",
            "martingale": {
                "enabled": False,
                "multiplier": 2.0,
                "max_streak": 5,
                "reset_on_win": True,
            },
            "enabled": True,
        },
    )
    assert r.status_code == 404


def test_test_endpoint_scheduled_pin_rejects_live_signal(client: TestClient) -> None:
    """trade_mode=scheduled rejects a parser output that has no fire_at."""
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION}"},
                "trade_mode": "scheduled",
            },
            "messages": [{"text": "BUY EURUSD 1m"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert "scheduled" in body["error"]


def test_test_endpoint_live_pin_strips_fire_at(client: TestClient) -> None:
    """trade_mode=live forces fire_at=None even if the parser extracted one."""
    headers = _login(client)
    r = client.post(
        "/parsers/test",
        headers=headers,
        json={
            "config": {
                "parser_type": "template",
                "parser_config": {"template": "{DIRECTION} {ASSET} {DURATION} at {TIME}"},
                "trade_mode": "live",
            },
            "messages": [{"text": "BUY EURUSD 1m at 14:30"}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["signal"]["fire_at"] is None
    assert body["signal"]["trade_mode"] == "live"


def test_config_endpoints_require_auth(client: TestClient) -> None:
    r = client.get("/parsers/configs")
    assert r.status_code == 401
    r = client.post("/parsers/test", json={"config": {}, "messages": [{"text": "x"}]})
    assert r.status_code == 401
