"""Minimal startup/shutdown orchestration for the contract API."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.core.provider import ACTIVE_PROVIDER

logger = structlog.get_logger("app.bootstrap.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[START] IRIS contract API starting. Provider: %s", ACTIVE_PROVIDER)
    logger.info("[READY] Runtime state is in-memory only.")
    yield
    logger.info("[STOP] IRIS contract API shutting down.")
