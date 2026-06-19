"""Minimal startup/shutdown orchestration for the contract API."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.core.provider import ACTIVE_PROVIDER, get_business_knowledge_service

logger = structlog.get_logger("app.bootstrap.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] IRIS contract API starting. Provider: %s", ACTIVE_PROVIDER)
    loaded_count = get_business_knowledge_service().load_persisted_indexes()
    logger.info(
        "[READY] Runtime state initialized.",
        persisted_business_kb_loaded=loaded_count,
    )
    yield
    logger.info("[STOP] IRIS contract API shutting down.")
