"""POST /api/outcomes - link real-world outcomes to earlier assistant messages."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_session
from models.schemas import OutcomeLinkRequest, OutcomeLinkResponse
from routers.deps import require_api_key
from services.audit import write_audit

router = APIRouter(prefix="/outcomes", tags=["outcomes"],
                   dependencies=[Depends(require_api_key)])


@router.post("", response_model=OutcomeLinkResponse)
async def link_outcome(
    req: OutcomeLinkRequest, session: AsyncSession = Depends(get_session)
) -> OutcomeLinkResponse:
    msg = (await session.execute(
        text("SELECT 1 FROM messages WHERE id = :mid"),
        {"mid": req.message_id},
    )).first()
    if not msg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"message_id {req.message_id} not found",
        )

    table = req.outcome_table
    if table not in ("quality_results", "defect_events", "downtime_events"):
        raise HTTPException(status_code=400, detail="invalid outcome_table")

    target = (await session.execute(
        text(f"SELECT 1 FROM {table} WHERE id = :oid"),  # nosec - table is whitelisted above
        {"oid": req.outcome_id},
    )).first()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"outcome_id {req.outcome_id} not found in {table}",
        )

    linkage_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO outcome_linkages (
                id, message_id, outcome_type, outcome_id, outcome_table,
                alignment, linked_by, notes
            ) VALUES (
                :id, :mid, :otype, :oid, :otable, :align, :lby, :notes
            )
            """
        ),
        {
            "id": linkage_id,
            "mid": req.message_id,
            "otype": req.outcome_type,
            "oid": req.outcome_id,
            "otable": req.outcome_table,
            "align": req.alignment,
            "lby": req.linked_by,
            "notes": req.notes,
        },
    )

    await write_audit(
        session,
        event_type="outcome_linked",
        user_id=req.linked_by,
        entity_type="message",
        entity_id=str(req.message_id),
        details={
            "alignment": req.alignment,
            "outcome_table": req.outcome_table,
            "outcome_id": str(req.outcome_id),
            "linkage_id": str(linkage_id),
        },
    )

    return OutcomeLinkResponse(linkage_id=linkage_id, accepted=True)
