"""Shared pytest fixtures and pytest config.

Four pieces live here:

1. ``pytest_configure`` — registers ``unit`` / ``integration`` / ``live``
   markers used to filter tests in CI.
2. ``pytest_collection_modifyitems`` — auto-skips ``@pytest.mark.live``
   tests unless the ``PYQUOTEX_LIVE=1`` environment variable is set.
   Heuristically tags legacy tests that depend on ``credentials()`` /
   real broker access as live so CI doesn't try to run them.
3. ``replay_server`` — spins up a :class:`WSReplayServer` per test, lets
   the test script its responses, tears it down on exit.
4. ``offline_quotex`` — factory that builds a :class:`Quotex` pre-seeded
   with a fake SSID and pointed at the replay server.
"""
from __future__ import annotations

import os
from typing import AsyncIterator, Callable

import pytest

from pyquotex.stable_api import Quotex
from pyquotex.types import ReconnectPolicy
from pyquotex.utils.account_type import AccountType
from tests.fakes.ws_replay_server import WSReplayServer

# Test modules that depend on a real broker session — auto-tagged ``live``.
# Add to this set when introducing a new credentials-using test file.
_LIVE_TEST_MODULES: frozenset[str] = frozenset({
    "test_basic",
    "test_buy",
    "test_login",
    "test_subscribe_indicator",
    "test_deep_history",
    "test_infinite_history",
    "test_win",
    "test_tournament",
    "test_user",
})


def _live_enabled() -> bool:
    return os.environ.get("PYQUOTEX_LIVE", "").lower() in {"1", "true", "yes"}


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers used across the test suite."""
    for marker, description in (
        ("unit", "Pure-Python unit test, no I/O"),
        ("integration", "Uses the WSReplayServer or fakes, but stays offline"),
        ("live", "Hits qxbroker.com; requires credentials and PYQUOTEX_LIVE=1"),
    ):
        config.addinivalue_line("markers", f"{marker}: {description}")


def pytest_ignore_collect(collection_path, config: pytest.Config) -> bool | None:
    """Skip *collection* of legacy live modules when ``PYQUOTEX_LIVE`` is off.

    Several legacy ``test_*.py`` files call :func:`pyquotex.config.credentials`
    at module import time, which blocks on ``input()`` when ``settings/config.ini``
    is missing (as is the case on CI). Collecting them at all would crash the
    run before any test executes, so we drop them entirely outside live mode.
    """
    if _live_enabled():
        return None
    stem = collection_path.stem  # filename without extension
    if stem in _LIVE_TEST_MODULES:
        return True
    return None


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Auto-mark live tests; skip them at runtime unless ``PYQUOTEX_LIVE=1``."""
    live_enabled = _live_enabled()
    skip_live = pytest.mark.skip(
        reason="live test (set PYQUOTEX_LIVE=1 to enable)"
    )
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name in _LIVE_TEST_MODULES:
            item.add_marker(pytest.mark.live)
        if "live" in item.keywords and not live_enabled:
            item.add_marker(skip_live)


@pytest.fixture
async def replay_server() -> AsyncIterator[WSReplayServer]:
    """Spin up a per-test :class:`WSReplayServer`."""
    server = WSReplayServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
def offline_quotex(
    replay_server: WSReplayServer,
) -> Callable[..., Quotex]:
    """Build a :class:`Quotex` whose WS layer points at the replay server.

    The factory pre-seeds session data so the HTTP login is skipped, and
    forwards any kwargs to :class:`Quotex` for per-test overrides.
    """

    def _make(**overrides: object) -> Quotex:
        reconnect = overrides.pop(
            "reconnect_policy",
            ReconnectPolicy(enabled=False, stale_timeout=0),
        )
        client = Quotex(
            email="offline@test",
            password="x",
            lang="en",
            reconnect_policy=reconnect,  # type: ignore[arg-type]
            wss_url_override=replay_server.url,
            **overrides,  # type: ignore[arg-type]
        )
        client.session_data = {
            "cookies": "session=fake",
            "token": "fake-ssid",
            "user_agent": "test-agent/1.0",
        }
        client.account_is_demo = AccountType.DEMO
        return client

    return _make
