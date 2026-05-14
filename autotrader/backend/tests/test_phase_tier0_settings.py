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
    """Setting the hard ceiling lower than the soft-downgrade threshold
    (10, see quotex_manager._SOFT_DOWNGRADE_AFTER_ATTEMPTS) is a config
    error: the operator would never see the 'transient → outage'
    notification transition before the auto-halt fires."""
    monkeypatch.setenv("AUTOTRADER_BROKER_RECONNECT_HARD_CEILING", "5")
    from autotrader.config import Settings  # noqa: PLC0415
    with pytest.raises(ValueError, match="hard_ceiling"):
        Settings()  # type: ignore[call-arg]
