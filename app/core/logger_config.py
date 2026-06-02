"""
==============================================================
 Contextual Structured Logging Configuration
==============================================================
 Provides structured JSON logging via `structlog` with automatic
 `session_id` injection using Python's `contextvars`.

 Usage:
   1. Call `configure_structlog()` once at application startup.
   2. In request handlers, call `bind_session(session_id)` at the
      start and `clear_session()` in a `finally` block.
   3. In any module, use `logger = structlog.get_logger(__name__)`
      — the session_id will appear in every log event automatically.
==============================================================
"""

import logging
import sys
from contextvars import ContextVar

import structlog

# ──────────────────────────────────────────────────────────────────
# Session Context Variable
# ──────────────────────────────────────────────────────────────────
# Stores the active session_id for the current asyncio task / thread.
# All structlog processors can read this without explicit parameter
# passing — achieving cross-cutting observability cleanly.
_session_context: ContextVar[str] = ContextVar("session_id", default="")


def bind_session(session_id: str) -> None:
    """Bind a session_id to the current execution context."""
    _session_context.set(session_id)


def clear_session() -> None:
    """Clear the session_id from the current execution context."""
    _session_context.set("")


# ──────────────────────────────────────────────────────────────────
# Custom Structlog Processor
# ──────────────────────────────────────────────────────────────────
def _inject_session_id(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    """Structlog processor that injects session_id from contextvars."""
    session_id = _session_context.get("")
    if session_id:
        event_dict["session_id"] = session_id
    return event_dict


# ──────────────────────────────────────────────────────────────────
# Configuration Entry Point
# ──────────────────────────────────────────────────────────────────
def configure_structlog() -> None:
    """
    Configure structlog for the entire application.

    - Output: JSON lines to stdout (compatible with ELK, Datadog, etc.)
    - Processors: timestamp, log level, logger name, session_id injection
    - Integrates with stdlib logging so third-party libraries (uvicorn,
      httpx, etc.) also emit structured JSON.

    Call this ONCE at application startup (e.g., in main.py).
    """
    # Shared processors used by both structlog and stdlib logging
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_session_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib root logger to also output structured JSON
    # This ensures uvicorn, httpx, and other libraries produce JSON too.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
