"""Shared FastAPI dependencies."""
from fastapi import Depends, Header, HTTPException, Request, status

from config.settings import get_settings
from services.auth import GatewayToken, require_user_token


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if not x_api_key or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


async def require_attributed_user(
    request: Request,
    token: GatewayToken = Depends(require_user_token),
) -> GatewayToken:
    """
    Verify the bearer token AND, when a user_id is present on
    request.state (set by `chat_user_key`), require it to match the
    token's user_id. Returns the verified token.
    """
    body_user = getattr(request.state, "user_id", None)
    if (
        body_user
        and body_user not in ("anonymous", "dev")
        and body_user != token.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Body user_id does not match token user_id",
        )
    return token
