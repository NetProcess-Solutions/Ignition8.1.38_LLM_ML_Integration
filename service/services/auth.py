"""
Per-user attribution via gateway-signed JWTs (Sprint 1 / A4).

The gateway script signs a short-lived JWT (HS256) over
{user_id, session_id, gateway_id, exp, iat} using the shared
GATEWAY_HMAC_SECRET. The service verifies that token and treats
the embedded user_id as the authoritative identity.

Without this layer the X-API-Key header is a shared secret — anyone
with the key could attribute messages to any user, defeating audit-
grade traceability.

Environment in production:
    require_user_token=True (forced by Settings.assert_production_ready)
    gateway_hmac_secret=<32+ random chars>
    gateway_id_allowlist="coater1-gw,coater2-gw"   (optional)
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, status

from config.settings import get_settings


@dataclass
class GatewayToken:
    user_id: str
    session_id: str | None
    gateway_id: str | None
    issued_at: int
    expires_at: int


def _allowed_gateway_ids() -> set[str] | None:
    raw = get_settings().gateway_id_allowlist.strip()
    if not raw:
        return None
    return {x.strip() for x in raw.split(",") if x.strip()}


def verify_gateway_token(token: str) -> GatewayToken:
    """Verify a gateway-signed JWT and return its claims.

    Raises HTTPException(401) on any verification failure.
    """
    s = get_settings()
    secret = s.gateway_hmac_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="gateway_hmac_secret is not configured",
        )
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "user_id"]},
            leeway=10,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, detail="token expired") from None
    except jwt.InvalidTokenError as e:
        raise HTTPException(401, detail=f"invalid token: {e}") from None

    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(401, detail="token missing user_id")

    iat = int(payload.get("iat", 0))
    exp = int(payload.get("exp", 0))
    # Bound the maximum lifetime regardless of what the gateway requested.
    if exp - iat > s.gateway_token_max_age_s + 10:
        raise HTTPException(401, detail="token lifetime exceeds policy")

    gateway_id = payload.get("gateway_id")
    allow = _allowed_gateway_ids()
    if allow is not None and gateway_id not in allow:
        raise HTTPException(401, detail="gateway_id not allowed")

    return GatewayToken(
        user_id=user_id.strip(),
        session_id=payload.get("session_id"),
        gateway_id=gateway_id,
        issued_at=iat,
        expires_at=exp,
    )


def parse_authorization_header(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_user_token(
    authorization: str | None = Header(default=None),
) -> GatewayToken:
    """
    FastAPI dependency that requires a valid gateway-signed bearer token.

    In non-production environments where `require_user_token` is False,
    accepts a missing token and returns a synthetic GatewayToken with
    user_id='dev' so existing dev clients keep working.
    """
    s = get_settings()
    token = parse_authorization_header(authorization)
    if token is None:
        if s.require_user_token or s.service_env == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token required",
            )
        # Dev fall-back; logged elsewhere if needed.
        now = int(time.time())
        return GatewayToken(
            user_id="dev",
            session_id=None,
            gateway_id="dev",
            issued_at=now,
            expires_at=now + 60,
        )
    return verify_gateway_token(token)
