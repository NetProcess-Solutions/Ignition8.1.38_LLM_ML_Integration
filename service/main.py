"""FastAPI application entry point."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from config.settings import get_settings
from db.connection import dispose_engine
from routers import chat, corrections, feedback, health, outcomes, select_tags
from services.embeddings import warmup as warmup_embeddings

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("startup", env=s.service_env, llm_provider=s.llm_provider)
    warmup_embeddings()
    log.info("embedding_model_loaded", model=s.embedding_model)
    yield
    await dispose_engine()
    log.info("shutdown_complete")


app = FastAPI(
    title="IgnitionChatbot AI Service",
    description="RAG-grounded line assistant for Ignition 8.1 (Coater 1).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router,      prefix="/api")
app.include_router(chat.router,        prefix="/api")
app.include_router(feedback.router,    prefix="/api")
app.include_router(corrections.router, prefix="/api")
app.include_router(outcomes.router,    prefix="/api")
app.include_router(select_tags.router, prefix="/api")
