"""Auth + connection lifecycle endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import verify_api_key
from ..deps import get_client, get_settings
from ..models import ConnectResponse

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(verify_api_key)])


@router.post(
    "/connect",
    response_model=ConnectResponse,
    summary="Warm the broker WebSocket and return profile + balance",
)
async def connect_endpoint(
        client = Depends(get_client),
        settings = Depends(get_settings),
) -> ConnectResponse:
    """Idempotent: if the client is already connected, returns the
    cached state immediately. Otherwise issues ``Quotex.connect()``
    (which is itself single-flighted via an internal lock as of v1.3.0).
    """
    ok, reason = await client.connect()
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Broker connect failed: {reason}",
        )

    client.set_account_mode(settings.account_mode)

    profile = None
    balance = None
    try:
        profile = await client.get_profile()
        balance = await client.get_balance()
    except Exception:
        # Don't fail the connect just because profile/balance hydration
        # is slow on a cold start; the dedicated endpoints can fetch
        # again.
        pass

    return ConnectResponse(
        connected=True,
        message=str(reason),
        balance=float(balance) if balance is not None else None,
        profile_id=getattr(profile, "profile_id", None),
        nickname=getattr(profile, "nick_name", None),
        currency=getattr(profile, "currency_code", None),
    )
