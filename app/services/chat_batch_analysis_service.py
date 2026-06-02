"""Deterministic chat-batch analysis with PII redaction."""

from __future__ import annotations

from collections import Counter

from fastapi import HTTPException

from app.models.analysis import (
    AnalysisMessage,
    ChatAnalysisResult,
    ChatBatchAnalysisRequest,
    ChatBatchAnalysisResponse,
    IntentCount,
    SentimentResult,
)
from app.services.business_knowledge_service import normalize_text
from app.services.pii_service import PIIService


class ChatBatchAnalysisService:
    def __init__(self, pii_service: PIIService) -> None:
        self.pii = pii_service

    def analyze(self, payload: ChatBatchAnalysisRequest) -> ChatBatchAnalysisResponse:
        session = payload.sessions[0]
        messages = self._clean_messages(session.messages)
        if not messages:
            raise HTTPException(status_code=422, detail="At least one non-empty message is required.")

        redacted_messages = [
            AnalysisMessage(role=message.role, text=self.pii.remove_pii_text(message.text))
            for message in messages
        ]
        transcript = "\n".join(f"{message.role}: {message.text}" for message in redacted_messages)
        intents = self._detect_intents(redacted_messages)
        sentiment = self._sentiment(redacted_messages)
        topics = self._topics(redacted_messages)
        key_moments = self._key_moments(redacted_messages, intents)

        result = ChatAnalysisResult(
            session_id=session.session_id,
            summary=self._summary_en(redacted_messages, intents),
            summary_ar=self._summary_ar(redacted_messages, intents),
            overall_sentiment=sentiment,
            main_intent=intents[0].name,
            intents_detected=intents,
            main_topics=topics,
            key_moments=key_moments,
        )
        del transcript
        return ChatBatchAnalysisResponse(
            business_id=payload.business_id,
            results=[result],
        )

    @staticmethod
    def _clean_messages(messages: list[AnalysisMessage]) -> list[AnalysisMessage]:
        cleaned = []
        for message in messages:
            text = message.text.strip()
            if text:
                cleaned.append(AnalysisMessage(role=message.role, text=text))
        return cleaned

    def _detect_intents(self, messages: list[AnalysisMessage]) -> list[IntentCount]:
        counts: Counter[str] = Counter()
        for message in messages:
            intent = self._intent_for_text(message.text)
            counts[intent] += 1
        if not counts:
            counts["Unknown"] = 1
        return [
            IntentCount(name=name, count=count)
            for name, count in counts.most_common()
        ]

    @staticmethod
    def _intent_for_text(text: str) -> str:
        normalized = normalize_text(text)
        if any(word in normalized for word in ["اهلا", "السلام", "hello", "hi"]):
            return "Greeting"
        if any(word in normalized for word in ["الغاء", "cancel"]):
            return "CancelOrder"
        if any(word in normalized for word in ["تعديل", "غير", "modify", "change"]):
            return "ModifyOrder"
        if any(word in normalized for word in ["سعر", "اسعار", "price"]):
            return "AskAboutPrice"
        if any(word in normalized for word in ["منيو", "منتجات", "خدمات", "menu", "products", "services"]):
            return "AskAboutProducts"
        if any(word in normalized for word in ["شكوي", "مشكله", "بارد", "غلط", "complaint", "wrong", "bad", "cold"]):
            return "Complaint"
        if any(word in normalized for word in ["مدير", "انسان", "موظف", "manager", "human", "agent"]):
            return "RequestHumanAgent"
        if any(word in normalized for word in ["شكرا", "ممتاز", "حلو", "thanks", "great"]):
            return "Compliment"
        if any(word in normalized for word in ["باي", "مع السلامه", "bye"]):
            return "Farewell"
        if any(word in normalized for word in ["عايز", "اطلب", "اضيف", "order", "want"]):
            return "CreateOrder"
        if normalized:
            return "GeneralQuestion"
        return "Unknown"

    @staticmethod
    def _sentiment(messages: list[AnalysisMessage]) -> SentimentResult:
        text = normalize_text(" ".join(message.text for message in messages))
        negative = ["شكوي", "مشكله", "بارد", "غلط", "سيء", "وحش", "bad", "wrong", "cold", "late"]
        positive = ["شكرا", "ممتاز", "حلو", "رائع", "thanks", "great", "good"]
        if any(word in text for word in negative):
            return SentimentResult(score=-0.65, label="Negative")
        if any(word in text for word in positive):
            return SentimentResult(score=0.65, label="Positive")
        return SentimentResult(score=0.0, label="Neutral")

    @staticmethod
    def _topics(messages: list[AnalysisMessage]) -> list[str]:
        skip = {
            "customer",
            "assistant",
            "عايز",
            "اريد",
            "تمام",
            "اهلا",
            "سعر",
            "شكرا",
            "hello",
            "want",
            "order",
            "the",
            "and",
            "for",
        }
        topics: list[str] = []
        for message in messages:
            for token in normalize_text(message.text).split():
                if token in skip or token.startswith("[") or len(token) < 3:
                    continue
                if token not in topics:
                    topics.append(token)
                if len(topics) >= 5:
                    return topics
        return topics

    @staticmethod
    def _key_moments(messages: list[AnalysisMessage], intents: list[IntentCount]) -> list[str]:
        names = {intent.name for intent in intents}
        moments = []
        if "CreateOrder" in names:
            moments.append("Customer placed or built an order.")
        if "Complaint" in names:
            moments.append("Customer reported a complaint.")
        if "RequestHumanAgent" in names:
            moments.append("Customer requested human support.")
        if "Compliment" in names:
            moments.append("Customer gave positive feedback.")
        return moments

    @staticmethod
    def _summary_en(messages: list[AnalysisMessage], intents: list[IntentCount]) -> str:
        main_intent = intents[0].name
        if len(messages) == 1:
            return f"Single-message session. Main intent: {main_intent}."
        return f"Conversation analyzed with {len(messages)} messages. Main intent: {main_intent}."

    @staticmethod
    def _summary_ar(messages: list[AnalysisMessage], intents: list[IntentCount]) -> str:
        main_intent = intents[0].name
        if len(messages) == 1:
            return f"جلسة من رسالة واحدة. النية الرئيسية: {main_intent}."
        return f"تم تحليل المحادثة بعدد {len(messages)} رسائل. النية الرئيسية: {main_intent}."
