"""Market-data endpoints (recent + deep historical candles + sentiment)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import verify_api_key
from ..deps import get_client
from ..models import (
    Candle,
    CandlesResponse,
    HistoricalCandlesResponse,
    SentimentResponse,
)

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(verify_api_key)])


def _normalise_candles(rows) -> list[Candle]:
    out: list[Candle] = []
    for c in rows or []:
        if isinstance(c, dict) and "time" in c:
            out.append(Candle(
                time=int(c["time"]),
                open=float(c.get("open", 0)),
                close=float(c.get("close", 0)),
                high=float(c.get("high", c.get("max", 0))),
                low=float(c.get("low", c.get("min", 0))),
            ))
    return out


@router.get(
    "/candles",
    response_model=CandlesResponse,
    summary="Recent candles (capped by broker at ~200 per request)",
)
async def candles_endpoint(
        asset: str = Query(..., description="e.g. EURUSD or EURUSD_otc"),
        period: int = Query(60, gt=0, description="Candle size in seconds"),
        offset: int = Query(3600, gt=0, description="How far back from now, in seconds"),
        end_from_time: float | None = Query(
            None, description="Anchor timestamp; defaults to now",
        ),
        client = Depends(get_client),
) -> CandlesResponse:
    end = end_from_time if end_from_time is not None else time.time()
    try:
        candles = await client.get_candles(asset, end, offset, period)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"get_candles failed: {e}",
        ) from e

    rows = _normalise_candles(candles)
    return CandlesResponse(
        asset=asset, period=period, count=len(rows), candles=rows
    )


@router.get(
    "/historical-candles",
    response_model=HistoricalCandlesResponse,
    summary="Deep historical candles (paginated + parallel-stitched)",
)
async def historical_candles_endpoint(
        asset: str = Query(...),
        period: int = Query(60, gt=0),
        amount_of_seconds: int = Query(
            ..., gt=0,
            description="Lookback in seconds (≈ N_candles * period)",
        ),
        max_workers: int = Query(
            5, gt=0, le=10,
            description="Parallel workers; broker bans above ~10",
        ),
        client = Depends(get_client),
) -> HistoricalCandlesResponse:
    try:
        candles = await client.get_historical_candles(
            asset=asset,
            amount_of_seconds=amount_of_seconds,
            period=period,
            max_workers=max_workers,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"get_historical_candles failed: {e}",
        ) from e

    rows = _normalise_candles(candles)
    return HistoricalCandlesResponse(
        asset=asset,
        period=period,
        amount_of_seconds=amount_of_seconds,
        count=len(rows),
        oldest_time=rows[0].time if rows else None,
        newest_time=rows[-1].time if rows else None,
        candles=rows,
    )


@router.get(
    "/sentiment/{asset}",
    response_model=SentimentResponse,
    summary="Current trader sentiment for an asset",
)
async def sentiment_endpoint(
        asset: str,
        timeout: float = Query(10.0, gt=0, le=60),
        client = Depends(get_client),
) -> SentimentResponse:
    try:
        await client.start_realtime_sentiment(asset, timeout=timeout)
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"sentiment fetch failed: {e}",
        ) from e

    raw = await client.get_realtime_sentiment(asset)
    bullish = raw.get("bullish") or raw.get("sentimentBuy") or raw.get("buy")
    bearish = raw.get("bearish") or raw.get("sentimentSell") or raw.get("sell")
    return SentimentResponse(
        asset=asset,
        bullish=float(bullish) if bullish is not None else None,
        bearish=float(bearish) if bearish is not None else None,
        raw=dict(raw),
    )
