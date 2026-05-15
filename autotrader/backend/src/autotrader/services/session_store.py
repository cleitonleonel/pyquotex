"""Atomic encrypted persistence for pyquotex's ``session_data`` dict.

The broker's session — SSID token + cookies + user-agent — lives in
process memory by default in pyquotex. We persist it to disk so most
container restarts can skip the HTTP login (and therefore the OTP
challenge) when the SSID is still valid.

Encryption at rest uses the same ``AUTOTRADER_FERNET_KEY`` that
protects ``broker_credentials``; reusing the key keeps the deployment
story unchanged (no new secret to provision, rotate, back up).

Atomic write: ``save()`` writes ``${path}.tmp`` then ``os.replace()``
to ``${path}``. Without this, a crash mid-write would corrupt the
file and the next ``load()`` would return None — safe-by-default but
wasteful. With the rename, the file is either the old contents or
the new, never partial.

Schema (pyquotex-native):
    {"token": str, "cookies": str, "user_agent": str}

No schema version field — we control both ends and would coordinate a
rewrite if the shape ever changed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog
from cryptography.fernet import Fernet, InvalidToken

log = structlog.get_logger(__name__)


class SessionStore:
    """Persists a session dict, encrypted at rest."""

    def __init__(self, *, path: Path, fernet: Fernet) -> None:
        self._path = path
        self._fernet = fernet

    def load(self) -> dict[str, Any] | None:
        """Return the decrypted dict, or None on any failure.

        Never raises — a corrupt file, wrong key, or missing file all
        fall back to None so the caller treats this as 'no cached
        session' and runs a fresh login.
        """
        try:
            ciphertext = self._path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken:
            log.warning("broker.session.decrypt_failed", path=str(self._path))
            return None
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("broker.session.parse_failed", path=str(self._path))
            return None
        if not isinstance(payload, dict):
            log.warning(
                "broker.session.bad_shape", path=str(self._path),
                got_type=type(payload).__name__,
            )
            return None
        return payload

    def save(self, session_data: dict[str, Any]) -> None:
        """Atomically replace the on-disk file with the encrypted dict.

        Never logs the token value — only its presence.
        """
        # Best-effort directory create — covers first-startup on a
        # fresh /data volume.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(session_data).encode("utf-8")
        ciphertext = self._fernet.encrypt(plaintext)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_bytes(ciphertext)
        os.replace(tmp_path, self._path)
        log.info(
            "broker.session.persisted",
            token_present=bool(session_data.get("token")),
            path=str(self._path),
        )

    def clear(self) -> None:
        """Remove the on-disk file; idempotent."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        log.info("broker.session.cleared", path=str(self._path))
