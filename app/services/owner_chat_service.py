"""Business owner analytics chatbot — driven by backend-synced reports."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Dict, List

from app.config import settings
from app.core.conversation import ConversationManager
from app.core.llm_interface import LLMProvider
from app.models.owner_chat import OwnerChatRequest, OwnerChatResponse

if TYPE_CHECKING:
    from app.services.owner_report_service import OwnerReportService

logger = logging.getLogger(__name__)

_NO_REPORT_REPLY_AR = (
    "معلش يا فندم، مش لاقي تقرير متزامن لأعمالك دي دلوقتي."
)
_NO_REPORT_REPLY_EN = (
    "Sorry, no synced report was found for this business yet."
)


def _contains_arabic(text: str) -> bool:
    """Return True if the text contains at least one Arabic Unicode character."""
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


class OwnerChatService:
    def __init__(self, provider: LLMProvider, report_service: "OwnerReportService") -> None:
        self.provider = provider
        self.report_service = report_service
        self.conversation = ConversationManager(limit=10)

    async def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        return await self.conversation.get_history(session_id)

    async def _update_history(self, session_id: str, user_msg: str, assistant_msg: str):
        await self.conversation.update_history(session_id, user_msg, assistant_msg)

    def _build_system_prompt(self, message_language: str) -> str:
        """Build the system prompt, injecting the detected reply language explicitly."""
        if message_language == "ar":
            lang_directive = (
                "DETECTED_LANGUAGE: Arabic.\n"
                "You MUST reply entirely in Egyptian Masry Arabic. Do NOT use any English in your reply.\n"
                "For the no-data fallback, use EXACTLY: معلش، المعلومة دي مش موجودة في تقرير النهاردة."
            )
        else:
            lang_directive = (
                "DETECTED_LANGUAGE: English.\n"
                "You MUST reply entirely in English. Do NOT use any Arabic in your reply.\n"
                "For the no-data fallback, use EXACTLY: Sorry, that information is not available in the current report."
            )

        return f"""You are IRIS Analytics Assistant - the private AI advisor for the business owner.
ROLE:
- You are speaking ONLY to the business owner/manager.
- You answer business questions about daily operations, sales, performance, and strategy.
- You are professional, executive-level, and data-driven.

LANGUAGE (CRITICAL — follow exactly):
{lang_directive}
For Arabic replies: use Egyptian Masry (عامية مصرية), NOT Modern Standard Arabic (فصحى).
   Banned Fosha words (use Masry instead):
   هذا/هذه → ده/دي, الذي/التي → اللي, يجب → لازم, يمكن → ممكن, كيف → إزاي, لماذا → ليه, ماذا → إيه, الآن → دلوقتي, أيضاً/كذلك/كما → كمان, ولكن → بس, نحن → احنا, جداً → أوي, غير متوفر → مش متاح, لا يوجد → مفيش.

