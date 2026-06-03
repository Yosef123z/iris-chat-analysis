"""LLM-backed customer chat orchestration over synced business KB."""

from __future__ import annotations

import json
import re
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
    normalize_text,
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
_FINALIZATION_CUES = {
    "أكد الطلب",
    "اكد الطلب",
    "تمام أكد",
    "تمام اكد",
    "كده تمام",
    "كدة تمام",
    "بس كده",
    "بس كدة",
    "وبس كده",
    "و بس كدة",
    "خلاص كده",
    "خلاص كدة",
    "ايوه خلاص",
    "أيوه خلاص",
    "هو ده الطلب",
    "هو دا الطلب",
    "لا كده تمام",
    "لا كدة تمام",
    "تمام كده",
    "تمام كدة",
    "كفاية كده",
    "كفاية كدة",
    "confirm order",
    "that's all",
    "that is all",
    "yes that's it",
    "yes confirm",
    "confirm it",
}
_CANCEL_CUES = {
    "الغى الاوردر",
    "كنسل الاوردر",
    "إلغاء الأوردر",
    "كنسل الأوردر",
    "الغي الاوردر",
    "ألغي الأوردر",
    "الغى الطلب",
    "كنسل الطلب",
    "إلغاء الطلب",
    "كنسلة الطلب",
    "الغي الطلب",
    "ألغي الطلب",
    "عايز الغي",
    "عايز اكنسل",
    "عايز ألغى",
    "عايز ألغي",
    "خلاص الغي",
    "خلاص كنسل",
    "خلاص ألغي",
    "بلاش الطلب",
    "مش عايز الطلب",
    "مش عاوز الطلب",
    "cancel order",
    "cancel it",
    "cancel my order",
    "forget the order",
}
_INFORMATIONAL_CUES = {
    "قولي تفاصيل",
    "قوللي تفاصيل",
    "ايه تفاصيل",
    "إيه تفاصيل",
    "بكام",
    "سعر",
    "متاح",
    "عندكم ايه",
    "عندكم إيه",
    "ايه المتاح",
    "إيه المتاح",
    "قوللي عن",
    "قولي عن",
    "معلومات عن",
    "ينفع اعرف",
    "ممكن اعرف",
    "tell me about",
    "details",
    "price",
    "how much",
    "what do you have",
    "available",
    "information about",
}
_ORDER_ACTION_CUES = {
    "عايز أطلب",
    "عايز اطلب",
    "اطلبلي",
    "ضيف",
    "ضف",
    "حط",
    "زود",
    "خدلي",
    "احجزلي",
    "احجز",
    "عايز أحجز",
    "عايز احجز",
    "order",
    "add",
    "book",
    "reserve",
    "put",
    "i want",
}
_ESCALATION_CUES = {
    "عايز أكلم المدير",
    "عايز اكلم المدير",
    "كلم المدير",
    "اكلم حد من الإدارة",
    "اكلم حد من الادارة",
    "عايز حد من الإدارة",
    "عايز حد من الادارة",
    "حد من الإدارة",
    "حد من الادارة",
    "خدمة العملاء",
    "عايز أكلم موظف",
    "عايز اكلم موظف",
    "حولني لموظف",
    "حوّلني لموظف",
    "عايز إنسان",
    "عايز انسان",
    "عايز حد يكلمني",
    "manager",
    "human agent",
    "customer support",
    "representative",
    "talk to someone",
    "speak to a person",
}
_COMPLAINT_CUES = {
    "الأوردر وصل بارد",
    "الاوردر وصل بارد",
    "الأوردر وصل متأخر",
    "الاوردر وصل متأخر",
    "الخدمة سيئة",
    "وصل بارد",
    "وصل غلط",
    "الأوردر غلط",
    "الاوردر غلط",
    "ناقص",
    "اتأخر",
    "تأخير",
    "مش كويس",
    "وحش",
    "مشكلة",
    "زعلان",
    "مضايق",
    "مش عاجبني",
    "wrong order",
    "cold",
    "missing",
    "late",
    "bad",
    "complaint",
    "problem",
    "upset",
}
_INFORMATIONAL_ORDER_REPLY_CUES = {
    "أضيفها لطلبك",
    "أضيفه لطلبك",
    "أضيفها للطلب",
    "أضيفه للطلب",
    "أضيفها للأوردر",
    "أضيفه للأوردر",
    "تحب أضيف",
    "تأكيد الطلب",
    "تأكد الطلب",
}
_DIALECT_REPLACEMENTS = (
    ("هل ترغب في تأكيد الطلب؟", "تحب تأكد الطلب؟"),
    ("هل ترغب في إضافة أي شيء آخر؟", "تحب تضيف حاجة تانية؟"),
    ("هل ترغب في إضافة أي شئ آخر؟", "تحب تضيف حاجة تانية؟"),
    ("هل ترغب", "تحب"),
    ("عذرًا", "معلش"),
    ("عذراً", "معلش"),
    ("غير متوفر حاليًا", "مش متاح دلوقتي"),
    ("غير متوفر حالياً", "مش متاح دلوقتي"),
    ("متوفر حاليًا", "متاح دلوقتي"),
    ("متوفر حالياً", "متاح دلوقتي"),
    ("تم إضافة", "ضفت"),
    ("تمت إضافة", "ضفت"),
    ("يريد التحدث إلى المدير", "عايز يكلم المدير"),
    ("يريد التحدث", "عايز يتكلم"),
    ("تقديم شكوى", "تسجيل المشكلة"),
    ("استفسار", "سؤال"),
    ("شيء آخر", "حاجة تانية"),
    ("شيئ آخر", "حاجة تانية"),
    ("حاليًا", "دلوقتي"),
    ("حالياً", "دلوقتي"),
)


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
            "respectful. Do not use Modern Standard Arabic phrases like 'هل ترغب', 'عذرًا', 'غير متوفر حاليًا', "
            "'تم إضافة', 'يريد', 'تقديم شكوى', 'استفسار', or 'شيء آخر'. Prefer natural Egyptian phrasing like "
            "'تحب', 'معلش', 'مش متاح دلوقتي', 'ضفت', 'عايز', 'هسجل المشكلة', 'سؤال', and 'حاجة تانية'. "
            "Never mention backend, API, contract, JSON, RAG, vector, embeddings, system, prompt, tools, "
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

        informational_only = self._is_informational_query(request.message)
        finalization_requested = self._has_finalization_cue(request.message)
        cancel_requested = self._has_cancel_cue(request.message)
        complaint_detected = self._has_complaint_cue(request.message)
        escalation_detected = self._has_escalation_cue(request.message)

        if sanitized_details is None and output.order_detected:
            unavailable_items = self._unavailable_items_from_message(
                request.business_id,
                request.message,
            )

        if informational_only and not unavailable_items:
            sanitized_details = None

        order_detected = output.order_detected or sanitized_details is not None or bool(unavailable_items)
        order_finalized = output.order_finalized

        if sanitized_details is not None:
            sanitized_details = self._merge_with_existing_cart(state, sanitized_details)
            if sanitized_details.items:
                state.cart_items = list(sanitized_details.items)
        elif order_finalized and state.cart_items:
            sanitized_details = self._cart_details(state)
            order_detected = True

        if informational_only and not unavailable_items:
            order_detected = False
            order_finalized = False
            sanitized_details = None

        if unavailable_items:
            order_finalized = False
            order_detected = True
            sanitized_details = OrderDetails(
                intent="CreateOrder",
                items=[] if sanitized_details is None else list(sanitized_details.items),
                total_amount=0 if sanitized_details is None else sanitized_details.total_amount,
            )

        if finalization_requested:
            if sanitized_details is None and state.cart_items:
                sanitized_details = self._cart_details(state)
                order_detected = True
            if sanitized_details is not None and sanitized_details.items:
                order_detected = True
                order_finalized = True
            elif not unavailable_items and not informational_only:
                order_detected = False
                order_finalized = False

        if order_finalized:
            if sanitized_details is None or not sanitized_details.items:
                order_finalized = False
            else:
                order_detected = True

        if order_detected and sanitized_details is None and state.cart_items:
            sanitized_details = self._cart_details(state)

        if cancel_requested:
            state.cart_items = []
            order_detected = False
            order_finalized = False
            sanitized_details = None

        ticket_detected = output.ticket_detected
        escalation_requested = output.escalation_requested
        ticket_details = self._sanitize_ticket(output.ticket_details) if ticket_detected else None
        if escalation_detected and not complaint_detected:
            ticket_detected = False
            ticket_details = None
            escalation_requested = True
        elif complaint_detected:
            ticket_detected = True
            escalation_requested = escalation_requested or escalation_detected
            ticket_details = self._complaint_ticket_details(
                request.message,
                high_priority=escalation_requested,
                fallback=ticket_details,
            )

        reply = self._sanitize_dialect(output.reply.strip())
        if cancel_requested:
            reply = self._cancel_reply(
                complaint_detected=complaint_detected,
                escalation_detected=escalation_detected,
            )
        elif unavailable_items:
            reply = self._unavailable_reply(request.business_id, unavailable_items)
        elif order_finalized and finalization_requested:
            reply = "تمام يا فندم، كده الطلب اتأكد. هنبدأ نجهزه لحضرتك."
        elif escalation_detected and complaint_detected:
            reply = (
                "معلش جدًا يا فندم على اللي حصل، هسجل المشكلة فورًا "
                "وهحوّل حضرتك لحد من الإدارة يتابع معاك."
            )
        elif escalation_detected:
            reply = "تمام يا فندم، هحوّل حضرتك لحد من الإدارة يتابع معاك."
        elif complaint_detected:
            reply = "معلش يا فندم، هسجل المشكلة لفريق الدعم عشان يتابعوها."
        elif finalization_requested and not order_finalized:
            reply = "تمام يا فندم، اختار الحاجة اللي تحب تطلبها الأول وأنا أساعدك."
        if not reply or self._reply_has_internal_terms(reply):
            reply = self._safe_reply(order_detected, ticket_detected, escalation_requested)
        reply = self._sanitize_dialect(reply)
        if informational_only and not unavailable_items and not cancel_requested:
            reply = self._sanitize_informational_reply(reply)

        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            order_detected=order_detected,
            order_finalized=order_finalized,
            order_details=sanitized_details if order_detected else None,
            ticket_detected=ticket_detected,
            ticket_details=ticket_details,
            escalation_requested=escalation_requested,
            feedback_requested=output.feedback_requested,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    @staticmethod
    def _has_any_cue(message: str, cues: set[str]) -> bool:
        normalized = normalize_text(message)
        return any(normalize_text(cue) in normalized for cue in cues)

    @classmethod
    def _has_finalization_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _FINALIZATION_CUES)

    @classmethod
    def _has_cancel_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _CANCEL_CUES)

    @classmethod
    def _has_complaint_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _COMPLAINT_CUES)

    @classmethod
    def _has_escalation_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _ESCALATION_CUES)

    @classmethod
    def _is_informational_query(cls, message: str) -> bool:
        if not cls._has_any_cue(message, _INFORMATIONAL_CUES):
            return False
        return not cls._has_any_cue(message, _ORDER_ACTION_CUES)

    def _unavailable_items_from_message(
        self,
        business_id: str,
        message: str,
    ) -> list[BusinessMenuItem]:
        item = self.knowledge.find_menu_item(business_id, message, min_score=70)
        if item is None or item.is_available:
            return []
        return [item]

    @staticmethod
    def _merge_with_existing_cart(state: SessionState, details: OrderDetails) -> OrderDetails:
        if not state.cart_items or not details.items:
            return details

        detail_names = {item.name for item in details.items}
        existing_names = {item.name for item in state.cart_items}
        if existing_names.issubset(detail_names):
            merged = ChatService._merge_duplicate_items(list(details.items))
        else:
            merged = ChatService._merge_duplicate_items([*state.cart_items, *details.items])

        return OrderDetails(
            intent=details.intent or "CreateOrder",
            items=merged,
            total_amount=sum(item.quantity * item.price for item in merged),
        )

    @staticmethod
    def _complaint_ticket_details(
        message: str,
        *,
        high_priority: bool,
        fallback: TicketDetails | None,
    ) -> TicketDetails:
        category = ChatService._complaint_category(message)
        priority = "high" if high_priority else (fallback.priority if fallback else "normal")
        if priority not in _SUPPORTED_TICKET_PRIORITIES:
            priority = "normal"
        if high_priority and priority in {"low", "normal"}:
            priority = "high"
        return TicketDetails(
            subject=fallback.subject if fallback else "شكوى عميل",
            description=fallback.description if fallback and fallback.description else message,
            priority=priority,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
        )

    @staticmethod
    def _complaint_category(message: str) -> str:
        normalized = normalize_text(message)
        if any(normalize_text(term) in normalized for term in {"غلط", "wrong order"}):
            return "wrong_order"
        if any(normalize_text(term) in normalized for term in {"ناقص", "missing"}):
            return "missing"
        if any(normalize_text(term) in normalized for term in {"متأخر", "اتأخر", "تأخير", "late"}):
            return "delivery"
        if any(normalize_text(term) in normalized for term in {"بارد", "cold", "وحش", "bad"}):
            return "quality"
        return "complaint"

    @staticmethod
    def _sanitize_dialect(text: str) -> str:
        sanitized = text
        for source, target in _DIALECT_REPLACEMENTS:
            sanitized = sanitized.replace(source, target)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized

    @staticmethod
    def _cancel_reply(*, complaint_detected: bool, escalation_detected: bool) -> str:
        if complaint_detected and escalation_detected:
            return (
                "تمام يا فندم، لغيتلك الطلب وهسجل المشكلة فورًا "
                "وهحوّل حضرتك لحد من الإدارة يتابع معاك."
            )
        if escalation_detected:
            return "تمام يا فندم، لغيتلك الطلب وهحوّل حضرتك لحد من الإدارة يتابع معاك."
        if complaint_detected:
            return "تمام يا فندم، لغيتلك الطلب وهسجل المشكلة لفريق الدعم عشان يتابعوها."
        return "تمام يا فندم، لغيتلك الطلب. لو احتجت أي حاجة تانية أنا معاك."

    @staticmethod
    def _sanitize_informational_reply(reply: str) -> str:
        if not ChatService._has_any_cue(reply, _INFORMATIONAL_ORDER_REPLY_CUES):
            return reply

        sentences = re.split(r"(?<=[.!?؟])\s+", reply)
        kept = [
            sentence.strip()
            for sentence in sentences
            if sentence.strip()
            and not ChatService._has_any_cue(sentence, _INFORMATIONAL_ORDER_REPLY_CUES)
        ]
        if kept:
            return " ".join(kept)

        sanitized = reply
        for cue in sorted(_INFORMATIONAL_ORDER_REPLY_CUES, key=len, reverse=True):
            sanitized = re.sub(
                rf"\s*[^.!?؟]*{re.escape(cue)}[^.!?؟]*[!?؟]?",
                "",
                sanitized,
            )
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized or "لو تحب تعرف تفاصيل أكتر، قولي."

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
