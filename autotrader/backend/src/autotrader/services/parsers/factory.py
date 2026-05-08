"""Build a configured :class:`Parser` from a per-channel config row."""

from __future__ import annotations

from autotrader.services.parsers.base import Parser
from autotrader.services.parsers.batch import BatchParser
from autotrader.services.parsers.prep_trigger import PrepTriggerParser
from autotrader.services.parsers.regex_parser import RegexParser
from autotrader.services.parsers.template import TemplateParser

ParserType = str  # "template" | "regex" | "prep_trigger" | "batch"


class ParserBuildError(ValueError):
    """Raised when a config can't be turned into a parser (bad regex, etc.)."""


def build_parser(  # noqa: PLR0912, PLR0915  (linear dispatch, one branch per parser type)
    *,
    parser_type: ParserType,
    parser_config: dict[str, str | int | float | bool],
    timezone_offset_minutes: int = 0,
    asset_aliases: dict[str, str] | None = None,
    known_assets: tuple[str, ...] | None = None,
    default_duration_seconds: int = 60,
    parser_id: str | None = None,
    gap_seconds: int = 120,
) -> Parser:
    """Construct a parser from a structured config.

    ``parser_config`` shape depends on ``parser_type``:

    * ``"template"``      → ``{"template": "{DIRECTION} {ASSET} {DURATION}"}``
    * ``"regex"``         → ``{"pattern": "..."}`` (Python regex with named groups)
    * ``"prep_trigger"``  → ``{"prep_kind": "template"|"regex",
                                "prep": "...",
                                "trigger_kind": "template"|"regex",
                                "trigger": "..."}``
                            Two-phase parser — fires immediately when
                            the trigger arrives. ``gap_seconds`` is
                            the prep-to-trigger timeout (typically
                            sourced from ``aggregate_window_seconds``
                            or its default).

    ``known_assets`` is the broker's asset catalogue — the parser will
    auto-resolve channel-side names against it (with ``_otc`` suffix
    probing) before falling back to the stripped form.

    Both single-phase types also accept ``default_duration_unit``
    (``"s"`` / ``"m"`` / ``"h"``) — useful when channels post bare
    numbers like ``"60"``.
    """
    default_unit = str(parser_config.get("default_duration_unit", "m"))

    common: dict[str, object] = {
        "timezone_offset_minutes": timezone_offset_minutes,
        "asset_aliases": asset_aliases,
        "known_assets": known_assets,
        "default_duration_seconds": default_duration_seconds,
        "default_duration_unit": default_unit,
    }
    if parser_id:
        common["parser_id"] = parser_id

    try:
        if parser_type == "template":
            template = parser_config.get("template")
            if not isinstance(template, str) or not template.strip():
                msg = "template parser requires a non-empty 'template' string"
                raise ParserBuildError(msg)
            return TemplateParser(template=template, **common)  # type: ignore[arg-type]

        if parser_type == "regex":
            pattern = parser_config.get("pattern")
            if not isinstance(pattern, str) or not pattern.strip():
                msg = "regex parser requires a non-empty 'pattern' string"
                raise ParserBuildError(msg)
            return RegexParser(pattern=pattern, **common)  # type: ignore[arg-type]

        if parser_type == "batch":
            row = parser_config.get("row")
            if not isinstance(row, str) or not row.strip():
                msg = "batch parser requires a non-empty 'row' string"
                raise ParserBuildError(msg)
            row_kind = str(parser_config.get("row_kind", "regex"))
            if row_kind not in ("template", "regex"):
                msg = f"row_kind must be 'template' or 'regex', got {row_kind!r}"
                raise ParserBuildError(msg)

            header = parser_config.get("header")
            header_kind = str(parser_config.get("header_kind", "regex"))
            if header is not None and not isinstance(header, str):
                msg = "batch parser 'header' must be a string"
                raise ParserBuildError(msg)
            if header and header_kind not in ("template", "regex"):
                msg = f"header_kind must be 'template' or 'regex', got {header_kind!r}"
                raise ParserBuildError(msg)

            kwargs: dict[str, object] = {
                k: v for k, v in common.items() if k != "default_duration_unit"
            }
            kwargs["default_duration_unit"] = default_unit
            return BatchParser(
                row_pattern=row,
                header_pattern=str(header) if header else None,
                row_kind=row_kind,
                header_kind=header_kind,
                **kwargs,  # type: ignore[arg-type]
            )

        if parser_type == "prep_trigger":
            prep = parser_config.get("prep")
            trigger = parser_config.get("trigger")
            if not isinstance(prep, str) or not prep.strip():
                msg = "prep_trigger parser requires a non-empty 'prep' string"
                raise ParserBuildError(msg)
            if not isinstance(trigger, str) or not trigger.strip():
                msg = "prep_trigger parser requires a non-empty 'trigger' string"
                raise ParserBuildError(msg)
            prep_kind = str(parser_config.get("prep_kind", "template"))
            trigger_kind = str(parser_config.get("trigger_kind", "template"))
            if prep_kind not in ("template", "regex"):
                msg = f"prep_kind must be 'template' or 'regex', got {prep_kind!r}"
                raise ParserBuildError(msg)
            if trigger_kind not in ("template", "regex"):
                msg = f"trigger_kind must be 'template' or 'regex', got {trigger_kind!r}"
                raise ParserBuildError(msg)
            # PrepTriggerParser doesn't take default_duration_unit.
            kwargs: dict[str, object] = {
                k: v for k, v in common.items() if k != "default_duration_unit"
            }
            kwargs["default_duration_unit"] = default_unit
            kwargs["gap_seconds"] = gap_seconds
            return PrepTriggerParser(
                prep=prep,
                trigger=trigger,
                prep_kind=prep_kind,  # type: ignore[arg-type]
                trigger_kind=trigger_kind,  # type: ignore[arg-type]
                **kwargs,  # type: ignore[arg-type]
            )
    except ValueError as exc:
        # ValueError is what TemplateParser / RegexParser /
        # PrepTriggerParser throw for bad input — wrap for the caller.
        raise ParserBuildError(str(exc)) from exc

    msg = (
        f"unknown parser_type {parser_type!r}; "
        "expected 'template', 'regex', 'prep_trigger', or 'batch'"
    )
    raise ParserBuildError(msg)
