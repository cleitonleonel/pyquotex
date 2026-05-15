"""Tier-0 settings tests (audit 2026-05-14).

Three new Tier-0 tunables on Settings:

* ``broker_curl_cffi_profile`` — exposes the currently-hardcoded
  ``firefox144`` so a profile rotation no longer needs a redeploy.
* ``broker_stale_feed_max_age_seconds`` — stale-quote threshold
  for the pre-trade health gate (Task 5).
* ``broker_reconnect_hard_ceiling`` — count of consecutive failed
  reconnects after which the manager stops auto-retrying and flips
  to ``awaiting_manual_recovery`` (Task 4).

All three default to safe values; all three are env-overridable
with the ``AUTOTRADER_`` prefix.
"""

from __future__ import annotations

import pytest


def test_defaults_match_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec §3.1/§3.3/§3.4 default values must hold without env overrides."""
    for k in (
        "AUTOTRADER_BROKER_CURL_CFFI_PROFILE",
        "AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS",
        "AUTOTRADER_BROKER_RECONNECT_HARD_CEILING",
    ):
        monkeypatch.delenv(k, raising=False)

    from autotrader.config import Settings  # noqa: PLC0415
    s = Settings()  # type: ignore[call-arg]
    assert s.broker_curl_cffi_profile == "firefox144"
    assert s.broker_stale_feed_max_age_seconds == 10
    assert s.broker_reconnect_hard_ceiling == 20


def test_env_overrides_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator can override all three via AUTOTRADER_* env vars."""
    monkeypatch.setenv("AUTOTRADER_BROKER_CURL_CFFI_PROFILE", "safari170")
    monkeypatch.setenv("AUTOTRADER_BROKER_STALE_FEED_MAX_AGE_SECONDS", "5")
    monkeypatch.setenv("AUTOTRADER_BROKER_RECONNECT_HARD_CEILING", "50")

    from autotrader.config import Settings  # noqa: PLC0415
    s = Settings()  # type: ignore[call-arg]
    assert s.broker_curl_cffi_profile == "safari170"
    assert s.broker_stale_feed_max_age_seconds == 5
    assert s.broker_reconnect_hard_ceiling == 50


def test_hard_ceiling_below_soft_downgrade_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Field(ge=11) rejects values <= 10 — the operator gets a
    pydantic ValidationError that names the field. The intent is
    that the hard ceiling must exceed the soft-downgrade threshold
    (10) so the operator sees the 'transient → outage' notification
    transition before the auto-halt fires."""
    from pydantic import ValidationError  # noqa: PLC0415

    monkeypatch.setenv("AUTOTRADER_BROKER_RECONNECT_HARD_CEILING", "5")
    from autotrader.config import Settings  # noqa: PLC0415

    with pytest.raises(ValidationError, match="broker_reconnect_hard_ceiling"):
        Settings()  # type: ignore[call-arg]


def test_whitespace_only_profile_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """broker_curl_cffi_profile must not be blank after stripping whitespace
    (Task 1 deferred minor). A value of '   ' passes min_length=1 but must
    be rejected before it reaches curl_cffi."""
    from pydantic import ValidationError  # noqa: PLC0415

    monkeypatch.setenv("AUTOTRADER_BROKER_CURL_CFFI_PROFILE", "   ")
    from autotrader.config import Settings  # noqa: PLC0415

    with pytest.raises(ValidationError, match="broker_curl_cffi_profile"):
        Settings()  # type: ignore[call-arg]
