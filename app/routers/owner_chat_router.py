"""Auxiliary owner analytics routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from app.core.provider import get_analytics_service, get_owner_chat_service
from app.models.owner_chat import (
    AnalyticsReportResponse,
    OwnerChatRequest,
    OwnerChatResponse,
    ReloadResponse,
)

if TYPE_CHECKING:
    from app.services.analytics_service import AnalyticsService
    from app.services.owner_chat_service import OwnerChatService

router = APIRouter(prefix="/api/v1/owner", tags=["Auxiliary Owner Analytics"])


@router.post("/chat", response_model=OwnerChatResponse)
async def owner_chat_endpoint(
    request: OwnerChatRequest,
    service: OwnerChatService = Depends(get_owner_chat_service),
):
    try:
        return await service.process_owner_message(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/report", response_model=AnalyticsReportResponse)
async def get_analytics_report(
    service: AnalyticsService = Depends(get_analytics_service),
):
    try:
        report_content = service.get_full_report()
        sections = service.get_available_sections()
        last_updated = service.last_loaded.isoformat() if service.last_loaded else None
        return AnalyticsReportResponse(
            report_content=report_content,
            sections_available=sections,
            last_updated=last_updated,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/reload", response_model=ReloadResponse)
async def reload_analytics(
    service: AnalyticsService = Depends(get_analytics_service),
):
    try:
        count = service.reload()
        return ReloadResponse(
            status="success" if count > 0 else "no_reports_found",
            reports_loaded=count,
            message=(
                f"Successfully reloaded {count} analytics report(s)."
                if count > 0
                else "No analytics report files found."
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
