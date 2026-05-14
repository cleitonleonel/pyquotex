"""Configuration loaded from environment variables.

Two settings classes, one per env-var prefix, so groups stay readable:

    AUTOTRADER_*  -> Settings        (app config, secrets, tunables)
    TELEGRAM_*    -> TelegramSettings (Pyrogram MTProto credentials)
"""

from __future__ import annotations

from functools import cached_property

from pydantic import Field, SecretStr, model_validator
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

    # Maximum OTP attempts per cycle before the relay gives up and
    # edits the message to '/reconnect to retry'. Trades off
    # alert-fatigue (low) against finger-fumble forgiveness (high).
    # 3 is the sweet spot per the spec; tune via env if you find
    # yourself routinely needing more.
    otp_max_attempts: int = Field(default=3, ge=1, le=10)

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

    # When true, ``TelegramManager`` attaches a ``RawUpdateHandler``
    # that logs every Pyrogram update kind. Useful when channel posts
    # fail to reach the live ``MessageHandler`` (peer-cache miss,
    # missing channel-update subscription) — a wave of
    # ``UpdateChannelMessageViews`` with no ``UpdateNewChannelMessage``
    # tells you the channel update stream is alive but the post-
    # delivery path is dropping. Off by default — high-cardinality.
    debug_telegram_raw_updates: bool = False

    # When true, ``executor._place`` wraps each broker ``buy()`` /
    # ``open_pending()`` in :class:`BrokerWireTrace`, which taps
    # pyquotex's ``send_websocket_request`` to record every outgoing
    # socket.io frame around the call. On exit (success or
    # ``TimeoutError``) it emits ``executor.broker_wire.preflight``
    # and ``executor.broker_wire.postmortem`` structured logs with
    # the captured frames, ``realtime_price[asset]`` deque length
    # before/after, and which registry events fired. Used to
    # diagnose silent ``broker_error: Timeout waiting for realtime
    # price data`` failures where the broker UI shows the asset as
    # tradable. Off by default — flip to true only while a known
    # issue is being investigated, the postmortem payload is large.
    debug_broker_wire: bool = False

    # ── Tier-0 broker reliability (audit 2026-05-14) ─────────────
    #
    # curl_cffi impersonate profile used by pyquotex's HTTP login
    # path. Currently hardcoded at quotex_manager.py:405; exposing
    # it lets the operator rotate profiles without a redeploy when
    # Cloudflare's bot scoring shifts. Sweep candidates with the
    # probe in RUNBOOK §B.
    broker_curl_cffi_profile: str = "firefox144"

    # Pre-trade WS health gate (Task 5 / spec §3.4). If the latest
    # tick for the asset is older than this, the executor refuses
    # to send the order and marks the attempt ``broker_error``. The
    # martingale ladder is NOT advanced on a health-gate block —
    # the trade never reached the broker.
    broker_stale_feed_max_age_seconds: int = Field(default=10, ge=1, le=300)

    # Hard reconnect ceiling (Task 4 / spec §3.3). After this many
    # consecutive failed reconnect attempts, the manager stops the
    # pyquotex supervisor, disconnects cleanly, and flips state to
    # ``awaiting_manual_recovery``. Operator must run /reconnect.
    # Must be > _SOFT_DOWNGRADE_AFTER_ATTEMPTS (= 10) so the operator
    # sees the 'transient → outage' notification before the auto-halt.
    broker_reconnect_hard_ceiling: int = Field(default=20, ge=11, le=200)

    @cached_property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_hard_ceiling(self) -> "Settings":
        # Field(ge=11) covers most cases, but pydantic emits a
        # cryptic "Input should be greater than or equal to 11"
        # without naming the config knob. Re-check here for the
        # cleaner error.
        if self.broker_reconnect_hard_ceiling <= 10:
            raise ValueError(
                "broker_reconnect_hard_ceiling must exceed the "
                "soft-downgrade threshold (10); set >= 11"
            )
        return self


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
    # @BotFather token for the *admin* bot (separate from the userbot
    # MTProto session). When unset the admin bot is a no-op — see
    # ``services/admin_bot.py``. Stored as SecretStr so it's never
    # leaked through ``repr(settings)`` or accidental logging.
    bot_token: SecretStr | None = None


settings = Settings()  # type: ignore[call-arg]
telegram_settings = TelegramSettings()  # type: ignore[call-arg]
