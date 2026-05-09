"""Single-user passcode authentication.

The passcode is set via env (``AUTOTRADER_PASSCODE``) and hashed with
Argon2id at boot. Successful logins receive a Fernet-encrypted bearer
token whose lifetime is enforced by Fernet's built-in TTL — no separate
session store is required.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from autotrader.config import settings

_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12h

_hasher = PasswordHasher()
_passcode_hash: str = _hasher.hash(settings.passcode.get_secret_value())


def _fernet() -> Fernet:
    return Fernet(settings.fernet_key.get_secret_value().encode())


@dataclass(frozen=True, slots=True)
class Principal:
    """The single authenticated user. Holds token-issue metadata only."""

    issued_at: int
    nonce: str


def verify_passcode(passcode: str) -> bool:
    """Constant-time check against the hashed passcode."""
    try:
        return _hasher.verify(_passcode_hash, passcode)
    except (VerifyMismatchError, VerificationError):
        return False


def issue_token() -> str:
    """Mint a Fernet-encrypted bearer token. TTL enforced on decrypt."""
    payload = f"{int(time.time())}:{secrets.token_urlsafe(16)}".encode()
    return _fernet().encrypt(payload).decode()


def _decode(token: str) -> Principal:
    try:
        plaintext = _fernet().decrypt(token.encode(), ttl=_TOKEN_TTL_SECONDS)
    except InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    issued_at_str, nonce = plaintext.decode().split(":", 1)
    return Principal(issued_at=int(issued_at_str), nonce=nonce)


_bearer = HTTPBearer(auto_error=False)


def require_auth(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode(creds.credentials)
