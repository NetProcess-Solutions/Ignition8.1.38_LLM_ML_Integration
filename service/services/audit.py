"""Append-only audit log writer."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def write_audit(
    session: AsyncSession,
    *,
    event_type: str,
    user_id: str | None = None,
    session_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_log
                (event_type, user_id, session_id, entity_type, entity_id, details)
            VALUES
                (:event_type, :user_id, :session_id, :entity_type, :entity_id,
                 CAST(:details AS jsonb))
            """
        ),
        {
            "event_type": event_type,
            "user_id": user_id,
            "session_id": session_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "details": _json_safe(details or {}),
        },
    )


def _json_safe(obj: Any) -> str:
    import json
    from datetime import date, datetime
    from uuid import UUID

    def default(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        return str(o)

    return json.dumps(obj, default=default)
