"""POST /api/feedback - per-message feedback signals."""
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_session
from models.schemas import FeedbackRequest, FeedbackResponse
from routers.deps import require_api_key
from services.audit import write_audit

router = APIRouter(prefix="/feedback", tags=["feedback"],
                   dependencies=[Depends(require_api_key)])


@router.post("", response_model=FeedbackResponse)
async def submit_feedback(
    req: FeedbackRequest, session: AsyncSession = Depends(get_session)
) -> FeedbackResponse:
    exists = (await session.execute(
        text("SELECT 1 FROM messages WHERE id = :mid"),
        {"mid": req.message_id},
    )).first()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"message_id {req.message_id} not found",
        )

    feedback_id = uuid4()
    await session.execute(
        text(
            """
            INSERT INTO message_feedback
                (id, message_id, user_id, signal_type, signal_value, comment)
            VALUES
                (:id, :mid, :uid, :stype, :sval, :cmt)
            """
        ),
        {
            "id": feedback_id,
            "mid": req.message_id,
            "uid": req.user_id,
            "stype": req.signal_type,
            "sval": req.signal_value,
            "cmt": req.comment,
        },
    )

    # Update chunk_quality_signals for any chunks cited in this message,
    # bounded so a single signal can't dominate.
    if req.signal_type in ("usefulness", "source_relevance", "correctness"):
        delta = 1 if req.signal_value == "positive" else (-1 if req.signal_value == "negative" else 0)
        if delta != 0:
            await session.execute(
                text(
                    """
                    INSERT INTO chunk_quality_signals (chunk_id, positive_count, negative_count, cited_count, quality_score, last_updated)
                    SELECT
                        (s->>'metadata')::jsonb->>'chunk_id' AS chunk_id_text,
                        CASE WHEN :delta > 0 THEN 1 ELSE 0 END,
                        CASE WHEN :delta < 0 THEN 1 ELSE 0 END,
                        1,
                        0.0,
                        NOW()
                    FROM messages m, jsonb_array_elements(m.sources) s
                    WHERE m.id = :mid
                      AND s->>'type' = 'document_chunk'
                      AND (s->'metadata'->>'chunk_id') IS NOT NULL
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        positive_count = chunk_quality_signals.positive_count
                            + CASE WHEN :delta > 0 THEN 1 ELSE 0 END,
                        negative_count = chunk_quality_signals.negative_count
                            + CASE WHEN :delta < 0 THEN 1 ELSE 0 END,
                        cited_count = chunk_quality_signals.cited_count + 1,
                        quality_score = LEAST(0.5, GREATEST(-0.5,
                            (chunk_quality_signals.positive_count + CASE WHEN :delta > 0 THEN 1 ELSE 0 END
                             - chunk_quality_signals.negative_count - CASE WHEN :delta < 0 THEN 1 ELSE 0 END)::numeric
                            / NULLIF(chunk_quality_signals.cited_count + 1, 0)
                        )),
                        last_updated = NOW()
                    """
                ),
                {"mid": req.message_id, "delta": delta},
            )

    await write_audit(
        session,
        event_type="feedback_submitted",
        user_id=req.user_id,
        entity_type="message",
        entity_id=str(req.message_id),
        details={"signal_type": req.signal_type, "signal_value": req.signal_value},
    )

    return FeedbackResponse(feedback_id=feedback_id, accepted=True)
