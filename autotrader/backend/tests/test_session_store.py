"""SessionStore — atomic encrypted dict I/O.

The store persists pyquotex's session_data dict to disk so most
container restarts can skip the HTTP login (and therefore the OTP
challenge). Encrypted at rest with the same Fernet key the rest of
the app uses for secrets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
import structlog
from cryptography.fernet import Fernet


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(Fernet.generate_key())


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "session.json"


def test_save_load_roundtrip(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    payload = {
        "token": "ssid-abc123",
        "cookies": "laravel_session=foo; _cfuvid=bar",
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/144.0",
    }
    store.save(payload)
    loaded = store.load()
    assert loaded == payload


def test_load_missing_file_returns_none(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    assert store.load() is None


def test_load_corrupt_file_returns_none(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    # Write garbage that Fernet can't decrypt.
    store_path.write_bytes(b"not-fernet-ciphertext")
    store = SessionStore(path=store_path, fernet=fernet)
    assert store.load() is None


def test_load_wrong_fernet_key_returns_none(store_path: Path) -> None:
    """Key rotation: an old file under the previous key decrypts to
    None rather than raising. Forces a fresh login on the next start
    rather than crashing the app."""
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    key_a = Fernet(Fernet.generate_key())
    key_b = Fernet(Fernet.generate_key())
    SessionStore(path=store_path, fernet=key_a).save({"token": "x", "cookies": "", "user_agent": ""})
    assert SessionStore(path=store_path, fernet=key_b).load() is None


def test_save_is_atomic_on_os_replace_failure(
    fernet: Fernet, store_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If os.replace raises, the original file (if any) must be
    untouched and no half-written temp file should leak through to
    the final path."""
    from autotrader.services import session_store as ss_mod  # noqa: PLC0415

    # Seed an existing valid file so we can verify it survives.
    ss_mod.SessionStore(path=store_path, fernet=fernet).save(
        {"token": "original", "cookies": "", "user_agent": ""},
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated mid-replace crash")

    monkeypatch.setattr(ss_mod.os, "replace", boom)
    store = ss_mod.SessionStore(path=store_path, fernet=fernet)
    with pytest.raises(OSError, match="simulated"):
        store.save({"token": "new", "cookies": "", "user_agent": ""})

    # Original file is intact.
    loaded = ss_mod.SessionStore(path=store_path, fernet=fernet).load()
    assert loaded == {"token": "original", "cookies": "", "user_agent": ""}


def test_clear_removes_file_idempotently(fernet: Fernet, store_path: Path) -> None:
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    store = SessionStore(path=store_path, fernet=fernet)
    store.save({"token": "x", "cookies": "", "user_agent": ""})
    assert store_path.exists()
    store.clear()
    assert not store_path.exists()
    # Second clear on a missing file must not raise.
    store.clear()


def test_save_does_not_log_token_value(
    fernet: Fernet, store_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """The token is the credential — never let it appear in
    structured logs or stderr output."""
    from autotrader.services.session_store import SessionStore  # noqa: PLC0415

    # structlog routes via stdlib logging — caplog catches both.
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    store = SessionStore(path=store_path, fernet=fernet)
    with caplog.at_level(logging.INFO):
        store.save({"token": "extremely-secret-ssid", "cookies": "", "user_agent": ""})
    joined = " ".join(rec.message for rec in caplog.records)
    assert "extremely-secret-ssid" not in joined
