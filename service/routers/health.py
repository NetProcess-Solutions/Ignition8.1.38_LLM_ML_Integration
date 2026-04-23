"""Health check (no auth required)."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from db.connection import get_session
from models.schemas import HealthResponse
from services.embeddings import _model

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    embed_ok = False
    try:
        _ = _model()
        embed_ok = True
    except Exception:
        pass

    overall = "ok" if (db_ok and embed_ok) else "degraded"
    return HealthResponse(
        status=overall,
        database=db_ok,
        embedding_model=embed_ok,
        llm_provider=get_settings().llm_provider,
    )
