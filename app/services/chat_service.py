"""LLM-backed customer chat orchestration over synced business KB."""

from __future__ import annotations

import json
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core.llm_interface import AIProviderError, LLMProvider
from app.models.business_kb import BusinessMenuItem
from app.models.chat import (
    ChatRequest,
    ChatResponse,
    OrderDetails,
    OrderLineItem,
    TicketDetails,
)
from app.services.business_knowledge_service import (
    BusinessKnowledgeService,
    BusinessRetrievalContext,
)
from app.services.session_memory import SessionMemoryStore, SessionState


_SUPPORTED_TICKET_PRIORITIES = {"low", "normal", "high", "critical"}
_SUPPORTED_TICKET_CATEGORIES = {
    "complaint",
    "quality",
    "delivery",
    "payment",
    "wrong_order",
    "missing",
    "other",
}
_FORBIDDEN_REPLY_TERMS = {
    "backend",
    "api",
    "json",
    "contract",
    "rag",
    "vector",
    "embedding",
    "system prompt",
    "system",
    "prompt",
    "python",
    "module",
}


class CustomerChatLLMTicketDetails(BaseModel):
    subject: str = Field(default="Customer Support")
    description: str | None = None
    priority: str = "normal"
    category: str | None = "other"


class CustomerChatLLMOutput(BaseModel):
    reply: str
    order_detected: bool = False
    order_finalized: bool = False
    order_details: OrderDetails | None = None
    ticket_detected: bool = False
    ticket_details: CustomerChatLLMTicketDetails | None = None
    escalation_requested: bool = False
    feedback_requested: bool = False


