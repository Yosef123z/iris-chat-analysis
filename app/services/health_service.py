"""Health and contract readiness reporting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import settings


class HealthCheckService:
    async def get_health_status(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": settings.APP_VERSION,
            "persistence": "in_memory_only",
            "components": {
                "business_knowledge_store": {"status": "up"},
                "session_memory": {"status": "up"},
                "analysis": {"status": "up"},
                "pii": {"status": "up"},
            },
        }

    def get_backend_integration_status(self) -> dict[str, Any]:
        return {
            "status": "ready",
            "mode": "signal_based_contract",
            "persistence": "in_memory_only",
            "backend_record_creation": "backend_owned",
            "routes": {
                "chat": True,
                "businessKnowledgeSync": True,
                "analysisChatBatch": True,
                "piiRemove": True,
            },
            "sideEffects": {
                "createsOrders": False,
                "createsTickets": False,
                "createsEscalations": False,
                "storesFeedback": False,
                "storesAnalysis": False,
            },
        }

    def get_metrics(self) -> dict[str, Any]:
        return {
            "runtime": "in_memory_only",
            "contract_mode": "signal_based",
            "version": settings.APP_VERSION,
        }
