"""Regression tests for configuration parsing."""

from __future__ import annotations


def test_empty_telegram_api_id_falls_back_to_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """docker-compose passes ``TELEGRAM_API_ID=""`` when unset.

    With ``env_ignore_empty=True`` on TelegramSettings, that should
    fall back to the model default of ``None`` instead of crashing the
    int parser.
    """
    monkeypatch.setenv("TELEGRAM_API_ID", "")
    monkeypatch.setenv("TELEGRAM_API_HASH", "")

    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.api_id is None
    assert s.api_hash is None


def test_empty_autotrader_log_level_falls_back_to_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Same idea for the main Settings — empty == use default."""
    monkeypatch.setenv("AUTOTRADER_LOG_LEVEL", "")

    from autotrader.config import Settings  # noqa: PLC0415

    s = Settings()  # type: ignore[call-arg]
    assert s.log_level == "INFO"


def test_telegram_api_id_parses_when_set(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Sanity check: a real int value still parses."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "deadbeef")

    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.api_id == 12345
    assert s.api_hash is not None
    assert s.api_hash.get_secret_value() == "deadbeef"
