"""Build a configured :class:`Parser` from a per-channel config row."""

from __future__ import annotations

from autotrader.services.parsers.base import Parser
from autotrader.services.parsers.regex_parser import RegexParser
from autotrader.services.parsers.template import TemplateParser

ParserType = str  # "template" | "regex"


class ParserBuildError(ValueError):
    """Raised when a config can't be turned into a parser (bad regex, etc.)."""


def build_parser(
    *,
    parser_type: ParserType,
    parser_config: dict[str, str | int | float | bool],
    timezone_offset_minutes: int = 0,
    asset_aliases: dict[str, str] | None = None,
    default_duration_seconds: int = 60,
    parser_id: str | None = None,
) -> Parser:
    """Construct a parser from a structured config.

    ``parser_config`` shape depends on ``parser_type``:

    * ``"template"``  → ``{"template": "{DIRECTION} {ASSET} {DURATION}"}``
    * ``"regex"``     → ``{"pattern": "..."}`` (Python regex with named groups)

    Both types also accept ``default_duration_unit`` (``"s"`` / ``"m"`` /
    ``"h"``) — useful when channels post bare numbers like ``"60"``.
    """
    default_unit = str(parser_config.get("default_duration_unit", "m"))

    common: dict[str, object] = {
        "timezone_offset_minutes": timezone_offset_minutes,
        "asset_aliases": asset_aliases,
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
    except ValueError as exc:
        # ValueError is what TemplateParser / RegexParser throw for bad
        # input — wrap for the caller's convenience.
        raise ParserBuildError(str(exc)) from exc

    msg = f"unknown parser_type {parser_type!r}; expected 'template' or 'regex'"
    raise ParserBuildError(msg)
