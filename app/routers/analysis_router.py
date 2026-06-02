"""Analysis contract routes."""

from fastapi import APIRouter, Depends, Request

from app.core.provider import get_chat_batch_analysis_service, get_pii_service
from app.core.rate_limiter import limiter
from app.models.analysis import (
    ChatBatchAnalysisRequest,
    ChatBatchAnalysisResponse,
    PIIRemoveRequest,
    PIIRemoveResult,
)
from app.services.chat_batch_analysis_service import ChatBatchAnalysisService
from app.services.pii_service import PIIService

router = APIRouter(prefix="/api/v1/analysis", tags=["Analysis Contract"])


@router.post("/chat-batch", response_model=ChatBatchAnalysisResponse)
@limiter.limit("120/minute")
async def chat_batch_endpoint(
    request: Request,
    payload: ChatBatchAnalysisRequest,
    service: ChatBatchAnalysisService = Depends(get_chat_batch_analysis_service),
):
    del request
    return service.analyze(payload)


@router.post("/pii-remove", response_model=PIIRemoveResult)
@limiter.limit("120/minute")
async def pii_remove_endpoint(
    request: Request,
    payload: PIIRemoveRequest,
    service: PIIService = Depends(get_pii_service),
):
    del request
    return service.remove_pii(payload.text)
