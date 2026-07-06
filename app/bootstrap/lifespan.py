"""Minimal startup/shutdown orchestration for the contract API."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.core.provider import (
    ACTIVE_PROVIDER,
    get_business_knowledge_service,
    get_owner_report_service,
)

logger = structlog.get_logger("app.bootstrap.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] IRIS contract API starting. Provider: %s", ACTIVE_PROVIDER)
    loaded_count = get_business_knowledge_service().load_persisted_indexes()
    owner_reports_loaded = get_owner_report_service().load_persisted_reports()
    logger.info(
        "[READY] Runtime state initialized.",
        persisted_business_kb_loaded=loaded_count,
        persisted_owner_reports_loaded=owner_reports_loaded,
    )
    yield
    logger.info("[STOP] IRIS contract API shutting down.")
