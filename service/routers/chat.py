"""POST /api/chat - the main RAG endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_session
from models.schemas import ChatRequest, ChatResponse
from routers.deps import require_api_key
from services.rag import handle_chat

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest, session: AsyncSession = Depends(get_session)
) -> ChatResponse:
    return await handle_chat(session, req)
