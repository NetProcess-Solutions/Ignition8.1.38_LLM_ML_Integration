"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import FastAPI

from config.settings import get_settings
from db.connection import SessionFactory, dispose_engine
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


async def _nightly_jobs_loop():
    """
    Lightweight in-process scheduler. Runs each enabled job once per day
    at the first iteration after the configured local hour. For
    production, swap in APScheduler or a dedicated worker — this keeps
    the MVP single-container.
    """
    s = get_settings()
    while True:
        try:
            now = datetime.now(timezone.utc)
            ran_any = False
            if getattr(s, "wo_sync_enabled", False):
                from services.wo_sync import sync_since
                since = now - timedelta(days=1)
                async with SessionFactory() as session:
                    result = await sync_since(session, since)
                log.info("wo_sync_complete", **result)
                ran_any = True
            if getattr(s, "symphony_backfill_enabled", False):
                from services.symphony_capture import backfill_since
                since = now - timedelta(days=1)
                async with SessionFactory() as session:
                    result = await backfill_since(session, since)
                log.info("symphony_backfill_complete", **result)
                ran_any = True
            if not ran_any:
                log.info("nightly_jobs_skipped",
                         reason="no integrations enabled")
        except Exception as e:
            log.error("nightly_jobs_failed", error=str(e))
        # Sleep until next day's run window. Configurable via
        # nightly_jobs_interval_seconds (default 24h).
        interval = int(getattr(s, "nightly_jobs_interval_seconds", 86400))
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("startup", env=s.service_env, llm_provider=s.llm_provider)
    warmup_embeddings()
    log.info("embedding_model_loaded", model=s.embedding_model)
    scheduler_task = None
    if getattr(s, "scheduler_enabled", False):
        scheduler_task = asyncio.create_task(_nightly_jobs_loop())
        log.info("scheduler_started")
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
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
