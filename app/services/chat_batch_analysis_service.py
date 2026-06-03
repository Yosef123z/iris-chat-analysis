"""LLM-backed chat-batch analysis with PII redaction."""

from __future__ import annotations

import json
import re

from fastapi import HTTPException

from app.config import settings
from app.core.llm_interface import AIProviderError, LLMProvider
from app.models.analysis import (
    AnalysisMessage,
    ChatAnalysisResult,
    ChatBatchAnalysisRequest,
    ChatBatchAnalysisResponse,
    IntentCount,
)
from app.services.business_knowledge_service import normalize_text
from app.services.pii_service import PIIService


_SUPPORTED_INTENTS = [
    "CreateOrder",
    "ModifyOrder",
    "CancelOrder",
    "AskAboutProducts",
    "AskAboutPrice",
    "Complaint",
    "RequestHumanAgent",
    "Compliment",
    "Greeting",
    "Farewell",
    "GeneralQuestion",
    "Unknown",
]
_ANALYSIS_DIALECT_REPLACEMENTS = (
    ("يريد التحدث إلى المدير", "عايز يكلم المدير"),
    ("يريد التحدث مع المدير", "عايز يكلم المدير"),
    ("استلمه بارداً", "وصله بارد"),
    ("استلمه باردًا", "وصله بارد"),
    ("استلمه باردا", "وصله بارد"),
    ("العميل طلب التحدث", "العميل طلب يكلم"),
    ("التحدث إلى", "يكلم"),
    ("التحدث مع", "يكلم"),
    ("تقديم شكوى", "تسجيل مشكلة"),
)
_COMPLAINT_TERMS = {
    "شكوى",
    "وصل بارد",
    "وصل غلط",
    "الأوردر غلط",
    "الاوردر غلط",
    "ناقص",
    "متأخر",
    "اتأخر",
    "مشكلة",
    "wrong order",
    "cold",
    "missing",
    "late",
    "complaint",
    "problem",
}
_HUMAN_TERMS = {
    "المدير",
    "الإدارة",
    "الادارة",
    "موظف",
    "خدمة العملاء",
    "manager",
    "human",
    "representative",
    "support",
}
_ORDER_TERMS = {
    "عايز",
    "طلب",
    "ضفت",
    "ضيف",
    "order",
    "ordered",
    "add",
}


