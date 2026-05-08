"""Signal parser plug-ins.

Public surface:

* :class:`Parser`, :class:`RawMessage`, :class:`ParsedSignal`,
  :class:`ParseError` (``base``) — the contract every parser implements.
* :class:`TemplateParser`, :class:`RegexParser`, :class:`Aggregator` —
  the three concrete parsers.
* :func:`build_parser` (``factory``) — constructs a parser from a
  per-channel config dict.
"""

from autotrader.services.parsers.aggregator import Aggregator, parse_via_aggregator
from autotrader.services.parsers.asset_resolver import AssetResolution, resolve_asset
from autotrader.services.parsers.base import (
    ParsedSignal,
    ParseError,
    ParseOutcome,
    Parser,
    RawMessage,
)
from autotrader.services.parsers.factory import ParserBuildError, build_parser
from autotrader.services.parsers.prep_trigger import (
    PrepTriggerParser,
    parse_via_prep_trigger,
)
from autotrader.services.parsers.regex_parser import RegexParser
from autotrader.services.parsers.template import (
    BUILTIN_TEMPLATES,
    TemplateParser,
    template_to_regex,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "Aggregator",
    "AssetResolution",
    "ParseError",
    "ParseOutcome",
    "ParsedSignal",
    "Parser",
    "ParserBuildError",
    "PrepTriggerParser",
    "RawMessage",
    "RegexParser",
    "TemplateParser",
    "build_parser",
    "parse_via_aggregator",
    "parse_via_prep_trigger",
    "resolve_asset",
    "template_to_regex",
]
