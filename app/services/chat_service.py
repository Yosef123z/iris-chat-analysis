"""Contract-compliant customer chat orchestration."""

from __future__ import annotations

import re
import time
from collections import Counter

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
    normalize_text,
)
from app.services.session_memory import SessionMemoryStore, SessionState

_ORDER_WORDS = {
    "عايز",
    "عاوز",
    "اريد",
    "اطلب",
    "طلب",
    "اضيف",
    "ضيف",
    "add",
    "order",
    "want",
}
_CONFIRM_WORDS = {
    "اكد",
    "أكد",
    "تمام كده",
    "خلص",
    "confirm",
    "done",
    "ok",
}
_MENU_WORDS = {
    "عندكم",
    "ايه",
    "منيو",
    "منتجات",
    "خدمات",
    "اسعار",
    "سعر",
    "price",
    "menu",
    "products",
    "services",
}
_COMPLAINT_WORDS = {
    "شكوى",
    "مشكله",
    "مشكلة",
    "بارد",
    "غلط",
    "متاخر",
    "وحش",
    "سيء",
    "complaint",
    "wrong",
    "bad",
    "cold",
    "late",
    "problem",
}
_HUMAN_WORDS = {
    "مدير",
    "المدير",
    "اداره",
    "الإدارة",
    "انسان",
    "موظف",
    "human",
    "manager",
    "agent",
}
_FEEDBACK_WORDS = {"تقييم", "قيم", "rating", "feedback"}


def _contains_any(normalized_text: str, words: set[str]) -> bool:
    return any(normalize_text(word) in normalized_text for word in words)


