"""Symmetric encryption helpers for sensitive data at rest.

Backed by Fernet (AES-128-CBC + HMAC-SHA256). The key is read from the
env (``AUTOTRADER_FERNET_KEY``) and must be a 32-byte url-safe base64
value — generate with ``Fernet.generate_key()``.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from autotrader.config import settings


class CryptoError(Exception):
    """Raised when decryption fails (wrong key or corrupted token)."""


def _fernet() -> Fernet:
    return Fernet(settings.fernet_key.get_secret_value().encode())


def encrypt(plaintext: str | bytes) -> bytes:
    if isinstance(plaintext, str):
        plaintext = plaintext.encode()
    return _fernet().encrypt(plaintext)


def decrypt(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(token)
    except InvalidToken as exc:
        raise CryptoError("decryption failed: wrong key or corrupted token") from exc


def decrypt_str(token: bytes) -> str:
    return decrypt(token).decode()
