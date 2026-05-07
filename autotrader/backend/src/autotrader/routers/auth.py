"""Login + identity endpoints for the single-user dashboard."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from autotrader.auth import Principal, issue_token, require_auth, verify_passcode

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    passcode: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"  # noqa: S105  (OAuth2 token-type literal, not a credential)


class MeResponse(BaseModel):
    issued_at: int


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    if not verify_passcode(req.passcode):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid passcode",
        )
    return LoginResponse(token=issue_token())


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[Principal, Depends(require_auth)]) -> MeResponse:
    return MeResponse(issued_at=user.issued_at)
