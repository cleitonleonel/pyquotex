"""Admin Telegram bot — unit + integration tests."""

from __future__ import annotations

import pytest

from autotrader.models.settings import GlobalSettings


def test_global_settings_has_admin_fields_with_safe_defaults() -> None:
    """Fresh ``GlobalSettings`` row defaults to:
    - admin unbound (``admin_telegram_user_id is None``)
    - all four notify classes ON

    Defaults matter: a brand-new install with no bot configured must
    still construct a valid settings row, and once the operator binds
    the admin they should immediately receive the full event firehose
    without flipping four extra toggles.
    """
    s = GlobalSettings()
    assert s.admin_telegram_user_id is None
    assert s.admin_notify_placed is True
    assert s.admin_notify_settled is True
    assert s.admin_notify_risk_rejected is True
    assert s.admin_notify_system_error is True