STRICT RULES:
1. ONLY use data from the ANALYTICS_REPORT provided below. NEVER make up facts or numbers.
2. If the requested data is not in the report, use the exact no-data fallback phrase stated in DETECTED_LANGUAGE above.
3. Always cite the section you are referencing.
4. Keep responses concise (3-4 sentences max).
5. PLAIN TEXT ONLY — no markdown formatting (no **, *, ##, -, or bullet points). Write in natural sentences.
6. Do not output raw technical metrics like 'sentiment score: -0.6' or session IDs; describe them in plain business language instead (e.g., 'high customer frustration').
"""

    async def process_owner_message(self, request: OwnerChatRequest) -> OwnerChatResponse:
        stored = self.report_service.get_report(request.business_id)

        if stored is None:
            logger.warning(
                "Owner chat request for unknown business_id=%s — no synced report found.",
                request.business_id,
            )
            fallback = (
                _NO_REPORT_REPLY_AR
                if _contains_arabic(request.message)
                else _NO_REPORT_REPLY_EN
            )
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=fallback,
                data_sources_used=[],
                confidence="low",
            )

        try:
            is_arabic = _contains_arabic(request.message)
            report_context = self.report_service.build_prompt_context(stored)
            system_prompt = self._build_system_prompt("ar" if is_arabic else "en")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"ANALYTICS_REPORT:\n{report_context}"},
            ]
            messages.extend(await self._get_history(request.session_id))
            messages.append({"role": "user", "content": request.message})

            raw_reply = await self.provider.chat(
                messages,
                model=settings.GPT_CHAT_MODEL,
                temperature=0.3,
                max_tokens=600,
            )
            reply_text = self._sanitize_reply(raw_reply)
            reply_text = self._enforce_reply_language(reply_text, is_arabic)

            await self._update_history(request.session_id, request.message, reply_text)
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=reply_text,
                data_sources_used=self._infer_data_sources(request.message, reply_text),
                confidence=self._assess_confidence(reply_text),
            )
        except Exception as e:
            logger.error("Error in OwnerChatService.process_owner_message: %s", e)
            if _contains_arabic(request.message):
                error_reply = "معلش، عندي مشكلة في الوصول للبيانات دي دلوقتي. جرب تاني كمان شوية."
            else:
                error_reply = "Sorry, I had trouble accessing the data right now. Please try again in a moment."
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=error_reply,
                data_sources_used=[],
                confidence="low",
            )


    @staticmethod
    def _egyptianize(text: str) -> str:
        """Apply Fosha → Masry word substitutions as a post-processing safety net."""
        # Order matters: longer phrases first to avoid partial replacements
        substitutions = [
            # Demonstratives
            (r"\bهذه\b", "دي"),
            (r"\bهذا\b", "ده"),
            (r"\bتلك\b", "دي"),
            (r"\bذلك\b", "ده"),
            (r"\bاللذي\b", "اللي"),
            (r"\bالتي\b", "اللي"),
            # Modal / obligation
            (r"\bيجب عليك\b", "لازم"),
            (r"\bيجب\b", "لازم"),
            (r"\bيمكن\b", "ممكن"),
            # Question words
            (r"\bكيف\b", "إزاي"),
            (r"\bلماذا\b", "ليه"),
            (r"\bماذا\b", "إيه"),
            (r"\bأين\b", "فين"),
            (r"\bمتى\b", "امتى"),
            # Time
            (r"\bالآن\b", "دلوقتي"),
            (r"\bحالياً\b", "دلوقتي"),
            (r"\bاليوم\b", "النهارده"),
            # Conjunctions / connectors
            (r"\bولكن\b", "بس"),
            (r"\bلذلك\b", "عشان كده"),
            (r"\bبالإضافة إلى ذلك\b", "كمان"),
            (r"\bأيضاً\b", "كمان"),
            (r"\bكذلك\b", "كمان"),
            (r"\bكما\b", "كمان"),
            # Pronouns
            (r"\bنحن\b", "احنا"),
            (r"\bهم\b", "هما"),
            # Availability
            (r"\bغير متوفر\b", "مش متاح"),
            (r"\bلا يوجد\b", "مفيش"),
            (r"\bيوجد\b", "فيه"),
            # Apology
            (r"\bعذراً\b", "معلش"),
            (r"\bآسف\b", "معلش"),
            # Intensifiers
            (r"\bجداً\b", "أوي"),
            (r"\bبشكل كبير\b", "بشكل كبير"),  # keep as-is, acceptable
        ]
        for pattern, replacement in substitutions:
            text = re.sub(pattern, replacement, text)
        return text

    @staticmethod
    def _sanitize_reply(text: str) -> str:
        """Strip markdown noise, stray backslashes, and Fosha words from LLM replies."""
        # Remove stray backslashes (e.g. literal \n or lone \)
        text = re.sub(r"\\+", "", text)
        # Remove bold/italic markers (**word** / *word*)
        text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
        # Remove ATX headings (## Heading)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove leading list markers ("- item", "* item", "1. item")
        text = re.sub(r"^[\-\*]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
        # Collapse newlines into spaces
        text = re.sub(r"\n{2,}", " ", text)
        text = re.sub(r"\n", " ", text)
        text = re.sub(r" {2,}", " ", text)
        # Apply Fosha → Masry substitutions if the text contains Arabic
        if _contains_arabic(text):
            text = OwnerChatService._egyptianize(text)
        return text.strip()

    # Known no-data phrases in each language
    _NO_DATA_AR = "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
    _NO_DATA_EN = "Sorry, that information is not available in the current report."

    @classmethod
    def _enforce_reply_language(cls, reply: str, is_arabic: bool) -> str:
        """Safety net: if the LLM returned a no-data phrase in the wrong language, swap it.

        This handles the case where the LLM ignores the detected-language directive
        and returns the Arabic no-data phrase for an English message (or vice versa).
        """
        reply_stripped = reply.strip()

        if is_arabic:
            # Arabic message → must reply in Arabic
            if reply_stripped == cls._NO_DATA_EN:
                return cls._NO_DATA_AR
        else:
            # English message → must reply in English
            if reply_stripped == cls._NO_DATA_AR:
                return cls._NO_DATA_EN

        return reply

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
        # Both Arabic and English no-data phrases signal low confidence
        _no_data_phrases = (
            "معلش، المعلومة دي مش موجودة في تقرير النهاردة.",
            "sorry, that information is not available in the current report.",
        )
        if any(phrase in reply_lower for phrase in _no_data_phrases):
            return "low"
        if re.search(r"\d+", reply) or "%" in reply or "egp" in reply_lower:
            return "high"
        return "medium"