class ChatService:
    """Generate customer replies and contract signals through grounded RAG."""

    def __init__(
        self,
        *,
        knowledge_service: BusinessKnowledgeService,
        memory_store: SessionMemoryStore,
        llm_provider: LLMProvider,
    ) -> None:
        self.knowledge = knowledge_service
        self.memory = memory_store
        self.llm_provider = llm_provider

    async def process_chat_message(self, request: ChatRequest) -> ChatResponse:
        start_time = time.time()
        kb = self.knowledge.get_business_kb(request.business_id)
        index = self.knowledge.get_business_index(request.business_id)
        if kb is None or index is None:
            return ChatResponse(
                session_id=request.session_id,
                reply=(
                    "معلش يا فندم، بيانات النشاط ده لسه مش متاحة عندي. "
                    "من فضلك ابعت بيانات النشاط الأول وبعدها أقدر أساعد حضرتك."
                ),
                processing_time_ms=self._elapsed_ms(start_time),
            )

        state = self.memory.get_or_create(request.session_id, request.business_id)
        try:
            context = await self.knowledge.retrieve_context(
                request.business_id,
                request.message,
                self.llm_provider.get_embeddings_model(),
            )
            if context is None:
                return ChatResponse(
                    session_id=request.session_id,
                    reply=(
                        "معلش يا فندم، بيانات النشاط ده لسه مش متاحة عندي. "
                        "من فضلك ابعت بيانات النشاط الأول وبعدها أقدر أساعد حضرتك."
                    ),
                    processing_time_ms=self._elapsed_ms(start_time),
                )
            llm_output = await self.llm_provider.structured_output(
                self._build_messages(request, state, context),
                model=settings.GPT_CHAT_MODEL,
                output_model=CustomerChatLLMOutput,
                temperature=0.2,
            )
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="AI response generation failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI response generation failed") from exc

        response = self._validate_output(request, state, llm_output, start_time)
        self.memory.append_turn(state, request.message, response.reply)
        if response.escalation_requested:
            state.handoff_active = True
        return response

    def _build_messages(
        self,
        request: ChatRequest,
        state: SessionState,
        context: BusinessRetrievalContext,
    ) -> list[dict[str, str]]:
        context_payload = {
            "business_id": context.business_id,
            "business_name": context.business_name,
            "retrieved_documents": [
                {
                    "id": item.document.id,
                    "type": item.document.type,
                    "title": item.document.title,
                    "content": item.document.content,
                    "metadata": item.document.metadata,
                }
                for item in context.documents
            ],
            "candidate_items": [
                self._item_payload(item)
                for item in context.candidate_items
            ],
            "relevant_faqs": [
                {
                    "question": faq.question,
                    "answer": faq.answer,
                    "is_faq": faq.is_faq,
                }
                for faq in context.relevant_faqs
            ],
            "current_cart": [
                item.model_dump()
                for item in state.cart_items
            ],
            "recent_history": state.messages[-10:],
            "latest_customer_message": request.message,
        }

        system_prompt = (
            "You are IRIS, a customer-facing digital employee for the specific business in the context. "
            "Use only the supplied business knowledge context for this business_id. Do not invent facts, prices, "
            "policies, products, services, or availability. If information is not present, politely say in natural "
            "Egyptian Arabic that it is not available from the business data. Reply in natural Egyptian Arabic by "
            "default unless the customer explicitly asks for another language. Be concise, warm, professional, and "
            "respectful. Never mention backend, API, contract, JSON, RAG, vector, embeddings, system, prompt, tools, "
            "or implementation details in the customer-facing reply. Treat menu_items as canonical sellable "
            "products/services/items for any business type. Use canonical item names exactly "
            "in order_details.items[].name. Do not translate canonical names. Respect is_available. Do not add or "
            "finalize unavailable or invented items. Suggest available alternatives when possible. Only finalize "
            "an order after explicit customer confirmation. order_finalized=true always implies order_detected=true. "
            "Return ticket, escalation, and feedback signals separately. Do not create any backend records."
        )
        output_schema = {
            "reply": "string",
            "order_detected": "boolean",
            "order_finalized": "boolean",
            "order_details": {
                "intent": "CreateOrder | ModifyOrder | CancelOrder | null",
                "items": [{"name": "canonical name", "quantity": 1, "price": 0, "notes": None}],
                "total_amount": 0,
            },
            "ticket_detected": "boolean",
            "ticket_details": {
                "subject": "string",
                "description": "string | null",
                "priority": "low | normal | high | critical",
                "category": "complaint | quality | delivery | payment | wrong_order | missing | other",
            },
            "escalation_requested": "boolean",
            "feedback_requested": "boolean",
        }
        user_prompt = (
            "Business/customer context follows. Return only one strict JSON object matching this schema.\n\n"
            f"SCHEMA:\n{json.dumps(output_schema, ensure_ascii=False)}\n\n"
            f"CONTEXT:\n{json.dumps(context_payload, ensure_ascii=False)}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _validate_output(
        self,
        request: ChatRequest,
        state: SessionState,
        output: CustomerChatLLMOutput,
        start_time: float,
    ) -> ChatResponse:
        sanitized_details, unavailable_items = self._sanitize_order_details(
            request.business_id,
            output.order_details,
        )

        order_detected = output.order_detected or sanitized_details is not None
        order_finalized = output.order_finalized

        if sanitized_details is not None:
            state.cart_items = list(sanitized_details.items)
        elif order_finalized and state.cart_items:
            sanitized_details = self._cart_details(state)
            order_detected = True

        if unavailable_items:
            order_finalized = False
            order_detected = True
            sanitized_details = sanitized_details or OrderDetails(
                intent="CreateOrder",
                items=[],
                total_amount=0,
            )

        if order_finalized:
            if sanitized_details is None or not sanitized_details.items:
                order_finalized = False
            else:
                order_detected = True

        if order_detected and sanitized_details is None and state.cart_items:
            sanitized_details = self._cart_details(state)

        ticket_details = self._sanitize_ticket(output.ticket_details) if output.ticket_detected else None
        reply = output.reply.strip()
        if unavailable_items:
            reply = self._unavailable_reply(request.business_id, unavailable_items)
        if not reply or self._reply_has_internal_terms(reply):
            reply = self._safe_reply(order_detected, output.ticket_detected, output.escalation_requested)

        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            order_detected=order_detected,
            order_finalized=order_finalized,
            order_details=sanitized_details if order_detected else None,
            ticket_detected=output.ticket_detected,
            ticket_details=ticket_details,
            escalation_requested=output.escalation_requested,
            feedback_requested=output.feedback_requested,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    def _sanitize_order_details(
        self,
        business_id: str,
        details: OrderDetails | None,
    ) -> tuple[OrderDetails | None, list[BusinessMenuItem]]:
        if details is None:
            return None, []

        sanitized_items: list[OrderLineItem] = []
        unavailable_items: list[BusinessMenuItem] = []
        for item in details.items:
            kb_item = self.knowledge.find_menu_item(business_id, item.name, min_score=70)
            if kb_item is None:
                continue
            if not kb_item.is_available:
                unavailable_items.append(kb_item)
                continue
            sanitized_items.append(
                OrderLineItem(
                    name=kb_item.name,
                    quantity=max(1, min(item.quantity, 50)),
                    price=kb_item.price,
                    notes=item.notes,
                )
            )

        merged = self._merge_duplicate_items(sanitized_items)
        if not merged and not unavailable_items:
            return None, []
        total = sum(item.quantity * item.price for item in merged)
        return (
            OrderDetails(
                intent=details.intent or "CreateOrder",
                items=merged,
                total_amount=total,
            ),
            unavailable_items,
        )

    @staticmethod
    def _merge_duplicate_items(items: list[OrderLineItem]) -> list[OrderLineItem]:
        merged: list[OrderLineItem] = []
        for item in items:
            for existing in merged:
                if existing.name == item.name and existing.notes == item.notes:
                    existing.quantity += item.quantity
                    existing.price = item.price
                    break
            else:
                merged.append(item)
        return merged

    @staticmethod
    def _sanitize_ticket(details: CustomerChatLLMTicketDetails | None) -> TicketDetails | None:
        if details is None:
            return TicketDetails(subject="Customer Support", priority="normal", category="other")
        priority = details.priority if details.priority in _SUPPORTED_TICKET_PRIORITIES else "normal"
        category = details.category if details.category in _SUPPORTED_TICKET_CATEGORIES else "other"
        return TicketDetails(
            subject=details.subject or "Customer Support",
            description=details.description,
            priority=priority,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
        )

    def _unavailable_reply(self, business_id: str, unavailable_items: list[BusinessMenuItem]) -> str:
        names = "، ".join(item.name for item in unavailable_items)
        alternatives: list[BusinessMenuItem] = []
        for item in unavailable_items:
            alternatives.extend(self.knowledge.alternatives_for(business_id, item))
        unique_alternatives: list[BusinessMenuItem] = []
        for item in alternatives:
            if item.name not in {existing.name for existing in unique_alternatives}:
                unique_alternatives.append(item)
        if unique_alternatives:
            alt_text = self.knowledge.summarize_items(unique_alternatives[:3])
            return f"معلش يا فندم، {names} مش متاح حاليًا. المتاح بدلًا منه: {alt_text}."
        return f"معلش يا فندم، {names} مش متاح حاليًا."

    @staticmethod
    def _safe_reply(
        order_detected: bool,
        ticket_detected: bool,
        escalation_requested: bool,
    ) -> str:
        if escalation_requested:
            return "تمام يا فندم، هنوصل طلب حضرتك لفريق الدعم عشان يتابع معاك."
        if ticket_detected:
            return "معلش يا فندم، هنسجل المشكلة لفريق الدعم عشان يتابعها."
        if order_detected:
            return "تمام يا فندم، سجلت طلب حضرتك. تحب تضيف حاجة تانية؟"
        return "معلش يا فندم، المعلومة دي مش متاحة عندي حاليًا من بيانات النشاط."

    @staticmethod
    def _reply_has_internal_terms(reply: str) -> bool:
        normalized = reply.lower()
        return any(term in normalized for term in _FORBIDDEN_REPLY_TERMS)

    @staticmethod
    def _cart_details(state: SessionState) -> OrderDetails:
        total = sum(item.quantity * item.price for item in state.cart_items)
        return OrderDetails(intent="CreateOrder", items=list(state.cart_items), total_amount=total)

    @staticmethod
    def _item_payload(item: BusinessMenuItem) -> dict[str, object]:
        return {
            "menu_item_id": item.menu_item_id,
            "name": item.name,
            "description": item.description,
            "price": item.price,
            "category": item.category,
            "is_available": item.is_available,
        }

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.time() - start) * 1000)