class ChatService:
    """Generate replies and structured signals without backend side effects."""

    def __init__(
        self,
        *,
        knowledge_service: BusinessKnowledgeService,
        memory_store: SessionMemoryStore,
    ) -> None:
        self.knowledge = knowledge_service
        self.memory = memory_store

    async def process_chat_message(self, request: ChatRequest) -> ChatResponse:
        start_time = time.time()
        kb = self.knowledge.get_business_kb(request.business_id)
        if kb is None:
            return ChatResponse(
                session_id=request.session_id,
                reply="معلش يا فندم، بيانات النشاط ده لسه مش متحمّلة عندي. من فضلك ابعت بيانات النشاط الأول وبعدها أقدر أساعدك.",
                processing_time_ms=self._elapsed_ms(start_time),
            )

        state = self.memory.get_or_create(request.session_id, request.business_id)
        normalized = normalize_text(request.message)

        complaint = _contains_any(normalized, _COMPLAINT_WORDS)
        human_request = _contains_any(normalized, _HUMAN_WORDS)
        feedback = _contains_any(normalized, _FEEDBACK_WORDS)

        if complaint or human_request:
            response = self._build_support_response(
                request=request,
                complaint=complaint,
                human_request=human_request,
                feedback=feedback,
                start_time=start_time,
            )
            self.memory.append_turn(state, request.message, response.reply)
            if response.escalation_requested:
                state.handoff_active = True
            return response

        if state.handoff_active:
            response = ChatResponse(
                session_id=request.session_id,
                reply="طلبك متحوّل لفريق الدعم يا فندم، وحد من الفريق هيتابع مع حضرتك.",
                escalation_requested=True,
                processing_time_ms=self._elapsed_ms(start_time),
            )
            self.memory.append_turn(state, request.message, response.reply)
            return response

        if self._is_confirmation(normalized) and state.cart_items:
            response = self._finalize_order_response(request, state, start_time)
            self.memory.append_turn(state, request.message, response.reply)
            return response

        requested_item = self.knowledge.find_menu_item(request.business_id, request.message)
        looks_like_order = self._looks_like_order(normalized)
        if requested_item is not None and looks_like_order:
            response = self._add_item_response(request, state, requested_item, start_time)
            self.memory.append_turn(state, request.message, response.reply)
            return response

        response = self._answer_kb_question(request, start_time)
        self.memory.append_turn(state, request.message, response.reply)
        return response

    def _looks_like_order(self, normalized: str) -> bool:
        return _contains_any(normalized, _ORDER_WORDS)

    def _is_confirmation(self, normalized: str) -> bool:
        return _contains_any(normalized, _CONFIRM_WORDS)

    def _add_item_response(
        self,
        request: ChatRequest,
        state: SessionState,
        item: BusinessMenuItem,
        start_time: float,
    ) -> ChatResponse:
        if not item.is_available:
            alternatives = self.knowledge.alternatives_for(request.business_id, item)
            alt_text = ""
            if alternatives:
                alt_text = f" المتاح بدلًا منه: {self.knowledge.summarize_items(alternatives)}."
            details = OrderDetails(intent="CreateOrder", items=[], total_amount=0)
            return ChatResponse(
                session_id=request.session_id,
                reply=f"معلش يا فندم، {item.name} مش متاح حاليًا.{alt_text}",
                order_detected=True,
                order_finalized=False,
                order_details=details,
                processing_time_ms=self._elapsed_ms(start_time),
            )

        quantity = self._extract_quantity(request.message)
        line = OrderLineItem(
            name=item.name,
            quantity=quantity,
            price=item.price,
            notes=None,
        )
        self._merge_cart_item(state, line)
        details = self._cart_details(state)
        return ChatResponse(
            session_id=request.session_id,
            reply=f"تمام يا فندم، ضفت {quantity} {item.name}. تحب تضيف حاجة تانية ولا أأكد الطلب؟",
            order_detected=True,
            order_finalized=False,
            order_details=details,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    def _finalize_order_response(
        self,
        request: ChatRequest,
        state: SessionState,
        start_time: float,
    ) -> ChatResponse:
        details = self._cart_details(state)
        return ChatResponse(
            session_id=request.session_id,
            reply="تمام يا فندم، تم تأكيد الطلب. backend هيكمل إنشاء الطلب والتحقق من الأسعار والتوافر.",
            order_detected=True,
            order_finalized=True,
            order_details=details,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    def _answer_kb_question(self, request: ChatRequest, start_time: float) -> ChatResponse:
        menu_matches = self.knowledge.search_menu_items(request.business_id, request.message)
        faq_matches = self.knowledge.search_faqs(request.business_id, request.message)
        normalized = normalize_text(request.message)

        if menu_matches:
            summary = self.knowledge.summarize_items(menu_matches)
            reply = f"المتاح عندنا يا فندم: {summary}. تحب تفاصيل عن أي اختيار؟"
        elif faq_matches:
            answer = faq_matches[0].answer
            reply = f"أكيد يا فندم، {answer}"
        elif _contains_any(normalized, _MENU_WORDS):
            kb = self.knowledge.get_business_kb(request.business_id)
            items = kb.available_items[:6] if kb else []
            if items:
                summary = self.knowledge.summarize_items(items)
                reply = f"المتاح عندنا يا فندم: {summary}. تحب أساعدك تختار؟"
            else:
                reply = "معلش يا فندم، مفيش عناصر متاحة في بيانات النشاط حاليًا."
        else:
            reply = "معلش يا فندم، المعلومة دي مش متاحة عندي حاليًا من بيانات النشاط. ممكن تسألني عن المنتجات أو الخدمات أو الأسعار الموجودة عندي."

        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    def _build_support_response(
        self,
        *,
        request: ChatRequest,
        complaint: bool,
        human_request: bool,
        feedback: bool,
        start_time: float,
    ) -> ChatResponse:
        ticket_details = None
        if complaint:
            ticket_details = TicketDetails(
                subject="Customer Complaint",
                description=request.message,
                priority="critical" if human_request else "high",
                category=self._ticket_category(request.message),
            )

        if complaint and human_request:
            reply = "معلش جدًا يا فندم، هنسجل المشكلة فورًا وفريق مختص هيتابع مع حضرتك."
        elif complaint:
            reply = "معلش يا فندم، هنسجل المشكلة لفريق الدعم عشان يتابعها."
        else:
            reply = "تمام يا فندم، هنوصل طلب حضرتك لفريق الدعم عشان يتابع معاك."

        return ChatResponse(
            session_id=request.session_id,
            reply=reply,
            ticket_detected=complaint,
            ticket_details=ticket_details,
            escalation_requested=human_request,
            feedback_requested=feedback,
            processing_time_ms=self._elapsed_ms(start_time),
        )

    @staticmethod
    def _ticket_category(message: str) -> str:
        normalized = normalize_text(message)
        if any(word in normalized for word in ["توصيل", "متاخر", "late", "delivery"]):
            return "delivery"
        if any(word in normalized for word in ["غلط", "wrong"]):
            return "wrong_order"
        if any(word in normalized for word in ["دفع", "payment"]):
            return "payment"
        if any(word in normalized for word in ["ناقص", "missing"]):
            return "missing"
        if any(word in normalized for word in ["بارد", "جوده", "quality", "cold"]):
            return "quality"
        return "complaint"

    @staticmethod
    def _extract_quantity(message: str) -> int:
        digit_match = re.search(r"\d+", message)
        if digit_match:
            return max(1, min(int(digit_match.group()), 50))
        normalized = normalize_text(message)
        words = {
            "واحد": 1,
            "اتنين": 2,
            "اثنين": 2,
            "تلاته": 3,
            "ثلاثه": 3,
            "اربعه": 4,
            "خمسه": 5,
        }
        for word, quantity in words.items():
            if word in normalized:
                return quantity
        return 1

    @staticmethod
    def _merge_cart_item(state: SessionState, line: OrderLineItem) -> None:
        for existing in state.cart_items:
            if existing.name == line.name and existing.notes == line.notes:
                existing.quantity += line.quantity
                existing.price = line.price
                return
        state.cart_items.append(line)

    @staticmethod
    def _cart_details(state: SessionState) -> OrderDetails:
        total = sum(item.quantity * item.price for item in state.cart_items)
        return OrderDetails(
            intent="CreateOrder",
            items=list(state.cart_items),
            total_amount=total,
        )

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.time() - start) * 1000)
