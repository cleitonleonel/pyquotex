"""Template parser — picker-style for non-technical users.

A template is a string with bracketed placeholders, e.g.::

    {DIRECTION} {ASSET} {DURATION}

We compile the template into a regex by substituting each placeholder
with a permissive named-group pattern, then delegate to
:class:`RegexParser` so the parsing semantics stay identical.

Known placeholders:

    {ASSET}      - asset code, e.g. "EURUSD" or "EUR/USD"
    {DIRECTION}  - BUY/SELL/UP/DOWN/CALL/PUT/emoji
    {DURATION}   - "1m", "60s", "M5", bare number, etc.
    {TIME}       - HH:MM in the channel timezone (scheduled trade)
    {STAKE}      - numeric stake override

Anything else in the template is treated as literal text (regex-escaped).
"""

from __future__ import annotations

import re
from typing import Final

from autotrader.services.parsers.regex_parser import RegexParser

# Each placeholder maps to a regex fragment. We compile the user's
# template by literal-escaping it and then replacing each placeholder
# token with the corresponding (named-group) pattern.
_DURATION_PATTERN = (
    r"[Mm]?\d+\s*(?:s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hr?s?|hour?s?)?"
)

_PLACEHOLDERS: Final[dict[str, tuple[str, str]]] = {
    # token -> (regex group name, pattern)
    "{ASSET}":     ("asset",     r"[A-Za-z][A-Za-z0-9]*(?:[/_-][A-Za-z0-9]+)?"),
    "{DIRECTION}": ("direction", r"[A-Za-z]+|[\U0001F300-\U0001FAFF]"),
    "{DURATION}":  ("duration",  _DURATION_PATTERN),
    "{TIME}":      ("fire_at",   r"\d{1,2}:\d{2}(?::\d{2})?"),
    "{STAKE}":     ("stake",     r"\d+(?:[.,]\d+)?"),
}

_PLACEHOLDER_FINDER: Final = re.compile(r"\{[A-Z]+\}")


def _compile_template_to_regex(template: str) -> str:
    """Return a regex string equivalent to ``template``.

    Whitespace in the template matches any run of whitespace; literal
    text is regex-escaped so the user can't accidentally inject
    metacharacters. Unknown placeholders raise ``ValueError`` up-front
    so the live tester can flag the mistake.
    """
    out: list[str] = []
    last = 0
    for match in _PLACEHOLDER_FINDER.finditer(template):
        # Literal chunk before this placeholder.
        chunk = template[last:match.start()]
        out.append(_escape_with_loose_whitespace(chunk))
        token = match.group(0)
        if token not in _PLACEHOLDERS:
            msg = f"unknown placeholder {token!r}; valid: {', '.join(_PLACEHOLDERS)}"
            raise ValueError(msg)
        group_name, pattern = _PLACEHOLDERS[token]
        out.append(f"(?P<{group_name}>{pattern})")
        last = match.end()
    out.append(_escape_with_loose_whitespace(template[last:]))
    return "".join(out)


def _escape_with_loose_whitespace(s: str) -> str:
    """Escape ``s`` for regex; any run of whitespace becomes ``\\s+``."""
    parts = re.split(r"(\s+)", s)
    return "".join(r"\s+" if part.isspace() else re.escape(part) for part in parts)


def template_to_regex(template: str) -> str:
    """Public helper — useful for the live-tester UI."""
    return _compile_template_to_regex(template)


class TemplateParser(RegexParser):
    """Wrapper around :class:`RegexParser` that compiles a friendly template."""

    def __init__(
        self,
        template: str,
        *,
        timezone_offset_minutes: int = 0,
        asset_aliases: dict[str, str] | None = None,
        default_duration_seconds: int = 60,
        default_duration_unit: str = "m",
        parser_id: str = "template",
    ) -> None:
        pattern = _compile_template_to_regex(template)
        super().__init__(
            pattern,
            timezone_offset_minutes=timezone_offset_minutes,
            asset_aliases=asset_aliases,
            default_duration_seconds=default_duration_seconds,
            default_duration_unit=default_duration_unit,
            parser_id=parser_id,
        )
        self._template = template

    @property
    def template(self) -> str:
        return self._template


# Pre-baked starter templates the dashboard can offer as click-to-pick.
BUILTIN_TEMPLATES: Final[list[dict[str, str]]] = [
    {
        "id": "direction-asset-duration",
        "label": "DIRECTION ASSET DURATION",
        "template": "{DIRECTION} {ASSET} {DURATION}",
        "example": "BUY EURUSD 1m",
    },
    {
        "id": "asset-direction-duration",
        "label": "ASSET DIRECTION DURATION",
        "template": "{ASSET} {DIRECTION} {DURATION}",
        "example": "EURUSD BUY 1m",
    },
    {
        "id": "emoji-direction",
        "label": "Emoji + ASSET + DURATION",
        "template": "{DIRECTION} {ASSET} expiry {DURATION}",
        "example": "🟢 EUR/USD expiry 1m",
    },
    {
        "id": "scheduled",
        "label": "Scheduled trade at TIME",
        "template": "{DIRECTION} {ASSET} {DURATION} at {TIME}",
        "example": "BUY EURUSD 1m at 14:30",
    },
    {
        "id": "with-stake",
        "label": "DIRECTION ASSET DURATION STAKE",
        "template": "{DIRECTION} {ASSET} {DURATION} ${STAKE}",
        "example": "SELL GBPUSD 5m $25",
    },
]
