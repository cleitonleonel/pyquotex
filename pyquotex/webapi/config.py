"""Web API configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """All configuration is env-var driven so the same artifact runs in
    dev / Docker / prod without code changes.
    """

    # Quotex broker credentials
    email: str
    password: str
    lang: str = "en"
    account_mode: str = "PRACTICE"  # "PRACTICE" or "REAL"

    # Web-API auth (bearer token / X-API-Key header)
    api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS — comma-separated list of allowed origins; "*" for any
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # WebSocket relay tuning
    relay_poll_interval: float = 0.1   # seconds — how often to flush new ticks
    relay_queue_max: int = 100         # per-subscriber backpressure cap

    # Long-poll timeout for /trades/{id}/result (seconds)
    result_poll_timeout: float = 90.0

    @classmethod
    def from_env(cls) -> "Settings":
        email = os.environ.get("PYQUOTEX_EMAIL", "").strip()
        password = os.environ.get("PYQUOTEX_PASSWORD", "").strip()
        api_key = os.environ.get("PYQUOTEX_API_KEY", "").strip()

        if not email or not password:
            raise RuntimeError(
                "PYQUOTEX_EMAIL and PYQUOTEX_PASSWORD must be set."
            )
        if not api_key:
            raise RuntimeError(
                "PYQUOTEX_API_KEY must be set — refusing to expose the broker "
                "API without authentication."
            )

        cors_raw = os.environ.get("PYQUOTEX_CORS_ORIGINS", "*").strip()
        cors = (
            ["*"] if cors_raw == "*"
            else [o.strip() for o in cors_raw.split(",") if o.strip()]
        )

        return cls(
            email=email,
            password=password,
            lang=os.environ.get("PYQUOTEX_LANG", "en"),
            account_mode=os.environ.get(
                "PYQUOTEX_ACCOUNT_MODE", "PRACTICE"
            ).upper(),
            api_key=api_key,
            host=os.environ.get("PYQUOTEX_HOST", "0.0.0.0"),
            port=int(os.environ.get("PYQUOTEX_PORT", "8000")),
            cors_origins=cors,
            relay_poll_interval=float(
                os.environ.get("PYQUOTEX_RELAY_POLL_INTERVAL", "0.1")
            ),
            relay_queue_max=int(
                os.environ.get("PYQUOTEX_RELAY_QUEUE_MAX", "100")
            ),
            result_poll_timeout=float(
                os.environ.get("PYQUOTEX_RESULT_POLL_TIMEOUT", "90")
            ),
        )
