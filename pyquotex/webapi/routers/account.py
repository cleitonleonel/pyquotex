"""Account / health endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import verify_api_key
from ..deps import get_client, get_settings
from ..models import BalanceResponse, HealthResponse, ProfileResponse

# /health intentionally has NO auth — it's the readiness probe.
public_router = APIRouter(tags=["health"])
router = APIRouter(prefix="/account", tags=["account"], dependencies=[Depends(verify_api_key)])


@public_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Readiness probe — broker connection state + reconnect stats",
)
async def health_endpoint(client = Depends(get_client)) -> HealthResponse:
    api = client.api
    sup = getattr(api, "reconnect_supervisor", None) if api else None
    auth_status_name = "unknown"
    connected = False

    if api is not None:
        connected = await client.check_connect()
        try:
            auth_status_name = api.state.auth_status.name
        except Exception:
            auth_status_name = str(api.state.auth_status)

    return HealthResponse(
        status="ok" if connected else "degraded",
        connected=connected,
        auth_status=auth_status_name,
        last_reconnect_at=getattr(sup.stats, "last_reconnect_at", None) if sup else None,
        reconnect_attempts=getattr(sup.stats, "attempts", 0) if sup else 0,
        successful_reconnects=getattr(sup.stats, "successful_reconnects", 0) if sup else 0,
        in_flight_pendings=len(api._active_pending) if api else 0,
    )


@router.get(
    "/balance",
    response_model=BalanceResponse,
    summary="Current account balance",
)
async def balance_endpoint(
        client = Depends(get_client),
        settings = Depends(get_settings),
) -> BalanceResponse:
    try:
        balance = await client.get_balance()
        profile = await client.get_profile()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Broker call failed: {e}",
        ) from e

    return BalanceResponse(
        balance=float(balance) if balance is not None else 0.0,
        currency=getattr(profile, "currency_code", None),
        account_mode=settings.account_mode,
    )


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Profile data (nickname, currency, timezone, balances)",
)
async def profile_endpoint(client = Depends(get_client)) -> ProfileResponse:
    try:
        profile = await client.get_profile()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Broker call failed: {e}",
        ) from e

    return ProfileResponse(
        profile_id=getattr(profile, "profile_id", None),
        nickname=getattr(profile, "nick_name", None),
        currency_code=getattr(profile, "currency_code", None),
        currency_symbol=getattr(profile, "currency_symbol", None),
        country=getattr(profile, "country_name", None),
        timezone_offset=getattr(profile, "offset", None),
        demo_balance=float(getattr(profile, "demo_balance", 0) or 0) or None,
        live_balance=float(getattr(profile, "live_balance", 0) or 0) or None,
    )
