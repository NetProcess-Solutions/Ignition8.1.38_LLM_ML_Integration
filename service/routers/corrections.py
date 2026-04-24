"""POST /api/corrections - structured user corrections."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_session
from models.schemas import CorrectionRequest, CorrectionResponse
from routers.deps import require_api_key, require_attributed_user
from services.audit import write_audit

router = APIRouter(prefix="/corrections", tags=["corrections"],
                   dependencies=[Depends(require_api_key),
                                 Depends(require_attributed_user)])


@router.post("", response_model=CorrectionResponse)
async def submit_correction(
    req: CorrectionRequest, session: AsyncSession = Depends(get_session)
) -> CorrectionResponse:
    exists = (await session.execute(
        text("SELECT 1 FROM messages WHERE id = :mid"),
        {"mid": req.message_id},
    )).first()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"message_id {req.message_id} not found",
        )

    correction_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO user_corrections (
                id, message_id, user_id, correction_type,
                original_claim, corrected_claim, supporting_evidence
            ) VALUES (
                :id, :mid, :uid, :ctype, :orig, :corr, :evid
            )
            """
        ),
        {
            "id": correction_id,
            "mid": req.message_id,
            "uid": req.user_id,
            "ctype": req.correction_type,
            "orig": req.original_claim,
            "corr": req.corrected_claim,
            "evid": req.supporting_evidence,
        },
    )

    # If this correction conflicts with an approved memory used in the message,
    # increment its challenge_count.
    await session.execute(
        text(
            """
            UPDATE line_memory
            SET challenge_count = challenge_count + 1,
                last_challenged_at = NOW(),
                status = CASE WHEN challenge_count + 1 >= 3 AND status = 'approved'
                              THEN 'challenged' ELSE status END
            WHERE id IN (
                SELECT (mu->>'memory_id')::uuid
                FROM messages m, jsonb_array_elements(m.memories_used) mu
                WHERE m.id = :mid AND (mu->>'memory_id') IS NOT NULL
            )
            """
        ),
        {"mid": req.message_id},
    )

    await write_audit(
        session,
        event_type="correction_submitted",
        user_id=req.user_id,
        entity_type="message",
        entity_id=str(req.message_id),
        details={"correction_type": req.correction_type, "correction_id": str(correction_id)},
    )

    return CorrectionResponse(correction_id=correction_id, accepted=True)
