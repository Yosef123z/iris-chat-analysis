"""LLM-backed chat-batch analysis with PII redaction."""

from __future__ import annotations

import json

from fastapi import HTTPException

from app.config import settings
from app.core.llm_interface import AIProviderError, LLMProvider
from app.models.analysis import (
    AnalysisMessage,
    ChatAnalysisResult,
    ChatBatchAnalysisRequest,
    ChatBatchAnalysisResponse,
)
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
        result.summary_ar = self.pii.remove_pii_text(result.summary_ar)
        result.main_topics = [self.pii.remove_pii_text(topic) for topic in result.main_topics]
        result.key_moments = [self.pii.remove_pii_text(moment) for moment in result.key_moments]
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
            "must be concise Arabic. overallSentiment.score must be between -1.0 and 1.0. label must be Positive, "
            "Neutral, or Negative. intentsDetected must use only supported intent names and be sorted by count "
            "descending. mainIntent must equal intentsDetected[0].name. If uncertain, use Neutral score 0.0 and "
            "Unknown intent with count 1, empty topics, and empty key moments."
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
