"""Trading endpoints — buy, pending, pending-at-price, result, cancel."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import verify_api_key
from ..deps import get_client, get_settings
from ..models import (
    BuyRequest,
    BuyResponse,
    CancelResponse,
    PendingAtPriceRequest,
    PendingRequest,
    PendingResponse,
    TradeResultResponse,
)

router = APIRouter(prefix="/trades", tags=["trades"], dependencies=[Depends(verify_api_key)])


def _detail_payload(info: Any) -> dict[str, Any] | str | None:
    if info is None:
        return None
    if isinstance(info, dict):
        return info
    return str(info)


@router.post(
    "/buy",
    response_model=BuyResponse,
    summary="Place an immediate trade",
)
async def buy_endpoint(
        body: BuyRequest, client = Depends(get_client),
) -> BuyResponse:
    try:
        ok, info = await client.buy(
            amount=body.amount,
            asset=body.asset,
            direction=body.direction,
            duration=body.duration,
            time_mode=body.time_mode,
            confirm_timeout=body.confirm_timeout,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"buy() failed: {e}",
        ) from e

    order_id = (
        info.get("id") if isinstance(info, dict) else None
    )
    return BuyResponse(
        accepted=bool(ok),
        order_id=order_id,
        detail=_detail_payload(info),
    )


@router.post(
    "/pending",
    response_model=PendingResponse,
    summary="Place a time-based pending order",
)
async def pending_endpoint(
        body: PendingRequest, client = Depends(get_client),
) -> PendingResponse:
    try:
        ok, info = await client.open_pending(
            amount=body.amount,
            asset=body.asset,
            direction=body.direction,
            duration=body.duration,
            open_time=body.open_time,
            confirm_timeout=body.confirm_timeout,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"open_pending() failed: {e}",
        ) from e

    pending_id = client.api.pending_id if client.api else None
    return PendingResponse(
        accepted=bool(ok),
        pending_id=str(pending_id) if pending_id else None,
        detail=_detail_payload(info),
    )


@router.post(
    "/pending-at-price",
    response_model=PendingResponse,
    summary="Place a quote-triggered pending order (experimental)",
)
async def pending_at_price_endpoint(
        body: PendingAtPriceRequest, client = Depends(get_client),
) -> PendingResponse:
    try:
        ok, info = await client.open_pending_at_price(
            amount=body.amount,
            asset=body.asset,
            direction=body.direction,
            quote=body.quote,
            period=body.period,
            timeout=body.timeout,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"open_pending_at_price() failed: {e}",
        ) from e

    pending_id = client.api.pending_id if client.api else None
    return PendingResponse(
        accepted=bool(ok),
        pending_id=str(pending_id) if pending_id else None,
        detail=_detail_payload(info),
    )


@router.get(
    "/{order_id}/result",
    response_model=TradeResultResponse,
    summary="Long-poll for the trade result (event-driven via wait_for_order_close)",
)
async def trade_result_endpoint(
        order_id: str,
        duration: int = Query(
            60, ge=0,
            description=(
                "Trade duration; sizes the default wait. Pass 0 to use "
                "the explicit ``timeout`` instead."
            ),
        ),
        timeout: float | None = Query(
            None, gt=0, le=600,
            description="Override the long-poll deadline in seconds",
        ),
        client = Depends(get_client),
        settings = Depends(get_settings),
) -> TradeResultResponse:
    deadline = timeout if timeout is not None else settings.result_poll_timeout

    try:
        result, profit = await asyncio.wait_for(
            client.wait_for_order_close(order_id, duration=duration, timeout=deadline),
            timeout=deadline + 2,
        )
    except asyncio.TimeoutError:
        return TradeResultResponse(order_id=order_id, status="timeout", profit=0.0)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"wait_for_order_close() failed: {e}",
        ) from e

    if result not in ("win", "loss"):
        return TradeResultResponse(order_id=order_id, status="unknown", profit=float(profit))
    return TradeResultResponse(order_id=order_id, status=result, profit=float(profit))


@router.delete(
    "/{order_id}",
    response_model=CancelResponse,
    summary="Sell an open option early (cancel)",
)
async def cancel_endpoint(
        order_id: str, client = Depends(get_client),
) -> CancelResponse:
    try:
        result = await client.sell_option(order_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"sell_option() failed: {e}",
        ) from e

    return CancelResponse(
        cancelled=bool(result),
        detail=_detail_payload(result),
    )
