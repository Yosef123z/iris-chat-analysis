"""Owner analytics routes — backend-driven report sync and owner chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from app.core.provider import get_owner_chat_service, get_owner_report_service
from app.models.owner_chat import (
    OwnerChatRequest,
    OwnerChatResponse,
    OwnerReportSyncRequest,
    OwnerReportSyncResponse,
)

if TYPE_CHECKING:
    from app.services.owner_chat_service import OwnerChatService
    from app.services.owner_report_service import OwnerReportService

router = APIRouter(prefix="/api/v1/owner", tags=["Owner Analytics"])


@router.post("/reports/sync", response_model=OwnerReportSyncResponse)
async def sync_owner_report(
    payload: OwnerReportSyncRequest,
    service: "OwnerReportService" = Depends(get_owner_report_service),
) -> OwnerReportSyncResponse:
    """Backend calls this to push the latest generated report for a business.

    The AI service stores the report under storage/owner_reports and uses it
    exclusively to answer owner chat questions for that business_id.
    """
    try:
        service.sync_report(payload)
        return OwnerReportSyncResponse(status="ok")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/chat", response_model=OwnerChatResponse)
async def owner_chat_endpoint(
    request: OwnerChatRequest,
    service: "OwnerChatService" = Depends(get_owner_chat_service),
) -> OwnerChatResponse:
    """Owner dashboard sends questions here.

    Requires a synced report for the given business_id.
    Returns a low-confidence safe reply when no report exists yet.
    """
    try:
        return await service.process_owner_message(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
