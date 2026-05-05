"""Bearer-token authentication for the web API."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from .deps import get_settings


async def verify_api_key(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None),
        settings = Depends(get_settings),
) -> None:
    """Accept the API key in either header form:

    * ``X-API-Key: <key>``
    * ``Authorization: Bearer <key>``

    Reject otherwise.
    """
    candidate: str | None = None
    if x_api_key:
        candidate = x_api_key.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()

    if not candidate or candidate != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
