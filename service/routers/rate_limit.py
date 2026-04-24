"""
Per-user rate limiting (Sprint 1 / A1).

We use slowapi for fixed-window limits keyed off the authenticated user_id.
The user_id is stored on `request.state.user_id` by `chat_user_key()` —
which parses the JSON body once and caches it. slowapi then reads via
`user_key_func`.

Two limits are applied to /api/chat:
- per-minute burst limit (`chat_rate_per_user_per_min`)
- per-day cumulative limit (`chat_rate_per_user_per_day`)
"""
from __future__ import annotations

import json

from fastapi import HTTPException, Request, status
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config.settings import get_settings


def _user_key_func(request: Request) -> str:
    """Read user_id stamped on request.state by `chat_user_key`."""
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    # Fall back to remote address so unauthenticated calls are still bounded.
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_user_key_func, headers_enabled=True)


async def chat_user_key(request: Request) -> str:
    """
    Dependency for /api/chat that:
      1. Reads the JSON body once (caching it for the route handler), and
      2. Stores the user_id on `request.state` so the limiter sees it.
    """
    if not hasattr(request.state, "_cached_body"):
        body = await request.body()
        request.state._cached_body = body
    else:
        body = request.state._cached_body
    user_id = "anonymous"
    try:
        if body:
            payload = json.loads(body)
            uid = payload.get("user_id")
            if isinstance(uid, str) and uid.strip():
                user_id = uid.strip()
    except (ValueError, TypeError):
        pass
    request.state.user_id = user_id
    return user_id


def chat_rate_limits() -> str:
    s = get_settings()
    return f"{s.chat_rate_per_user_per_min}/minute;{s.chat_rate_per_user_per_day}/day"


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> HTTPException:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded: {exc.detail}",
        headers={"Retry-After": "60"},
    )
