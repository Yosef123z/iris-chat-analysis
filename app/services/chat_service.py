"""LLM-backed customer chat orchestration over synced business KB."""

from __future__ import annotations

import json
import re
import time
import unicodedata

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
    "database",
    "validation layer",
    "internal tools",
    "retrieval",
}
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u0870-\u089F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_MOJIBAKE_MARKERS = ("Ø", "Ù", "â", "Ã", "�")
_LEADING_REPLY_LABEL_RE = re.compile(
    r"^\s*(?:assistant|ai|iris|reply|response|answer|message)\s*[:：\-]\s*",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(r"```(?:json|text|markdown|md)?\s*|\s*```", re.IGNORECASE)
_CONTROL_FORMAT_CATEGORIES = {"Cc", "Cf"}
_DECORATIVE_SYMBOLS_RE = re.compile(r"[#*_~`]{2,}")
_REPEATED_PUNCTUATION_RE = re.compile(r"([!?؟،,.])\1{1,}")


def contains_arabic(text: str) -> bool:
    return bool(_ARABIC_CHAR_RE.search(text or ""))


def detect_customer_language(text: str) -> str:
    return "ar" if contains_arabic(text) else "en"


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
    "تمام كدا",
    "خلاص تمام",
    "كفاية كده",
    "كفاية كدة",
    "لا شكرا",
    "لا شكرًا",
    "لا مش عايز",
    "لا مش عاوز",
    "مش عايز حاجة تانية",
    "مش عاوز حاجة تانية",
    "مش محتاج حاجة تانية",
    "confirm order",
    "that's all",
    "that is all",
    "that's it",
    "yes that's it",
    "yes confirm",
    "confirm it",
    "no thanks",
    "no thank you",
    "nothing else",
    "all good",
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
_FULFILLMENT_PREFERENCE_CUES = {
    "في المطعم",
    "اكل في المطعم",
    "آكل في المطعم",
    "اكل هناك",
    "آكل هناك",
    "هنا",
    "takeaway",
    "تيك اواي",
    "تيك أواي",
    "اخده",
    "هاخده",
    "خارج",
    "dine in",
    "eat in",
    "for here",
    "to go",
    "take out",
    "takeout",
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
_OPEN_ORDER_CONFIRMATION_REPLY_CUES = {
    "تأكيد الطلب",
    "تأكد الطلب",
    "تأكيد الأوردر",
    "تأكد الأوردر",
    "أسجل الطلب",
    "اسجل الطلب",
    "أسجل الأوردر",
    "اسجل الأوردر",
    "متأكد",
    "متأكدة",
    "confirm the order",
    "confirm your order",
    "would you like to confirm",
    "do you want to confirm",
    "are you sure",
    "shall i place",
    "should i place",
    "place the order",
    "submit the order",
}
_OPEN_ORDER_FOLLOWUP_CUES = {
    "حاجة تانية",
    "تضيف",
    "تزود",
    "كمان",
    "معاه",
    "معاها",
    "anything else",
    "something else",
    "add anything",
    "add something",
    "with it",
    "with that",
}
_FULFILLMENT_QUESTION_REPLY_CUES = {
    "في المطعم",
    "تاكل في المطعم",
    "تاكله في المطعم",
    "تاكلي في المطعم",
    "تاكليه في المطعم",
    "takeaway",
    "تيك اواي",
    "تيك أواي",
    "تاخده",
    "تاخديه",
    "توصيل",
    "توصله",
    "توصليه",
    "يتوصلك",
    "يوصلك",
    "للبيت",
    "على البيت",
    "delivery",
    "deliver",
    "home delivery",
    "dine in",
    "eat in",
    "for here",
    "to go",
    "take out",
    "takeout",
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
        customer_language = detect_customer_language(request.message)
        kb = self.knowledge.get_business_kb(request.business_id)
        index = self.knowledge.get_business_index(request.business_id)
        if kb is None or index is None:
            return ChatResponse(
                session_id=request.session_id,
                reply=self._missing_business_data_reply(customer_language),
                processing_time_ms=self._elapsed_ms(start_time),
            )

        state = self.memory.get_or_create(request.session_id, request.business_id)
        if state.awaiting_fulfillment_preference and self._has_fulfillment_preference_cue(request.message):
            state.awaiting_fulfillment_preference = False
            fulfillment_language = (
                "ar"
                if customer_language == "en"
                and any(contains_arabic(turn.get("text", "")) for turn in state.messages[-6:])
                else customer_language
            )
            reply = self._fulfillment_preference_reply(fulfillment_language)
            self.memory.append_turn(state, request.message, reply)
            return ChatResponse(
                session_id=request.session_id,
                reply=reply,
                processing_time_ms=self._elapsed_ms(start_time),
            )

        try:
            context = await self.knowledge.retrieve_context(
                request.business_id,
                request.message,
                self.llm_provider.get_embeddings_model(),
            )
            if context is None:
                return ChatResponse(
                    session_id=request.session_id,
                    reply=self._missing_business_data_reply(customer_language),
                    processing_time_ms=self._elapsed_ms(start_time),
                )
            llm_output = await self.llm_provider.structured_output(
                self._build_messages(request, state, context, customer_language),
                model=settings.GPT_CHAT_MODEL,
                output_model=CustomerChatLLMOutput,
                temperature=0.2,
            )
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="AI response generation failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI response generation failed") from exc

        response = self._validate_output(request, state, llm_output, start_time, customer_language)
        self.memory.append_turn(state, request.message, response.reply)
        if response.escalation_requested:
            state.handoff_active = True
        return response

    def _build_messages(
        self,
        request: ChatRequest,
        state: SessionState,
        context: BusinessRetrievalContext,
        customer_language: str,
    ) -> list[dict[str, str]]:
        if customer_language == "ar":
            language_instruction = (
                "The latest customer message contains Arabic text. Reply in natural Egyptian Arabic only, "
                "like a professional Egyptian customer service employee. Do not use stiff Modern Standard Arabic. "
                "Do not use Modern Standard Arabic phrases like 'هل ترغب', 'عذرًا', 'غير متوفر حاليًا', "
                "'تم إضافة', 'يريد', 'تقديم شكوى', 'استفسار', or 'شيء آخر'. Prefer natural Egyptian phrasing like "
                "'تحب', 'معلش', 'مش متاح دلوقتي', 'ضفت', 'عايز', 'هسجل المشكلة', 'سؤال', and 'حاجة تانية'. "
            )
            not_found_language = "natural Egyptian Arabic"
        else:
            language_instruction = (
                "The latest customer message is entirely English. Reply in polished, natural customer-service "
                "English only. Do not use Arabic, and do not translate the customer's text back to Arabic."
            )
            not_found_language = "natural customer-service English"

        context_payload = {
            "business_id": context.business_id,
            "business_name": context.business_name,
            "detected_customer_language": customer_language,
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
            "You are IRIS, a professional restaurant customer-service assistant. "
            "Answer the customer using only the provided Business Knowledge Base context. "
            "The Business Knowledge Base may contain menu items, prices, availability, FAQs, restaurant policies, "
            "delivery information, offers, branches, working hours, and other restaurant data provided by the backend. "
            "Do not use general knowledge. "
            "Do not invent products, prices, offers, policies, availability, working hours, delivery rules, branches, or restaurant facts. "
            f"If the information is not available in the provided KB context, politely say in {not_found_language} "
            "that the information is not currently available. "
            "Never mention internal system details such as backend, API, prompt, system prompt, RAG, embeddings, "
            "vector search, retrieval, validation layer, JSON contract, database, or implementation details. "
            "Be friendly, professional, natural, concise, and helpful. "
            "You are a customer-facing digital employee for the restaurant or cafe in the context. "
            "Sound like a calm, smart, professional human restaurant/cafe customer service agent: helpful, respectful, "
            "situation-aware, and concise. "
            f"{language_instruction} Be concise, warm, professional, and respectful. "
            "The reply field must contain plain, natural human text only. Do NOT use quotes (\"\") or backslashes around menu item names. "
            "When listing menu items, use a simple inline dash format exactly like this: '- Item: description. - Item: description.' "
            "Do not use code fences, markdown tables, raw JSON, assistant labels, emojis, decorative symbols, repeated punctuation, or strange characters. "
            "Be logically aware that the customer is ordering from a restaurant/cafe context. Do not ask whether "
            "the customer wants delivery, home delivery, or the order delivered to their house unless they explicitly "
            "ask about delivery information. "
            "Treat menu_items as canonical restaurant/cafe menu items. Use canonical item names exactly "
            "in order_details.items[].name. Do not translate canonical names. Respect is_available. Do not add or "
            "finalize unavailable or invented items. Suggest available alternatives when possible. Only finalize "
            "When a customer orders items, behave exactly like a real professional waiter or cafe cashier. "
            "Acknowledge the item warmly and naturally (e.g. 'Perfect, added to your order'), keep the cart open, "
            "and optionally suggest one relevant available add-on from the retrieved menu. "
            "Do NOT ask for confirmation before every item. Do NOT say 'are you sure?'. Do NOT ask the customer "
            "to 'confirm the order' or 'would you like to place the order' after every message. "
            "Set order_finalized=true naturally when the customer's intent is clearly done: they say something like "
            "'that's all', 'no thanks', 'بس كده', 'كده تمام', 'خلاص', 'بس', 'لا شكرا', or simply stop adding items "
            "and indicate they are done. You do NOT need an explicit 'confirm' keyword — use natural human judgment. "
            "After order_finalized=true, ask once whether they will dine in or take away. Do not call takeaway delivery. "
            "Egyptian Arabic replies should be fluent, logical, and professional, like a real Egyptian customer service "
            "employee speaking naturally — not a rigid script. English replies should be equally natural and professional. "
            "order_finalized=true always implies order_detected=true. "
            "If the customer is reporting an issue or complaining about a previous order, do NOT set order_detected=true or order_finalized=true. Handle the complaint naturally and empathetically. Only set ticket_detected=true. "
            "When responding to a complaint, do NOT ask unnecessary questions like 'Can you give me more details about the order?'. Instead, assure them you will log the issue and follow up, and ask if they would like to speak to management. "
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
        customer_language: str,
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

        if ticket_detected or escalation_requested:
            state.cart_items = []
            order_detected = False
            order_finalized = False
            sanitized_details = None

        reply = self._sanitize_reply_text(output.reply)
        if cancel_requested:
            reply = self._cancel_reply(
                customer_language,
                complaint_detected=complaint_detected,
                escalation_detected=escalation_detected,
            )
        elif unavailable_items:
            reply = self._unavailable_reply(request.business_id, unavailable_items, customer_language)
        elif order_finalized:
            # Trust the LLM's natural judgment — use the LLM's reply unless it lacks
            # the dine-in/takeaway question, in which case append it.
            if not ChatService._has_any_cue(reply, _FULFILLMENT_QUESTION_REPLY_CUES):
                reply = self._order_finalized_reply(customer_language)
        elif escalation_detected and complaint_detected:
            reply = self._complaint_escalation_reply(customer_language)
        elif escalation_detected:
            reply = self._escalation_reply(customer_language)
        elif complaint_detected:
            reply = self._complaint_reply(customer_language)
        elif finalization_requested and not order_finalized:
            reply = self._empty_finalization_reply(customer_language)

        reply = self._sanitize_reply_text(reply)
        if order_detected and not order_finalized and not finalization_requested and not informational_only:
            reply = self._sanitize_open_order_reply(reply, customer_language)
            reply = self._sanitize_reply_text(reply)
        if not reply or self._reply_has_internal_terms(reply):
            reply = self._safe_reply(customer_language, order_detected, ticket_detected, escalation_requested)
        reply = self._sanitize_reply_text(reply)
        if informational_only and not unavailable_items and not cancel_requested:
            reply = self._sanitize_informational_reply(reply, customer_language)
            reply = self._sanitize_reply_text(reply)
            
        if customer_language == "en":
            reply = ChatService._sanitize_english_apologies(reply)
            
        reply = self._ensure_natural_reply_direction(reply, customer_language)
        if order_finalized:
            state.awaiting_fulfillment_preference = True
        elif cancel_requested:
            state.awaiting_fulfillment_preference = False

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
        if cls._has_any_cue(message, _COMPLAINT_CUES):
            return True
        normalized = normalize_text(message)
        if "wrong" in normalized and any(word in normalized for word in {"order", "item", "product", "food"}):
            return True
        return False

    @classmethod
    def _has_escalation_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _ESCALATION_CUES)

    @classmethod
    def _has_fulfillment_preference_cue(cls, message: str) -> bool:
        return cls._has_any_cue(message, _FULFILLMENT_PREFERENCE_CUES)

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
        if any(normalize_text(term) in normalized for term in {"غلط", "wrong order"}) or ("wrong" in normalized and any(word in normalized for word in {"order", "item", "product", "food"})):
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
    def _sanitize_reply_text(text: str) -> str:
        sanitized = ChatService._repair_obvious_mojibake(text or "")
        sanitized = unicodedata.normalize("NFKC", sanitized)
        sanitized = _LEADING_REPLY_LABEL_RE.sub("", sanitized)
        sanitized = sanitized.replace("\\r\\n", "\n").replace("\\n", "\n")
        sanitized = _CODE_FENCE_RE.sub("", sanitized)
        sanitized = ChatService._unwrap_reply_json(sanitized)
        sanitized = re.sub(r"^\s*[-•·]+\s*", "", sanitized)
        sanitized = sanitized.replace("\uFFFD", "")
        sanitized = sanitized.replace("—", "-").replace("–", "-")
        sanitized = sanitized.replace('"', '').replace("\\", "")
        sanitized = sanitized.replace("“", '').replace("”", '')
        sanitized = sanitized.replace("‘", "'").replace("’", "'")
        sanitized = _DECORATIVE_SYMBOLS_RE.sub("", sanitized)
        sanitized = _REPEATED_PUNCTUATION_RE.sub(r"\1", sanitized)
        sanitized = "".join(
            char
            for char in sanitized
            if unicodedata.category(char) not in _CONTROL_FORMAT_CATEGORIES
        )
        sanitized = ChatService._remove_unusual_symbols(sanitized)
        sanitized = re.sub(r"\s+([,.!?؟،:;])", r"\1", sanitized)
        sanitized = re.sub(r"([(\[{])\s+", r"\1", sanitized)
        sanitized = re.sub(r"\s+([)\]}])", r"\1", sanitized)
        sanitized = re.sub(r"\s+", " ", sanitized).strip(" \t\r\n:-")
        return ChatService._sanitize_dialect(sanitized)

    @staticmethod
    def _repair_obvious_mojibake(text: str) -> str:
        marker_count = sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)
        if marker_count < 2:
            return text
        latin1_source = "".join(char for char in text if ord(char) <= 255)
        try:
            repaired = latin1_source.encode("latin1").decode("utf-8")
        except UnicodeError:
            try:
                repaired = text.encode("cp1252", errors="ignore").decode("utf-8")
            except UnicodeError:
                return text
        if contains_arabic(repaired) or repaired.count("�") < text.count("�"):
            return repaired
        return text

    @staticmethod
    def _unwrap_reply_json(text: str) -> str:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            return text
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        reply = payload.get("reply") if isinstance(payload, dict) else None
        return reply if isinstance(reply, str) and reply.strip() else text

    @staticmethod
    def _remove_unusual_symbols(text: str) -> str:
        kept: list[str] = []
        for char in text:
            category = unicodedata.category(char)
            if category in {"So", "Sk"}:
                continue
            kept.append(char)
        return "".join(kept)

    @staticmethod
    def _sanitize_english_apologies(reply: str) -> str:
        pattern = re.compile(r"(?i)\b(?:maalish|maalesh|ma'alesh|malesh)\b")
        sanitized = pattern.sub("Sorry", reply)
        sanitized = sanitized.replace("معلش", "Sorry")
        
        # In case the replacement created double sorry like "Sorry, Sorry",
        # clean it up nicely, but let's be safe.
        return sanitized

    @staticmethod
    def _ensure_natural_reply_direction(reply: str, customer_language: str) -> str:
        if customer_language != "ar":
            return reply
        stripped = reply.lstrip()
        if not stripped or not contains_arabic(reply):
            return reply
            
        result = reply
        if not _ARABIC_CHAR_RE.match(stripped):
            result = f"تمام يا فندم، {reply}"
        
        # Ensure Right-To-Left directionality for mixed text
        if not result.startswith("\u200F"):
            result = "\u200F" + result
        return result

    @staticmethod
    def _missing_business_data_reply(customer_language: str) -> str:
        if customer_language == "en":
            return (
                "Sorry, this business data is not available to me yet. "
                "Please send the business data first, then I can help."
            )
        return (
            "معلش يا فندم، بيانات النشاط ده لسه مش متاحة عندي. "
            "."
        )

    @staticmethod
    def _order_finalized_reply(customer_language: str) -> str:
        if customer_language == "en":
            return (
                "Your order is confirmed, and we will start preparing it for you. "
                "Will you be dining in or taking it takeaway?"
            )
        return "تمام يا فندم، كده الطلب اتأكد وهنبدأ نجهزه لحضرتك. تحب تاكل في المطعم ولا تاخده takeaway؟"

    @staticmethod
    def _fulfillment_preference_reply(customer_language: str) -> str:
        if customer_language == "en":
            return "Perfect, we will prepare your order for you now."
        return "تمام يا فندم، طلبك بيتجهز دلوقتي."

    @staticmethod
    def _complaint_escalation_reply(customer_language: str) -> str:
        if customer_language == "en":
            return (
                "I am sorry about what happened. I will record the issue right away "
                "and connect you with someone from management to follow up."
            )
        return (
            "معلش جدًا يا فندم على اللي حصل، هسجل المشكلة فورًا "
            "وهحوّل حضرتك لحد من الإدارة يتابع معاك."
        )

    @staticmethod
    def _escalation_reply(customer_language: str) -> str:
        if customer_language == "en":
            return "Sure, I will connect you with someone from management to follow up."
        return "تمام يا فندم، هحوّل حضرتك لحد من الإدارة يتابع معاك."

    @staticmethod
    def _complaint_reply(customer_language: str) -> str:
        if customer_language == "en":
            return "I am sorry about that. I will record the issue for the support team to follow up."
        return "معلش يا فندم، هسجل المشكلة لفريق الدعم عشان يتابعوها."

    @staticmethod
    def _empty_finalization_reply(customer_language: str) -> str:
        if customer_language == "en":
            return "Sure. Please choose what you would like to order first, and I will help you."
        return "تمام يا فندم، اختار الحاجة اللي تحب تطلبها الأول وأنا أساعدك."

    @staticmethod
    def _cancel_reply(customer_language: str, *, complaint_detected: bool, escalation_detected: bool) -> str:
        if customer_language == "en":
            if complaint_detected and escalation_detected:
                return (
                    "Your order has been canceled. I will record the issue right away "
                    "and connect you with someone from management to follow up."
                )
            if escalation_detected:
                return "Your order has been canceled, and I will connect you with someone from management to follow up."
            if complaint_detected:
                return "Your order has been canceled, and I will record the issue for the support team to follow up."
            return "Your order has been canceled. I am here if you need anything else."

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
    def _sanitize_informational_reply(reply: str, customer_language: str) -> str:
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
        if sanitized:
            return sanitized
        if customer_language == "en":
            return "Tell me if you would like more details."
        return "لو تحب تعرف تفاصيل أكتر، قولي."

    # Aggressive confirmation-question patterns that should be stripped from open-order replies.
    # These are things like "Would you like to confirm your order?" / "Shall I place the order?"
    # or Arabic equivalents — unnatural mid-conversation asks. Natural acknowledgments are kept.
    _AGGRESSIVE_CONFIRMATION_PATTERNS = re.compile(
        r"(?i)"
        # English patterns
        r"(would you like (me )?to (confirm|place|submit|finalize) (your |the )?order"
        r"|shall i (place|submit|confirm|finalize) (your |the )?order"
        r"|should i (place|submit|confirm) (your |the )?order"
        r"|do you want (me )?to (confirm|place|submit) (your |the )?order"
        r"|are you sure (about |with )?(your |the )?order"
        # Arabic Fosha patterns
        r"|هل تريد تأكيد الطلب"
        r"|هل تريد تسجيل الطلب"
        r"|هل تريد إتمام الطلب"
        r"|هل ترغب في تأكيد الطلب"
        r"|هل ترغب في تسجيل الطلب"
        r"|هل ترغب في إتمام الطلب"
        r"|هل تود تأكيد الطلب"
        r"|هل تود تسجيل الطلب"
        # Egyptian Arabic patterns
        r"|تحب تأكد الطلب"
        r"|تحب تأكيد الطلب"
        r"|تحب تسجل الطلب"
        r"|تحب تسجيل الطلب"
        r"|تحب اسجل الطلب"
        r"|عايز تأكد الطلب"
        r"|عايز تأكيد الطلب"
        r"|عايز تسجل الطلب"
        r"|نأكد الطلب"
        r"|هنأكد الطلب"
        r")"
    )


    @staticmethod
    def _sanitize_open_order_reply(reply: str, customer_language: str) -> str:
        """Remove aggressive confirmation-request and premature fulfillment-question sentences.

        Strips sentences that:
        1. Ask the customer to 'confirm/place/submit the order' mid-conversation.
        2. Ask about dine-in / delivery / takeaway before the order is finalized.

        Natural acknowledgments and "anything else?" suggestions are preserved.
        If bad sentences were stripped and no follow-up offer remains, one is appended.
        """
        sentences = re.split(r"(?<=[.!?؟])\s+", reply)
        stripped_any = False
        kept = []
        for s in sentences:
            stripped = s.strip()
            if not stripped:
                continue
            if ChatService._AGGRESSIVE_CONFIRMATION_PATTERNS.search(stripped):
                stripped_any = True
                continue
            if ChatService._has_any_cue(stripped, _FULFILLMENT_QUESTION_REPLY_CUES):
                stripped_any = True
                continue
            kept.append(stripped)

        if not kept:
            # Everything was stripped — return a safe natural fallback.
            return (
                "Got it! Anything else you'd like to add?"
                if customer_language == "en"
                else "تمام يا فندم، ضفت طلبك. تحب تضيف حاجة تانية؟"
            )

        result = " ".join(kept)

        # If we stripped bad sentences and no natural follow-up is already present, add one.
        if stripped_any and not ChatService._has_any_cue(result, _OPEN_ORDER_FOLLOWUP_CUES):
            followup = (
                "Anything else you'd like to add?"
                if customer_language == "en"
                else "تحب تضيف حاجة تانية؟"
            )
            result = f"{result} {followup}"

        return result


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

    def _unavailable_reply(
        self,
        business_id: str,
        unavailable_items: list[BusinessMenuItem],
        customer_language: str,
    ) -> str:
        names = ", ".join(item.name for item in unavailable_items)
        alternatives: list[BusinessMenuItem] = []
        for item in unavailable_items:
            alternatives.extend(self.knowledge.alternatives_for(business_id, item))
        unique_alternatives: list[BusinessMenuItem] = []
        for item in alternatives:
            if item.name not in {existing.name for existing in unique_alternatives}:
                unique_alternatives.append(item)
        if unique_alternatives:
            if customer_language == "en":
                alt_text = self._summarize_english_items(unique_alternatives[:3])
                return f"Sorry, {names} is not available right now. Available alternatives: {alt_text}."
            alt_text = self.knowledge.summarize_items(unique_alternatives[:3])
            names = "، ".join(item.name for item in unavailable_items)
            return f"معلش يا فندم، {names} مش متاح حاليًا. المتاح بدلًا منه: {alt_text}."
        if customer_language == "en":
            return f"Sorry, {names} is not available right now."
        names = "، ".join(item.name for item in unavailable_items)
        return f"معلش يا فندم، {names} مش متاح حاليًا."

    @staticmethod
    def _summarize_english_items(items: list[BusinessMenuItem]) -> str:
        parts = []
        for item in items:
            price = f" for {item.price:g}" if item.price is not None else ""
            parts.append(f"{item.name}{price}")
        return ", ".join(parts)

    @staticmethod
    def _safe_reply(
        customer_language: str,
        order_detected: bool,
        ticket_detected: bool,
        escalation_requested: bool,
    ) -> str:
        if customer_language == "en":
            if escalation_requested:
                return "Sure, we will send your request to the support team so they can follow up with you."
            if ticket_detected:
                return "I am sorry about that. We will record the issue for the support team to follow up."
            if order_detected:
                return "Your order has been recorded. Would you like to add anything else?"
            return "Sorry, that information is not available to me right now from the business data."
        if escalation_requested:
            return "تمام يا فندم، هنوصل طلب حضرتك لفريق الدعم عشان يتابع معاك."
        if ticket_detected:
            return "معلش يا فندم، هنسجل المشكلة لفريق الدعم عشان يتابعها."
        if order_detected:
            return "تمام يا فندم، سجلت طلب حضرتك. تحب تضيف حاجة تانية؟"
        return "معلش يا فندم، المعلومة دي مش متاحة عندي حاليًا."

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
