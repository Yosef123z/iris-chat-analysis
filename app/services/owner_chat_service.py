"""Business owner analytics chatbot service."""

import logging
import re
from typing import Dict, List

from app.config import settings
from app.core.conversation import ConversationManager
from app.core.llm_interface import LLMProvider
from app.models.owner_chat import OwnerChatRequest, OwnerChatResponse
from app.services.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)


class OwnerChatService:
    def __init__(self, provider: LLMProvider, analytics_service: AnalyticsService):
        self.provider = provider
        self.analytics = analytics_service
        self.conversation = ConversationManager(limit=10)

    async def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        return await self.conversation.get_history(session_id)

    async def _update_history(self, session_id: str, user_msg: str, assistant_msg: str):
        await self.conversation.update_history(session_id, user_msg, assistant_msg)

    def _build_system_prompt(self) -> str:
        return """You are IRIS Analytics Assistant - the private AI advisor for the business owner.
ROLE:
- You are speaking ONLY to the business owner/manager.
- You answer business questions about daily operations, sales, performance, and strategy.
- You respond in Egyptian Arabic.
- You are professional, executive-level, and data-driven.

STRICT RULES:
1. SPEAK ONLY IN ARABIC SCRIPT (Egyptian Dialect). NEVER use Latin/English letters.
2. Use ONLY Egyptian Massry, not Modern Standard Arabic.
3. ONLY use data from the ANALYTICS_REPORT provided below. NEVER make up numbers.
4. If the requested data is not in the report, respond EXACTLY: "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
5. Always cite the section you are referencing.
6. Provide actionable insights.
7. Format currency as 'جنيه' or 'EGP'. Format percentages with %.
8. Keep responses concise (3-4 sentences max).
"""

    async def process_owner_message(self, request: OwnerChatRequest) -> OwnerChatResponse:
        try:
            analytics_data = self.analytics.get_full_report()
            system_prompt = self._build_system_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"ANALYTICS_REPORT:\n{analytics_data}"},
            ]
            messages.extend(await self._get_history(request.session_id))
            messages.append({"role": "user", "content": request.message})

            reply_text = await self.provider.chat(
                messages,
                model=settings.GPT_CHAT_MODEL,
                temperature=0.3,
                max_tokens=600,
            )

            if "الأوضة" in reply_text or "الاوضة" in reply_text:
                logger.warning("Owner chat hallucination corrected: الأوضة")
                reply_text = reply_text.replace("الأوضة", "الدنيا").replace("الاوضة", "الدنيا")

            await self._update_history(request.session_id, request.message, reply_text)
            return OwnerChatResponse(
                session_id=request.session_id,
                reply=reply_text,
                data_sources_used=self._infer_data_sources(request.message, reply_text),
                confidence=self._assess_confidence(reply_text),
            )
        except Exception as e:
            logger.error("Error in OwnerChatService.process_owner_message: %s", e)
            return OwnerChatResponse(
                session_id=request.session_id,
                reply="معلش، عندي مشكلة في الوصول للبيانات دي دلوقتي. جرب تاني كمان شوية.",
                data_sources_used=[],
                confidence="low",
            )

    def _infer_data_sources(self, question: str, answer: str) -> List[str]:
        sections = []
        question_lower = question.lower()
        answer_lower = answer.lower()
        keyword_map = {
            "Executive Overview": ["summary", "overview", "how did", "today go", "brief"],
            "Financial Performance": ["revenue", "sales", "orders", "aov", "payment", "egp", "money"],
            "Menu Analysis": ["menu", "selling", "top seller", "item", "stock", "inventory", "cross-sell"],
            "Operational Health": ["peak", "delivery", "response time", "ai", "escalation", "hitl", "staff"],
            "Customer Sentiment": ["sentiment", "csat", "feedback", "complaint", "satisfaction", "churn"],
            "Recommendations": ["recommend", "suggest", "action", "should", "next steps", "improve"],
        }
        for section_name, keywords in keyword_map.items():
            if any(kw in question_lower or kw in answer_lower for kw in keywords):
                sections.append(section_name)
        return sections or ["Executive Overview"]

    def _assess_confidence(self, reply: str) -> str:
        reply_lower = reply.lower()
        if "معلش، المعلومة دي مش موجودة في تقرير النهاردة." in reply_lower:
            return "low"
        if re.search(r"\d+", reply) or "%" in reply or "egp" in reply_lower:
            return "high"
        return "medium"
