"""POST /api/chat - the main RAG endpoint."""
from fastapi import APIRouter, Depends, Request

from models.schemas import ChatRequest, ChatResponse
from routers.deps import require_api_key, require_attributed_user
from routers.rate_limit import chat_rate_limits, chat_user_key, limiter
from services.rag import handle_chat

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[
        Depends(require_api_key),
        Depends(chat_user_key),
        Depends(require_attributed_user),
    ],
)


@router.post("", response_model=ChatResponse)
@limiter.limit(chat_rate_limits)
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    return await handle_chat(req)
