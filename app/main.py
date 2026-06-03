"""IRIS FastAPI contract application assembly."""

from pathlib import Path

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.bootstrap.lifespan import lifespan
from app.config import settings
from app.core.logger_config import configure_structlog
from app.core.provider import ACTIVE_PROVIDER, health_service
from app.core.rate_limiter import limiter
from app.routers.analysis_router import router as analysis_router
from app.routers.chat_router import router as chat_router
from app.routers.owner_chat_router import router as owner_chat_router

configure_structlog()
logger = structlog.get_logger("app.main")

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
_LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
_API_KEY_EXEMPT_PATHS = {
    "/",
    "/health",
    "/health/integration",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
}

app = FastAPI(
    title="IRIS AI Contract API",
    description="Business KB sync, customer chat signals, PII removal, chat-batch analysis, and report generation.",
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        *settings.CORS_ALLOWED_ORIGINS,
    ],
    allow_origin_regex=_LOCALHOST_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _TOOLS_DIR.exists():
    app.mount("/tools", StaticFiles(directory=str(_TOOLS_DIR)), name="tools")
else:
    logger.warning("[WARN] Tools directory not found at %s", _TOOLS_DIR)


@app.middleware("http")
async def require_backend_api_key(request: Request, call_next):
    expected_key = settings.AI_BACKEND_API_KEY
    if (
        expected_key
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path not in _API_KEY_EXEMPT_PATHS
        and request.headers.get("X-API-Key") != expected_key
    ):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)


@app.middleware("http")
async def add_api_version_header(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-API-Version"] = settings.APP_VERSION
    return response


app.include_router(chat_router)
app.include_router(analysis_router)
app.include_router(owner_chat_router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "IRIS AI Contract API is running.",
        "docs_url": "/docs",
        "provider": ACTIVE_PROVIDER,
        "customer_tool_url": "/tools/customer_chat.html",
        "owner_tool_url": "/tools/owner_chat.html",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    return await health_service.get_health_status()


@app.get("/health/integration", tags=["Health"])
async def integration_status():
    return health_service.get_backend_integration_status()


@app.get("/metrics", tags=["Health"])
async def metrics():
    return health_service.get_metrics()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
    )
