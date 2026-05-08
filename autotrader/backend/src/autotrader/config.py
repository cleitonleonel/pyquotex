"""Configuration loaded from environment variables.

Two settings classes, one per env-var prefix, so groups stay readable:

    AUTOTRADER_*  -> Settings        (app config, secrets, tunables)
    TELEGRAM_*    -> TelegramSettings (Pyrogram MTProto credentials)
"""

from __future__ import annotations

from functools import cached_property

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings — loaded once at import."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AUTOTRADER_",
        extra="ignore",
        case_sensitive=False,
        # Treat empty env vars as unset so docker-compose's ``${VAR:-}``
        # interpolation falls back to the model defaults instead of
        # failing the int/bool parsers with "".
        env_ignore_empty=True,
    )

    # Single-user dashboard passcode (verified with Argon2id at runtime).
    passcode: SecretStr

    # Master key for at-rest encryption (Fernet 32-byte url-safe base64).
    fernet_key: SecretStr

    # Hard gate for real-money trading. Even when true, channels still
    # have their own enable flag — this is a *master* off-switch.
    live_trading_enabled: bool = False

    db_url: str = "sqlite+aiosqlite:///./data/autotrader.db"

    log_level: str = "INFO"

    api_host: str = "0.0.0.0"  # noqa: S104  (binding 0.0.0.0 is intentional in Docker)
    api_port: int = 8000

    # Comma-separated list, or "*" for any origin.
    cors_origins: str = "http://localhost:3000"

    # SQLite online backups. ``0`` disables the scheduler — a stock
    # install never writes to disk for backups until the operator
    # opts in. Backups land in ``<db_dir>/backups/`` by default.
    backup_interval_seconds: int = 0
    backup_retain: int = 24
    backup_dir: str = ""  # empty → derive from db_url

    # Optional Sentry error reporting. Set ``SENTRY_DSN`` to enable.
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.0

    @cached_property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


class TelegramSettings(BaseSettings):
    """Telegram MTProto API credentials (https://my.telegram.org/apps).

    Optional in Phase 0; required once the Telegram client comes online
    in Phase 2.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TELEGRAM_",
        extra="ignore",
        # docker-compose passes ``TELEGRAM_API_ID=""`` when the user has
        # not configured Telegram yet — without this flag, pydantic
        # tries to parse "" as int and crashes on startup.
        env_ignore_empty=True,
    )

    api_id: int | None = None
    api_hash: SecretStr | None = None


settings = Settings()  # type: ignore[call-arg]
telegram_settings = TelegramSettings()  # type: ignore[call-arg]