class ChatBatchAnalysisService:
    def __init__(self, pii_service: PIIService, llm_provider: LLMProvider) -> None:
        self.pii = pii_service
        self.llm_provider = llm_provider

    async def analyze(self, payload: ChatBatchAnalysisRequest) -> ChatBatchAnalysisResponse:
        session = payload.sessions[0]
        messages = self._clean_messages(session.messages)
        if not messages:
            raise HTTPException(status_code=422, detail="At least one non-empty message is required.")

        redacted_messages = [
            AnalysisMessage(role=message.role, text=self.pii.remove_pii_text(message.text))
            for message in messages
        ]

        try:
            result = await self.llm_provider.structured_output(
                self._build_messages(payload.business_id, session.session_id, redacted_messages),
                model=settings.ANALYSIS_MODEL,
                output_model=ChatAnalysisResult,
                temperature=0.0,
            )
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="AI analysis generation failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI analysis generation failed") from exc

        result.session_id = session.session_id
        result.summary = self.pii.remove_pii_text(result.summary)
        result.summary_ar = self._sanitize_summary_ar(self.pii.remove_pii_text(result.summary_ar))
        result.main_topics = [self.pii.remove_pii_text(topic) for topic in result.main_topics]
        result.key_moments = [self.pii.remove_pii_text(moment) for moment in result.key_moments]
        result = self._apply_validation_policies(result, redacted_messages)
        result = ChatAnalysisResult.model_validate(result.model_dump())
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

    @staticmethod
    def _build_messages(
        business_id: str,
        session_id: str,
        messages: list[AnalysisMessage],
    ) -> list[dict[str, str]]:
        transcript = "\n".join(f"{message.role}: {message.text}" for message in messages)
        schema = {
            "sessionId": session_id,
            "summary": "Concise English summary, no PII",
            "summaryAr": "Concise Arabic summary, no PII",
            "overallSentiment": {"score": "number -1.0 to 1.0", "label": "Positive | Neutral | Negative"},
            "mainIntent": "must equal intentsDetected[0].name",
            "intentsDetected": [{"name": _SUPPORTED_INTENTS, "count": "integer >= 1"}],
            "mainTopics": ["concise topic strings, no PII"],
            "keyMoments": ["concise human-readable strings, no PII"],
        }
        system_prompt = (
            "Analyze only the redacted transcript. Do not infer or include PII in summaries, topics, or key moments. "
            "Return one strict JSON object using camelCase fields only. summary must be concise English. summaryAr "
            "must be concise natural Egyptian Arabic, not Modern Standard Arabic. Extract meaningful mainTopics "
            "and keyMoments when products, issues, or handoff actions are clear. Distinguish active/current intent "
            "from historical context: if the session ends in a complaint or human handoff, Complaint should be the "
            "mainIntent, and RequestHumanAgent must be included when the customer asks for a manager or person. "
            "Only include CreateOrder for a clear order request/action, not merely because an item name appears in "
            "a complaint. overallSentiment.score must be between -1.0 and 1.0. label must be Positive, Neutral, or "
            "Negative. intentsDetected must use only supported intent names and be sorted by count descending. "
            "mainIntent must equal intentsDetected[0].name. If truly uncertain, use Neutral score 0.0 and Unknown "
            "intent with count 1."
        )
        user_prompt = (
            f"businessId: {business_id}\n"
            f"sessionId: {session_id}\n"
            f"supportedIntents: {json.dumps(_SUPPORTED_INTENTS)}\n"
            f"schema: {json.dumps(schema, ensure_ascii=False)}\n\n"
            f"redactedTranscript:\n{transcript}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def _sanitize_summary_ar(text: str) -> str:
        sanitized = text
        for source, target in _ANALYSIS_DIALECT_REPLACEMENTS:
            sanitized = sanitized.replace(source, target)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized

    def _apply_validation_policies(
        self,
        result: ChatAnalysisResult,
        messages: list[AnalysisMessage],
    ) -> ChatAnalysisResult:
        transcript = "\n".join(f"{message.role}: {message.text}" for message in messages)
        if not result.main_topics:
            result.main_topics = self._extract_topics(transcript)
        if not result.key_moments:
            result.key_moments = self._extract_key_moments(messages)

        has_complaint = self._has_any(transcript, _COMPLAINT_TERMS)
        has_human_request = self._has_any(transcript, _HUMAN_TERMS)
        if has_complaint:
            self._upsert_intent(result, "Complaint", make_first=True)
        if has_human_request:
            self._upsert_intent(result, "RequestHumanAgent")
        if has_complaint and has_human_request:
            self._upsert_intent(result, "Complaint", make_first=True)

        result.summary_ar = self._sanitize_summary_ar(result.summary_ar)
        return result

    @staticmethod
    def _has_any(text: str, terms: set[str]) -> bool:
        normalized = normalize_text(text)
        return any(normalize_text(term) in normalized for term in terms)

    @staticmethod
    def _upsert_intent(
        result: ChatAnalysisResult,
        name: str,
        *,
        make_first: bool = False,
    ) -> None:
        max_count = max((intent.count for intent in result.intents_detected), default=0)
        for intent in result.intents_detected:
            if intent.name == name:
                if make_first:
                    intent.count = max_count + 1
                break
        else:
            result.intents_detected.append(
                IntentCount(name=name, count=max_count + 1 if make_first else 1)
            )
        result.intents_detected = sorted(result.intents_detected, key=lambda item: item.count, reverse=True)
        result.main_intent = result.intents_detected[0].name

    @staticmethod
    def _extract_topics(transcript: str) -> list[str]:
        topics: list[str] = []
        for match in re.finditer(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\b", transcript):
            value = match.group(0).strip()
            if value not in {"EMAIL", "PHONE", "NAME"} and value not in topics:
                topics.append(value)
        if ChatBatchAnalysisService._has_any(transcript, {"شكوى", "complaint", "مشكلة"}):
            topics.append("شكوى")
        if ChatBatchAnalysisService._has_any(transcript, {"المدير", "manager", "human"}):
            topics.append("طلب المدير")
        if ChatBatchAnalysisService._has_any(transcript, {"وصل بارد", "cold"}):
            topics.append("الأوردر وصل بارد")
        if ChatBatchAnalysisService._has_any(transcript, {"وصل غلط", "wrong order"}):
            topics.append("الأوردر وصل غلط")
        deduped: list[str] = []
        for topic in topics:
            if topic and topic not in deduped:
                deduped.append(topic)
        return deduped[:6]

    @staticmethod
    def _extract_key_moments(messages: list[AnalysisMessage]) -> list[str]:
        moments: list[str] = []
        for message in messages:
            if message.role != "customer":
                continue
            text = message.text
            if ChatBatchAnalysisService._has_any(text, _ORDER_TERMS):
                item = ChatBatchAnalysisService._extract_first_title_case(text)
                if item:
                    moments.append(f"العميل طلب {item}")
            if ChatBatchAnalysisService._has_any(text, {"وصل بارد", "cold"}):
                moments.append("العميل قال إن الأوردر وصل بارد")
            if ChatBatchAnalysisService._has_any(text, {"وصل غلط", "wrong order"}):
                moments.append("العميل قال إن الأوردر وصل غلط")
            if ChatBatchAnalysisService._has_any(text, _HUMAN_TERMS):
                moments.append("العميل طلب يكلم المدير")
        deduped: list[str] = []
        for moment in moments:
            if moment not in deduped:
                deduped.append(moment)
        return deduped[:6]

    @staticmethod
    def _extract_first_title_case(text: str) -> str | None:
        match = re.search(r"\b[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\b", text)
        return match.group(0) if match else None
