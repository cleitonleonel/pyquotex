"""Regression tests for configuration parsing."""

from __future__ import annotations

import pytest


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


def test_telegram_settings_parses_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TELEGRAM_BOT_TOKEN`` env var is parsed as a SecretStr.

    The bot token comes from @BotFather and looks like
    ``123456:ABC-DEF...``. We never want it visible in logs / repr —
    SecretStr enforces that at the type level.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.bot_token is not None
    assert s.bot_token.get_secret_value() == "123456:fake-token"
    # SecretStr never leaks the value through repr.
    assert "fake-token" not in repr(s.bot_token)


def test_telegram_settings_bot_token_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``TELEGRAM_BOT_TOKEN`` leaves the field as None — admin bot
    becomes a no-op rather than crashing startup."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from autotrader.config import TelegramSettings  # noqa: PLC0415

    s = TelegramSettings()  # type: ignore[call-arg]
    assert s.bot_token is None


def test_otp_max_attempts_defaults_to_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default cap on OTP retry attempts per cycle. The relay edits
    the same Telegram message up to this count, then bails with the
    terminal '/reconnect to retry' state.
    """
    monkeypatch.delenv("AUTOTRADER_OTP_MAX_ATTEMPTS", raising=False)
    from autotrader.config import Settings  # noqa: PLC0415

    s = Settings()  # type: ignore[call-arg]
    assert s.otp_max_attempts == 3


def test_otp_max_attempts_env_var_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOTRADER_OTP_MAX_ATTEMPTS", "5")
    from autotrader.config import Settings  # noqa: PLC0415

    s = Settings()  # type: ignore[call-arg]
    assert s.otp_max_attempts == 5


def test_otp_max_attempts_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """Field-level validator rejects nonsense values at parse time."""
    monkeypatch.setenv("AUTOTRADER_OTP_MAX_ATTEMPTS", "0")
    from pydantic import ValidationError  # noqa: PLC0415
    from autotrader.config import Settings  # noqa: PLC0415

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
