"""Chat and business knowledge-base contract routes."""

from fastapi import APIRouter, Depends, Request

from app.core.provider import get_business_knowledge_service, get_chat_service
from app.core.rate_limiter import limiter
from app.models.business_kb import (
    BusinessKnowledgeSyncRequest,
    BusinessKnowledgeSyncResponse,
)
from app.models.chat import ChatRequest, ChatResponse
from app.services.business_knowledge_service import BusinessKnowledgeService
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api/v1", tags=["Backend Contract"])


@router.post(
    "/business/knowledge-base/sync",
    response_model=BusinessKnowledgeSyncResponse,
)
@limiter.limit("120/minute")
async def sync_business_knowledge_base(
    request: Request,
    payload: BusinessKnowledgeSyncRequest,
    service: BusinessKnowledgeService = Depends(get_business_knowledge_service),
):
    del request
    service.sync_business_kb(payload)
    return BusinessKnowledgeSyncResponse()


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat_endpoint(
    request: Request,
    chat_request: ChatRequest,
    chat_svc: ChatService = Depends(get_chat_service),
):
    del request
    return await chat_svc.process_chat_message(chat_request)
