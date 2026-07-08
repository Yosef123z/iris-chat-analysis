"""Business owner analytics chatbot — driven by backend-synced reports."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List

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

# Low-confidence fallback when the requested fact is missing from metrics/report.
_NO_DATA_REPLY_AR = "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
_NO_DATA_REPLY_EN = "Sorry, that information is not available in the current report."

# Question categories used to decide which metrics source to require first.
_MENU_KEYWORDS = {
    "menu", "catalog", "dish", "food", "item", "price", "cost", "category",
    "available", "availability", "description", "what do you have", "what do you offer",
    "how much", "how much is", "what is the price", "price of", "cost of", "item price",
    "منيو", "صنف", "أصناف", "اصناف", "اكل", "اكلة", "فيه ايه", "عندك ايه",
    "عندكم ايه", "موجود ايه", "متاح ايه", "سعر", "بكام", "كم", "التصنيف",
}
_FAQ_KEYWORDS = {
    "faq", "question", "policy", "return", "refund", "working hours", "opening hours",
    "delivery time", "سؤال", "سياسة", "مواعيد", "ساعات", "استرجاع", "استرداد",
}
_ORDER_KEYWORDS = {
    "order", "orders", "request", "requests", "today", "this week", "this period",
    "اوردر", "طلب", "طلبات", "النهارده", "الأسبوع", "الاسبوع", "الفترة",
}
_TICKET_KEYWORDS = {
    "ticket", "tickets", "complaint", "complaints", "issue", "issues", "escalated",
    "open ticket", "تذكرة", "تذاكر", "شكوى", "شكاوى", "مشكلة", "مشاكل", "مفتوحة",
}
_BEST_SELLER_KEYWORDS = {
    "best seller", "best-selling", "best selling", "top seller", "top-selling", "top item",
    "most popular", "most ordered", "bestseller",
    "اكتر مبيعا", "الأكثر", "الاكثر", "أفضل", "افضل", "شائع",
}
_COMMON_ISSUE_KEYWORDS = {
    "common issue", "common complaint", "most common", "frequent", "top problem",
    "اكتر شكوى", "أكثر مشكلة", "اكثر مشكلة", "متكرر",
}
_REPORT_SUMMARY_KEYWORDS = {
    "summary", "overview", "highlight", "recommendation", "action", "risk", "problem",
    "report", "brief", "ملخص", "نظرة", "توصية", "اقتراح", "خطر", "مشكلة",
}

_INTERNAL_TERMS = {
    "backend", "api", "prompt", "system prompt", "rag", "embeddings",
    "vector search", "vector", "retrieval", "validation layer", "json contract",
    "database", "internal tools", "python", "module",
}


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
                f"For the no-data fallback, use EXACTLY: {_NO_DATA_REPLY_AR}"
            )
        else:
            lang_directive = (
                "DETECTED_LANGUAGE: English.\n"
                "You MUST reply entirely in English. Do NOT use any Arabic in your reply.\n"
                f"For the no-data fallback, use EXACTLY: {_NO_DATA_REPLY_EN}"
            )

        return f"""You are IRIS Owner Assistant for a restaurant business owner.
ROLE:
- You are speaking ONLY to the business owner/manager.
- You answer business questions about daily operations, sales, performance, menu, and strategy.
- You are professional, executive-level, concise, intelligent, and data-driven.

LANGUAGE (CRITICAL — follow exactly):
{lang_directive}
For Arabic replies: use Egyptian Masry (عامية مصرية), NOT Modern Standard Arabic (فصحى).
   Banned Fosha words (use Masry instead):
   هذا/هذه → ده/دي, الذي/التي → اللي, يجب → لازم, يمكن → ممكن, كيف → إزاي, لماذا → ليه, ماذا → إيه, الآن → دلوقتي, أيضاً/كذلك/كما → كمان, ولكن → بس, نحن → احنا, جداً → أوي, غير متوفر → مش متاح, لا يوجد → مفيش.

STRICT RULES:
1. Answer using ONLY the provided synced owner context below. The synced owner context contains two sources: raw backend metrics and generated report sections.
2. Use raw metrics FIRST for factual questions about menu items, prices, availability, categories, FAQs, orders, tickets, best-selling items, and common ticket types.
3. Use the generated report sections for summaries, highlights, problems, recommendations, suggested actions, and risk level.
4. NEVER use general knowledge. NEVER invent menu items, prices, availability, offers, order counts, ticket counts, best sellers, FAQs, business facts, analytics, recommendations, or insights.
5. If the requested information is not available in the provided synced owner context, use the exact no-data fallback phrase stated in DETECTED_LANGUAGE above.
6. Do not mention internal system details such as backend, API, prompt, system prompt, RAG, embeddings, vector search, retrieval, validation layer, JSON contract, database, or implementation details.
7. Keep responses concise (3-4 sentences max).
8. PLAIN TEXT ONLY — no markdown formatting (no **, *, ##, -, or bullet points). Write in natural sentences.
9. Do not output raw technical metrics like 'sentiment score: -0.6' or session IDs; describe them in plain business language instead (e.g., 'high customer frustration').
"""

    @staticmethod
    def _classify_question(message: str) -> set[str]:
        """Return a set of question categories based on keyword matching."""
        # Drop punctuation so trailing keywords like "menu?" still match.
        stripped = re.sub(r"[^\w\s\u0600-\u06FF]", " ", message.lower())
        normalized = " " + re.sub(r"\s+", " ", stripped).strip() + " "
        categories: set[str] = set()
        # Use space-padded matching so Arabic sub-words (e.g. 'اكل' inside 'مشاكل')
        # do not create false positives.
        if any(f" {kw} " in normalized for kw in _MENU_KEYWORDS):
            categories.add("menu")
        if any(f" {kw} " in normalized for kw in _FAQ_KEYWORDS):
            categories.add("faq")
        if any(f" {kw} " in normalized for kw in _ORDER_KEYWORDS):
            categories.add("orders")
        if any(f" {kw} " in normalized for kw in _TICKET_KEYWORDS):
            categories.add("tickets")
        if any(f" {kw} " in normalized for kw in _BEST_SELLER_KEYWORDS):
            categories.add("best_sellers")
        if any(f" {kw} " in normalized for kw in _COMMON_ISSUE_KEYWORDS):
            categories.add("common_issues")
        if any(f" {kw} " in normalized for kw in _REPORT_SUMMARY_KEYWORDS):
            categories.add("report_summary")
        return categories or {"general"}

    @staticmethod
    def _required_metric_keys(category: str) -> list[str]:
        """Return the metric keys that ground a given factual category."""
        mapping = {
            "menu": ["menuItemsList"],
            "faq": ["faqList"],
            "orders": ["ordersToday", "ordersThisWeek", "ordersInPeriod", "totalOrdersDetected"],
            "tickets": ["openTicketsCount", "escalatedTicketsCount", "ticketsThisWeek", "recentOpenTickets"],
            "best_sellers": ["topOrderedItems"],
            "common_issues": ["mostCommonTicketTypes"],
        }
        return mapping.get(category, [])

    @staticmethod
    def _metrics_contain(metrics: dict[str, Any] | None, keys: list[str]) -> bool:
        if not metrics:
            return False
        return any(metrics.get(key) not in (None, [], {}) for key in keys)

    @staticmethod
    def _extract_numbers(text: str) -> list[float]:
        """Extract integer/float tokens from text."""
        numbers: list[float] = []
        for match in re.finditer(r"\b\d+(?:\.\d+)?\b", text):
            try:
                numbers.append(float(match.group()))
            except ValueError:
                continue
        return numbers

    @staticmethod
    def _reply_has_internal_terms(reply: str) -> bool:
        normalized = reply.lower()
        for term in _INTERNAL_TERMS:
            if " " in term:
                if term in normalized:
                    return True
            else:
                if re.search(r"\b" + re.escape(term) + r"\b", normalized):
                    return True
        return False

    @staticmethod
    def _item_names_in_reply(reply: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return menu/top-item dicts whose names appear in the reply."""
        found: list[dict[str, Any]] = []
        reply_lower = reply.lower()
        for item in items:
            name = str(item.get("name", "")).strip()
            if name and name.lower() in reply_lower:
                found.append(item)
        return found

    @staticmethod
    def _safe_missing_reply(is_arabic: bool) -> str:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN

    def _validate_reply_against_context(
        self,
        reply: str,
        categories: set[str],
        metrics: dict[str, Any] | None,
        is_arabic: bool,
    ) -> str:
        """Return the reply if it passes pragmatic grounding checks, otherwise a safe fallback."""
        # Internal implementation terms must never reach the owner.
        if self._reply_has_internal_terms(reply):
            return self._safe_missing_reply(is_arabic)

        menu_items: list[dict[str, Any]] = []
        if isinstance(metrics, dict):
            menu_items = list(metrics.get("menuItemsList") or [])
            top_items = list(metrics.get("topOrderedItems") or [])
            faq_list = list(metrics.get("faqList") or [])
        else:
            menu_items = []
            top_items = []
            faq_list = []

        # Menu / price / availability questions
        if "menu" in categories and menu_items:
            mentioned = self._item_names_in_reply(reply, menu_items)
            reply_prices = self._extract_numbers(reply)
            menu_prices = {
                float(item.get("price", 0))
                for item in menu_items
                if item.get("price") is not None
            }
            # Any price-like number in the reply must match a known menu price.
            for price in reply_prices:
                if price not in menu_prices and price > 10:
                    return self._safe_missing_reply(is_arabic)
            # If specific items are mentioned, availability claims must match.
            if mentioned:
                availability_words = {
                    "available": True, "متاح": True, "available now": True,
                    "unavailable": False, "not available": False, "مش متاح": False,
                    "غير متوفر": False, "not in stock": False,
                }
                for item in mentioned:
                    is_available = bool(item.get("isAvailable", True))
                    for phrase, expected in availability_words.items():
                        if phrase in reply.lower() and is_available != expected:
                            return self._safe_missing_reply(is_arabic)

        # FAQ questions: no hard validation beyond internal-term guard.
        if "faq" in categories and not menu_items and not faq_list:
            return self._safe_missing_reply(is_arabic)

        # Order-count questions
        if "orders" in categories and metrics:
            relevant_values = [
                float(metrics.get(k)) for k in ["ordersToday", "ordersThisWeek", "ordersInPeriod", "totalOrdersDetected"]
                if metrics.get(k) is not None and str(metrics.get(k)).replace(".", "", 1).isdigit()
            ]
            if relevant_values:
                for number in self._extract_numbers(reply):
                    if number not in relevant_values:
                        return self._safe_missing_reply(is_arabic)

        # Ticket questions
        if "tickets" in categories and metrics:
            relevant_values = [
                float(metrics.get(k)) for k in ["openTicketsCount", "escalatedTicketsCount", "ticketsThisWeek"]
                if metrics.get(k) is not None and str(metrics.get(k)).replace(".", "", 1).isdigit()
            ]
            if relevant_values:
                for number in self._extract_numbers(reply):
                    if number not in relevant_values:
                        return self._safe_missing_reply(is_arabic)

        # Best-seller questions
        if "best_sellers" in categories:
            known_items = list(top_items)
            if not known_items:
                return self._safe_missing_reply(is_arabic)
            mentioned = self._item_names_in_reply(reply, known_items)
            if not mentioned and self._extract_numbers(reply):
                return self._safe_missing_reply(is_arabic)

        return reply

    async def process_owner_message(self, request: OwnerChatRequest) -> OwnerChatResponse:
        stored = self.report_service.get_report(request.business_id)
        is_arabic = _contains_arabic(request.message)

        if stored is None:
            logger.warning(
                "Owner chat request for unknown business_id=%s — no synced report found.",
                request.business_id,
            )
            fallback = _NO_REPORT_REPLY_AR if is_arabic else _NO_REPORT_REPLY_EN
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=fallback,
                data_sources_used=[],
                confidence="low",
            )

        categories = self._classify_question(request.message)
        metrics = stored.metrics

        # For factual categories that require raw metrics, fall back safely if the
        # required source is absent. Report-only payloads remain usable for report-style
        # questions (summaries, problems, recommendations, risk), and common-issue
        # questions may fall back to report.problems when metrics are missing.
        factual_metric_categories = {"menu", "faq", "best_sellers", "orders", "tickets", "common_issues"}
        for category in categories & factual_metric_categories:
            if category == "common_issues":
                has_source = (
                    self._metrics_contain(metrics, ["mostCommonTicketTypes"])
                    or bool(stored.report.problems)
                )
            else:
                has_source = self._metrics_contain(metrics, self._required_metric_keys(category))
            if not has_source:
                fallback = self._safe_missing_reply(is_arabic)
                return OwnerChatResponse(
                    business_id=request.business_id,
                    session_id=request.session_id,
                    reply=fallback,
                    data_sources_used=[],
                    confidence="low",
                )

        try:
            report_context = self.report_service.build_prompt_context(stored)
            system_prompt = self._build_system_prompt("ar" if is_arabic else "en")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "system", "content": f"SYNCED_OWNER_CONTEXT:\n{report_context}"},
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
            reply_text = self._validate_reply_against_context(
                reply_text, categories, metrics, is_arabic
            )

            await self._update_history(request.session_id, request.message, reply_text)
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=reply_text,
                data_sources_used=self._infer_data_sources(request.message, reply_text, categories),
                confidence=self._assess_confidence(reply_text),
            )
        except Exception as e:
            logger.error("Error in OwnerChatService.process_owner_message: %s", e)
            error_reply = (
                "معلش، عندي مشكلة في الوصول للبيانات دي دلوقتي. جرب تاني كمان شوية."
                if is_arabic
                else "Sorry, I had trouble accessing the data right now. Please try again in a moment."
            )
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=error_reply,
                data_sources_used=[],
                confidence="low",
            )


    @staticmethod
    def _enforce_reply_language(reply: str, is_arabic: bool) -> str:
        """Safety net: if the LLM returned a no-data phrase in the wrong language, swap it."""
        reply_stripped = reply.strip()

        if is_arabic:
            if reply_stripped == _NO_DATA_REPLY_EN:
                return _NO_DATA_REPLY_AR
        else:
            if reply_stripped == _NO_DATA_REPLY_AR:
                return _NO_DATA_REPLY_EN

        return reply

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


    def _infer_data_sources(
        self,
        question: str,
        answer: str,
        categories: set[str] | None = None,
    ) -> List[str]:
        sources: List[str] = []
        categories = categories or set()
        if "menu" in categories:
            sources.append("metrics.menuItemsList")
        if "faq" in categories:
            sources.append("metrics.faqList")
        if "orders" in categories:
            sources.append("metrics.orderMetrics")
        if "tickets" in categories:
            sources.append("metrics.ticketMetrics")
        if "best_sellers" in categories:
            sources.append("metrics.topOrderedItems")
        if "common_issues" in categories:
            sources.append("metrics.mostCommonTicketTypes")
        if "report_summary" in categories or not sources:
            sources.append("report.sections")
        return sources

    def _assess_confidence(self, reply: str) -> str:
        reply_lower = reply.lower()
        _no_data_phrases = (
            _NO_DATA_REPLY_AR.lower(),
            _NO_DATA_REPLY_EN.lower(),
        )
        if any(phrase in reply_lower for phrase in _no_data_phrases):
            return "low"
        if re.search(r"\d+", reply) or "%" in reply or "egp" in reply_lower:
            return "high"
        return "medium"
