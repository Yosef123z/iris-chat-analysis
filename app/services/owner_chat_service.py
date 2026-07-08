"""Business owner analytics chatbot — driven by backend-synced reports.

Flow
----
1. Load stored owner report by business_id.
   → Missing: return no-report fallback immediately.
2. Classify the user message into a fine-grained intent.
3. FACTUAL METRIC INTENT:
   → Required data exists: build a deterministic answer (no LLM call).
   → Required data missing: return low-confidence fallback (no LLM call).
4. REPORT / ANALYTICAL INTENT:
   → Call the LLM with the full report context, then sanitize/validate the reply.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.conversation import ConversationManager
from app.core.llm_interface import LLMProvider
from app.models.owner_chat import OwnerChatRequest, OwnerChatResponse

if TYPE_CHECKING:
    from app.services.owner_report_service import OwnerReportService, StoredOwnerReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constant reply strings
# ---------------------------------------------------------------------------

_NO_REPORT_REPLY_AR = (
    "معلش يا فندم، مش لاقي تقرير متزامن لأعمالك دي دلوقتي."
)
_NO_REPORT_REPLY_EN = (
    "Sorry, no synced report was found for this business yet."
)

_NO_DATA_REPLY_AR = "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
_NO_DATA_REPLY_EN = "Sorry, that information is not available in the current report."

_INTERNAL_TERMS = {
    "backend", "api", "prompt", "system prompt", "rag", "embeddings",
    "vector search", "vector", "retrieval", "validation layer", "json contract",
    "database", "internal tools", "python", "module",
}

# ---------------------------------------------------------------------------
# Intent constants
# ---------------------------------------------------------------------------

# Factual intents — answered deterministically from metrics
INTENT_MENU_LIST = "MENU_LIST"
INTENT_MENU_PRICE = "MENU_PRICE"
INTENT_MENU_AVAILABILITY = "MENU_AVAILABILITY"
INTENT_MENU_DESCRIPTION = "MENU_DESCRIPTION"
INTENT_MENU_CATEGORY = "MENU_CATEGORY"
INTENT_FAQ_LIST = "FAQ_LIST"
INTENT_FAQ_ANSWER = "FAQ_ANSWER"
INTENT_ORDERS_TODAY = "ORDERS_TODAY"
INTENT_ORDERS_THIS_WEEK = "ORDERS_THIS_WEEK"
INTENT_ORDERS_IN_PERIOD = "ORDERS_IN_PERIOD"
INTENT_ORDERS_TOTAL_DETECTED = "ORDERS_TOTAL_DETECTED"
INTENT_TICKETS_OPEN = "TICKETS_OPEN"
INTENT_TICKETS_ESCALATED = "TICKETS_ESCALATED"
INTENT_TICKETS_THIS_WEEK = "TICKETS_THIS_WEEK"
INTENT_RECENT_OPEN_TICKETS = "RECENT_OPEN_TICKETS"
INTENT_BEST_SELLERS = "BEST_SELLERS"
INTENT_COMMON_ISSUES = "COMMON_ISSUES"

_FACTUAL_INTENTS = {
    INTENT_MENU_LIST, INTENT_MENU_PRICE, INTENT_MENU_AVAILABILITY,
    INTENT_MENU_DESCRIPTION, INTENT_MENU_CATEGORY,
    INTENT_FAQ_LIST, INTENT_FAQ_ANSWER,
    INTENT_ORDERS_TODAY, INTENT_ORDERS_THIS_WEEK,
    INTENT_ORDERS_IN_PERIOD, INTENT_ORDERS_TOTAL_DETECTED,
    INTENT_TICKETS_OPEN, INTENT_TICKETS_ESCALATED,
    INTENT_TICKETS_THIS_WEEK, INTENT_RECENT_OPEN_TICKETS,
    INTENT_BEST_SELLERS, INTENT_COMMON_ISSUES,
}

# Report / LLM intents
INTENT_REPORT_SUMMARY = "REPORT_SUMMARY"
INTENT_REPORT_HIGHLIGHTS = "REPORT_HIGHLIGHTS"
INTENT_REPORT_PROBLEMS = "REPORT_PROBLEMS"
INTENT_REPORT_RECOMMENDATIONS = "REPORT_RECOMMENDATIONS"
INTENT_REPORT_ACTIONS = "REPORT_ACTIONS"
INTENT_REPORT_RISK = "REPORT_RISK"
INTENT_GENERAL_ANALYTICAL = "GENERAL_ANALYTICAL"

# Data source labels used in responses
_DS_MENU = "metrics.menuItemsList"
_DS_FAQ = "metrics.faqList"
_DS_ORDERS = "metrics.orderMetrics"
_DS_TICKETS = "metrics.ticketMetrics"
_DS_TOP_ITEMS = "metrics.topOrderedItems"
_DS_COMMON_ISSUES = "metrics.mostCommonTicketTypes"
_DS_REPORT = "report.sections"


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

def _contains_arabic(text: str) -> bool:
    """Return True if the text contains at least one Arabic Unicode character."""
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def _normalize_text(text: str) -> str:
    """Robust normaliser for both English and Arabic input.

    Steps
    -----
    - lowercase
    - strip Arabic diacritics (harakat + tatweel)
    - normalize Arabic Alef variants (أ إ آ ٱ) → ا
    - normalize ى → ي
    - remove punctuation (keep digits, letters, Arabic chars, spaces)
    - collapse repeated whitespace
    """
    text = text.lower()

    # Arabic diacritics (harakat U+064B..U+065F, U+0670 tatweel U+0640)
    text = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)

    # Alef variants → bare Alef
    text = re.sub(r"[أإآٱ]", "ا", text)

    # ى → ي
    text = text.replace("\u0649", "\u064A")

    # Remove punctuation — keep letters (including Arabic), digits, and spaces
    text = re.sub(r"[^\w\s\u0600-\u06FF]", " ", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _tok(text: str) -> set[str]:
    """Return the set of whitespace-split tokens from normalised text."""
    return set(_normalize_text(text).split())


# ---------------------------------------------------------------------------
# Menu item matching
# ---------------------------------------------------------------------------

def _find_menu_item(
    query: str,
    items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the best-matching menu item for a free-text query.

    Matching tiers (first match wins):
    1. Exact normalised match against item name
    2. Normalised substring match (item name inside query or vice-versa)
    3. Token-overlap: ≥ 2 tokens in common (or all tokens of the shorter string)
    4. Fuzzy: SequenceMatcher ratio ≥ 0.75
    """
    if not items or not query:
        return None

    q_norm = _normalize_text(query)
    q_toks = set(q_norm.split())

    best_item: dict[str, Any] | None = None
    best_ratio: float = 0.0

    for item in items:
        name: str = str(item.get("name", "")).strip()
        if not name:
            continue
        n_norm = _normalize_text(name)
        n_toks = set(n_norm.split())

        # Tier 1: exact
        if q_norm == n_norm:
            return item

        # Tier 2: substring
        if n_norm in q_norm or q_norm in n_norm:
            return item

        # Tier 3: token overlap
        overlap = len(q_toks & n_toks)
        min_len = min(len(q_toks), len(n_toks))
        if min_len > 0 and overlap >= max(2, min_len):
            return item

        # Tier 4: fuzzy
        ratio = difflib.SequenceMatcher(None, q_norm, n_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_item = item

    if best_ratio >= 0.75:
        return best_item
    return None


# ---------------------------------------------------------------------------
# Fine-grained intent classifier
# ---------------------------------------------------------------------------

def _classify_intent(message: str) -> str:
    """Classify the owner message into one of the 23 defined intents.

    Priority order (evaluated top to bottom; first match wins):
    1.  BEST_SELLERS
    2.  COMMON_ISSUES
    3.  MENU_PRICE
    4.  MENU_AVAILABILITY
    5.  MENU_DESCRIPTION
    6.  MENU_CATEGORY
    7.  MENU_LIST
    8.  FAQ_LIST / FAQ_ANSWER
    9.  Order timeframe sub-intents
    10. Ticket sub-intents
    11. Report sub-intents
    12. GENERAL_ANALYTICAL (catch-all)
    """
    n = _normalize_text(message)

    def has(*phrases: str) -> bool:
        # Normalize each pattern so Arabic ى/أ/إ/آ variants in source patterns
        # match the already-normalized input string correctly.
        return any(_normalize_text(p) in n for p in phrases)

    # ------------------------------------------------------------------ #
    # 1. BEST_SELLERS — must precede MENU_LIST even if "item" is present  #
    # ------------------------------------------------------------------ #
    if has(
        "best sell", "bestsell", "best-sell",
        "top sell", "top-sell",
        "top item", "top ordered",
        "most popular", "most ordered",
        "best selling item",
        # Arabic
        "اكتر مبيعا", "الاكثر مبيعا", "اكثر مبيعا",
        "افضل مبيعا", "الافضل مبيعا",
        "الاكثر طلبا", "اكثر طلبا",
        "اكتر صنف", "الاكثر", "الاكثر شعبية",
        "شائع", "الشائع",
    ):
        return INTENT_BEST_SELLERS

    # ------------------------------------------------------------------ #
    # 2. COMMON_ISSUES — most common / frequent / top problem             #
    # Check BEFORE tickets so 'most common complaint/shikawa' routes here #
    # ------------------------------------------------------------------ #
    if has(
        "most common", "common issue", "common complaint", "common problem",
        "frequent issue", "frequent complaint", "frequent problem",
        "top problem", "top complaint", "top issue",
        "repeated issue", "repeated complaint",
        # Arabic — checked before ticket path to avoid шакаwа being eaten by TICKETS
        "اكتر شكوى", "الاكثر شكوى", "اكثر شكوى",
        "اكتر مشكله", "اكثر مشكله", "اكتر مشكلة", "اكثر مشكلة",
        "اشهر شكوى", "الشكوى المتكررة", "الشكاوى الاكثر",
        "متكرر", "المتكررة",
    ):
        return INTENT_COMMON_ISSUES

    # ------------------------------------------------------------------ #
    # 3. MENU_PRICE — before MENU_LIST                                    #
    # ------------------------------------------------------------------ #
    if has(
        "price", "cost", "how much", "how much is", "what is the price",
        "price of", "cost of", "item price", "menu price", "how much does",
        # Arabic
        "سعر", "بكام", "كام", "بقد ايه", "اسعار", "تمن",
    ):
        return INTENT_MENU_PRICE

    # ------------------------------------------------------------------ #
    # 4. MENU_AVAILABILITY — before MENU_LIST                             #
    # Only trigger when NOT asking about 'risk level' or 'what is on'     #
    # ------------------------------------------------------------------ #
    # Guard: 'risk level' should not match 'level' under availability
    if not has("risk level", "risk") and has(
        "is it available", "do you have",
        "in stock", "out of stock",
        # Arabic
        "متاح", "مش متاح", "موجود", "مش موجود",
        "مفيش", "عندك",
    ):
        return INTENT_MENU_AVAILABILITY
    # 'available' / 'availability' by themselves (English) — but not when preceded by 'not'
    # or when the question is about a risk/report context
    if has("available", "availability") and not has("risk", "report", "recommendation"):
        return INTENT_MENU_AVAILABILITY

    # ------------------------------------------------------------------ #
    # 5. MENU_DESCRIPTION — only when asking about a SPECIFIC item,      #
    #    NOT when asking 'what is on the menu' / 'what is available'      #
    # ------------------------------------------------------------------ #
    # Exclude generic menu-list phrases from MENU_DESCRIPTION
    _menu_list_signals = (
        "on the menu", "in the menu", "on your menu",
        "do you have", "what do you have", "what do you offer",
        "what do you sell", "all items", "list",
        "في المنيو", "على المنيو", "عندك ايه", "عندي ايه",
        "موجود ايه", "الاصناف", "الاطباق",
    )
    if has(
        "describe", "description",
        "what contain", "what's in", "whats in",
        "tell me about", "details about",
        # Arabic
        "وصف", "مكونات",
    ) and not any(s in n for s in _menu_list_signals):
        return INTENT_MENU_DESCRIPTION
    # 'what is X' / 'what does X' — only when the query has an item token
    # (more than just 'what is on / what is the price of')
    if has("what is", "what does", "ايه ده", "ايه دي", "ايه هو", "ايه هي") and not has(
        "the price", "the cost", "the risk", "the summary",
        "on the menu", "in the menu", "available", "delivery",
        "working hours", "opening hours",
    ) and not any(s in n for s in _menu_list_signals):
        return INTENT_MENU_DESCRIPTION

    # ------------------------------------------------------------------ #
    # 6. MENU_CATEGORY                                                    #
    # ------------------------------------------------------------------ #
    if has(
        "category", "categories", "type of", "what type",
        # Arabic
        "تصنيف", "قسم", "نوع",
    ):
        return INTENT_MENU_CATEGORY

    # ------------------------------------------------------------------ #
    # 7. MENU_LIST                                                        #
    # ------------------------------------------------------------------ #
    if has(
        "menu", "catalog", "what do i have", "what do you have",
        "what do you offer", "what do you sell",
        "list of items", "list my items", "show menu",
        "all items", "what items",
        # Arabic
        "منيو", "الاصناف", "صنف", "الاكل", "اكل",
        "عندي ايه", "عندك ايه", "موجود ايه",
        "الاطباق", "طبق",
    ):
        return INTENT_MENU_LIST

    # ------------------------------------------------------------------ #
    # 8. FAQ                                                              #
    # ------------------------------------------------------------------ #
    if has(
        "faq", "frequently asked", "common question",
        "working hours", "opening hours", "delivery time", "return policy",
        "refund", "policy",
        # Arabic
        "سؤال", "اسئلة", "سياسة", "مواعيد", "ساعات",
        "استرجاع", "استرداد",
    ):
        # If there seems to be a specific question query, use FAQ_ANSWER
        return INTENT_FAQ_ANSWER if len(message.strip()) > 20 else INTENT_FAQ_LIST  # noqa: PLR2004

    # ------------------------------------------------------------------ #
    # 9. Orders — timeframe sub-intents                                   #
    # ------------------------------------------------------------------ #
    is_order = has(
        "order", "orders", "request", "requests",
        "طلب", "طلبات", "اوردر",
    )
    if is_order:
        if has("today", "النهارده", "اليوم"):
            return INTENT_ORDERS_TODAY
        if has("this week", "الاسبوع", "الاسبوع الحالي", "هذا الاسبوع"):
            return INTENT_ORDERS_THIS_WEEK
        if has("total detected", "total orders"):
            return INTENT_ORDERS_TOTAL_DETECTED
        if has(
            "period", "this period", "month", "this month",
            "report period", "الفتره", "الشهر",
        ):
            return INTENT_ORDERS_IN_PERIOD
        # Generic order question → use most inclusive field
        return INTENT_ORDERS_TODAY

    # ------------------------------------------------------------------ #
    # 10. Tickets — sub-intents                                           #
    # ------------------------------------------------------------------ #
    is_ticket = has(
        "ticket", "tickets", "complaint", "complaints",
        "تذكره", "تذاكر", "شكوى", "شكاوى",
    )
    if is_ticket:
        if has("escalat", "مرفوع", "متصاعد"):
            return INTENT_TICKETS_ESCALATED
        if has("this week", "الاسبوع"):
            return INTENT_TICKETS_THIS_WEEK
        if has("recent", "latest", "last", "اخر", "احدث"):
            return INTENT_RECENT_OPEN_TICKETS
        # Default: open tickets count
        return INTENT_TICKETS_OPEN

    # ------------------------------------------------------------------ #
    # 11. Report / analytical intents                                     #
    # ------------------------------------------------------------------ #
    if has("summary", "summarize", "overview", "ملخص", "نظره عامه", "نظرة عامة"):
        return INTENT_REPORT_SUMMARY
    if has("highlight", "مميزات", "ابرز"):
        return INTENT_REPORT_HIGHLIGHTS
    if has("problem", "issue", "مشكله", "مشاكل"):
        return INTENT_REPORT_PROBLEMS
    if has(
        "recommend", "suggestion", "suggest",
        "توصيه", "توصيات", "اقتراح",
    ):
        return INTENT_REPORT_RECOMMENDATIONS
    if has("action", "next step", "خطوه", "خطوات"):
        return INTENT_REPORT_ACTIONS
    if has("risk", "خطر", "مخاطر"):
        return INTENT_REPORT_RISK

    return INTENT_GENERAL_ANALYTICAL


# ---------------------------------------------------------------------------
# Deterministic answer builder
# ---------------------------------------------------------------------------

def _build_deterministic_answer(
    intent: str,
    message: str,
    stored: "StoredOwnerReport",
    is_arabic: bool,
) -> Tuple[Optional[str], List[str], str]:
    """Build a deterministic reply from stored metrics.

    Returns
    -------
    (reply_text, data_sources, confidence)
    reply_text is None when data is missing and caller should return fallback.
    """
    metrics: dict[str, Any] = stored.metrics or {}

    # ------------------------------------------------------------------ #
    # MENU intents                                                        #
    # ------------------------------------------------------------------ #
    if intent in (INTENT_MENU_LIST, INTENT_MENU_PRICE,
                  INTENT_MENU_AVAILABILITY, INTENT_MENU_DESCRIPTION,
                  INTENT_MENU_CATEGORY):
        items: list[dict[str, Any]] = list(metrics.get("menuItemsList") or [])
        if not items:
            return None, [], "low"

        if intent == INTENT_MENU_LIST:
            return _answer_menu_list(items, is_arabic), [_DS_MENU], "high"

        if intent == INTENT_MENU_PRICE:
            return _answer_menu_price(message, items, is_arabic), [_DS_MENU], "high"

        if intent == INTENT_MENU_AVAILABILITY:
            return _answer_menu_availability(message, items, is_arabic), [_DS_MENU], "high"

        if intent == INTENT_MENU_DESCRIPTION:
            return _answer_menu_description(message, items, is_arabic), [_DS_MENU], "high"

        if intent == INTENT_MENU_CATEGORY:
            return _answer_menu_category(message, items, is_arabic), [_DS_MENU], "high"

    # ------------------------------------------------------------------ #
    # FAQ intents                                                         #
    # ------------------------------------------------------------------ #
    if intent in (INTENT_FAQ_LIST, INTENT_FAQ_ANSWER):
        faqs: list[dict[str, Any]] = list(metrics.get("faqList") or [])
        if not faqs:
            return None, [], "low"

        if intent == INTENT_FAQ_LIST:
            questions = [str(f.get("question", "")).strip() for f in faqs if f.get("question")]
            if is_arabic:
                body = " / ".join(questions)
                return f"الأسئلة المتاحة: {body}", [_DS_FAQ], "high"
            return f"Available FAQ topics: {', '.join(questions)}.", [_DS_FAQ], "high"

        # FAQ_ANSWER — find best match
        return _answer_faq(message, faqs, is_arabic), [_DS_FAQ], "high"

    # ------------------------------------------------------------------ #
    # Order intents                                                       #
    # ------------------------------------------------------------------ #
    if intent == INTENT_ORDERS_TODAY:
        val = metrics.get("ordersToday")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"النهارده عندك {val} طلب.", [_DS_ORDERS], "high"
        return f"You received {val} orders today.", [_DS_ORDERS], "high"

    if intent == INTENT_ORDERS_THIS_WEEK:
        val = metrics.get("ordersThisWeek")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"الأسبوع ده عندك {val} طلب.", [_DS_ORDERS], "high"
        return f"You received {val} orders this week.", [_DS_ORDERS], "high"

    if intent == INTENT_ORDERS_IN_PERIOD:
        val = metrics.get("ordersInPeriod")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"في الفترة دي عندك {val} طلب.", [_DS_ORDERS], "high"
        return f"You received {val} orders in this reporting period.", [_DS_ORDERS], "high"

    if intent == INTENT_ORDERS_TOTAL_DETECTED:
        val = metrics.get("totalOrdersDetected")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"إجمالي الطلبات المرصودة {val} طلب.", [_DS_ORDERS], "high"
        return f"Total orders detected: {val}.", [_DS_ORDERS], "high"

    # ------------------------------------------------------------------ #
    # Ticket intents                                                      #
    # ------------------------------------------------------------------ #
    if intent == INTENT_TICKETS_OPEN:
        val = metrics.get("openTicketsCount")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"عندك {val} تذكرة مفتوحة دلوقتي.", [_DS_TICKETS], "high"
        return f"You currently have {val} open ticket(s).", [_DS_TICKETS], "high"

    if intent == INTENT_TICKETS_ESCALATED:
        val = metrics.get("escalatedTicketsCount")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"عدد التذاكر المتصاعدة {val}.", [_DS_TICKETS], "high"
        return f"There are {val} escalated ticket(s).", [_DS_TICKETS], "high"

    if intent == INTENT_TICKETS_THIS_WEEK:
        val = metrics.get("ticketsThisWeek")
        if val is None:
            return None, [], "low"
        if is_arabic:
            return f"الأسبوع ده جه {val} تذكرة.", [_DS_TICKETS], "high"
        return f"You received {val} ticket(s) this week.", [_DS_TICKETS], "high"

    if intent == INTENT_RECENT_OPEN_TICKETS:
        tickets_list: list[dict[str, Any]] = list(metrics.get("recentOpenTickets") or [])
        if not tickets_list:
            return None, [], "low"
        subjects = [str(t.get("subject", "")).strip() for t in tickets_list if t.get("subject")]
        if is_arabic:
            body = " / ".join(subjects) if subjects else "لا توجد تفاصيل"
            return f"آخر التذاكر المفتوحة: {body}.", [_DS_TICKETS], "high"
        body = ", ".join(subjects) if subjects else "no details available"
        return f"Recent open tickets: {body}.", [_DS_TICKETS], "high"

    # ------------------------------------------------------------------ #
    # BEST_SELLERS                                                        #
    # ------------------------------------------------------------------ #
    if intent == INTENT_BEST_SELLERS:
        top_items: list[dict[str, Any]] = list(metrics.get("topOrderedItems") or [])
        if not top_items:
            return None, [], "low"
        return _answer_best_sellers(top_items, is_arabic), [_DS_TOP_ITEMS], "high"

    # ------------------------------------------------------------------ #
    # COMMON_ISSUES                                                       #
    # ------------------------------------------------------------------ #
    if intent == INTENT_COMMON_ISSUES:
        common: list[dict[str, Any]] = list(metrics.get("mostCommonTicketTypes") or [])
        if common:
            return _answer_common_issues_from_metrics(common, is_arabic), [_DS_COMMON_ISSUES], "high"
        # Fall back to report.problems
        problems = list(stored.report.problems or [])
        if problems:
            titles = [p.title for p in problems if p.title]
            if is_arabic:
                body = " / ".join(titles)
                return (
                    f"أكتر المشاكل الموجودة في التقرير: {body}.",
                    [_DS_REPORT],
                    "medium",
                )
            return (
                f"The most reported issues: {', '.join(titles)}.",
                [_DS_REPORT],
                "medium",
            )
        # Neither source has data
        return None, [], "low"

    # Should not reach here for factual intents
    return None, [], "low"


# ---- sub-answer helpers ---------------------------------------------------

def _answer_menu_list(items: list[dict[str, Any]], is_arabic: bool) -> str:
    names = [str(i.get("name", "")).strip() for i in items if i.get("name")]
    if is_arabic:
        body = " / ".join(names)
        return f"الأصناف المتاحة في المنيو: {body}."
    return f"Menu items available: {', '.join(names)}."


def _answer_menu_price(
    message: str, items: list[dict[str, Any]], is_arabic: bool
) -> str:
    item = _find_menu_item(message, items)
    if item is None:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    name = str(item.get("name", "")).strip()
    price = item.get("price")
    if price is None:
        if is_arabic:
            return f"معلش، مش عارف سعر {name} دلوقتي."
        return f"Sorry, the price of {name} is not available right now."
    if is_arabic:
        return f"سعر {name} هو {price} جنيه."
    return f"The price of {name} is {price} EGP."


def _answer_menu_availability(
    message: str, items: list[dict[str, Any]], is_arabic: bool
) -> str:
    item = _find_menu_item(message, items)
    if item is None:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    name = str(item.get("name", "")).strip()
    is_avail = bool(item.get("isAvailable", True))
    if is_arabic:
        status = "متاح" if is_avail else "مش متاح"
        return f"{name} {status} دلوقتي."
    status = "available" if is_avail else "not available"
    return f"{name} is {status} right now."


def _answer_menu_description(
    message: str, items: list[dict[str, Any]], is_arabic: bool
) -> str:
    item = _find_menu_item(message, items)
    if item is None:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    name = str(item.get("name", "")).strip()
    desc = str(item.get("description", "")).strip()
    if not desc:
        if is_arabic:
            return f"معلش، مش متاح وصف لـ {name} دلوقتي."
        return f"No description available for {name}."
    if is_arabic:
        return f"{name}: {desc}."
    return f"{name}: {desc}."


def _answer_menu_category(
    message: str, items: list[dict[str, Any]], is_arabic: bool
) -> str:
    item = _find_menu_item(message, items)
    if item is None:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    name = str(item.get("name", "")).strip()
    cat = str(item.get("category", "")).strip()
    if not cat:
        if is_arabic:
            return f"مش عارف تصنيف {name} دلوقتي."
        return f"Category for {name} is not specified."
    if is_arabic:
        return f"{name} في تصنيف {cat}."
    return f"{name} belongs to the {cat} category."


def _answer_faq(
    message: str, faqs: list[dict[str, Any]], is_arabic: bool
) -> str:
    q_norm = _normalize_text(message)
    best_match: dict[str, Any] | None = None
    best_ratio = 0.0
    for faq in faqs:
        faq_q_norm = _normalize_text(str(faq.get("question", "")))
        ratio = difflib.SequenceMatcher(None, q_norm, faq_q_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = faq
    if best_match and best_ratio >= 0.3:  # noqa: PLR2004
        answer = str(best_match.get("answer", "")).strip()
        if answer:
            return answer
    return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN


def _answer_best_sellers(items: list[dict[str, Any]], is_arabic: bool) -> str:
    # Sort by quantity / count / revenue descending
    def _sort_key(i: dict[str, Any]) -> float:
        for k in ("quantitySold", "quantity", "count", "revenue"):
            v = i.get(k)
            if v is not None:
                try:
                    return -float(v)
                except (ValueError, TypeError):
                    pass
        return 0.0

    sorted_items = sorted(items, key=_sort_key)
    top3 = sorted_items[:3]
    names = [str(i.get("name", "")).strip() for i in top3 if i.get("name")]
    if not names:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    if is_arabic:
        body = " / ".join(names)
        return f"أكتر الأصناف مبيعاً: {body}."
    return f"Best-selling items: {', '.join(names)}."


def _answer_common_issues_from_metrics(
    common: list[dict[str, Any]], is_arabic: bool
) -> str:
    def _sort_key(i: dict[str, Any]) -> float:
        try:
            return -float(i.get("count", 0))
        except (ValueError, TypeError):
            return 0.0

    sorted_issues = sorted(common, key=_sort_key)
    labels: list[str] = []
    for issue in sorted_issues[:3]:
        label = str(issue.get("name") or issue.get("type") or "").strip()
        if label:
            labels.append(label)
    if not labels:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
    if is_arabic:
        body = " / ".join(labels)
        return f"أكتر المشاكل الشائعة: {body}."
    return f"Most common issues: {', '.join(labels)}."


# ---------------------------------------------------------------------------
# Main service class
# ---------------------------------------------------------------------------

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

    async def process_owner_message(self, request: OwnerChatRequest) -> OwnerChatResponse:
        stored = self.report_service.get_report(request.business_id)
        is_arabic = _contains_arabic(request.message)

        # ------------------------------------------------------------------ #
        # Step 1: No stored report — return fallback immediately             #
        # ------------------------------------------------------------------ #
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

        # ------------------------------------------------------------------ #
        # Step 2: Classify intent                                             #
        # ------------------------------------------------------------------ #
        intent = _classify_intent(request.message)
        logger.debug("Owner chat intent=%s for message=%r", intent, request.message[:80])

        # ------------------------------------------------------------------ #
        # Step 3: Deterministic path for factual intents                     #
        # ------------------------------------------------------------------ #
        if intent in _FACTUAL_INTENTS:
            det_text, det_sources, det_conf = _build_deterministic_answer(
                intent, request.message, stored, is_arabic
            )
            if det_text is None:
                # Required data is missing
                fallback = _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN
                return OwnerChatResponse(
                    business_id=request.business_id,
                    session_id=request.session_id,
                    reply=fallback,
                    data_sources_used=[],
                    confidence="low",
                )
            # Deterministic answer ready — sanitize and return without LLM
            det_text = self._sanitize_reply(det_text)
            await self._update_history(request.session_id, request.message, det_text)
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=det_text,
                data_sources_used=det_sources,
                confidence=det_conf,
            )

        # ------------------------------------------------------------------ #
        # Step 4: LLM path for report / analytical intents                   #
        # ------------------------------------------------------------------ #
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
                reply_text, intent, stored.metrics, is_arabic
            )

            await self._update_history(request.session_id, request.message, reply_text)
            data_sources = self._infer_data_sources_for_llm_reply(intent)
            return OwnerChatResponse(
                business_id=request.business_id,
                session_id=request.session_id,
                reply=reply_text,
                data_sources_used=data_sources,
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

    # ------------------------------------------------------------------ #
    # Reply helpers (kept for LLM path)                                   #
    # ------------------------------------------------------------------ #

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

    def _validate_reply_against_context(
        self,
        reply: str,
        intent: str,
        metrics: dict[str, Any] | None,
        is_arabic: bool,
    ) -> str:
        """Validate LLM-generated replies (used only for report/analytical path).

        Checks
        ------
        1. Internal implementation terms → safe fallback.
        2. Common-issues from report.problems derived answer → pass through (medium confidence).
        3. No brittle numeric price / availability checks (those are for deterministic path only).
        """
        # Guard: internal terms must never reach the owner
        if self._reply_has_internal_terms(reply):
            return self._safe_missing_reply(is_arabic)

        return reply

    @staticmethod
    def _safe_missing_reply(is_arabic: bool) -> str:
        return _NO_DATA_REPLY_AR if is_arabic else _NO_DATA_REPLY_EN

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
        """Strip markdown noise, stray backslashes, and Fosha words from replies."""
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

    @staticmethod
    def _infer_data_sources_for_llm_reply(intent: str) -> List[str]:
        """Return data_sources_used for LLM-generated (report/analytical) replies."""
        # Common-issues from report.problems is the only factual intent that reaches LLM
        if intent == INTENT_COMMON_ISSUES:
            return [_DS_REPORT]
        # All report/analytical intents
        return [_DS_REPORT]

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

    # ------------------------------------------------------------------ #
    # Legacy helpers — kept for backward compatibility with existing tests #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _classify_question(message: str) -> set[str]:
        """Legacy broad-category classifier retained for tests that import it directly."""
        intent = _classify_intent(message)
        category_map: dict[str, str] = {
            INTENT_MENU_LIST: "menu",
            INTENT_MENU_PRICE: "menu",
            INTENT_MENU_AVAILABILITY: "menu",
            INTENT_MENU_DESCRIPTION: "menu",
            INTENT_MENU_CATEGORY: "menu",
            INTENT_FAQ_LIST: "faq",
            INTENT_FAQ_ANSWER: "faq",
            INTENT_ORDERS_TODAY: "orders",
            INTENT_ORDERS_THIS_WEEK: "orders",
            INTENT_ORDERS_IN_PERIOD: "orders",
            INTENT_ORDERS_TOTAL_DETECTED: "orders",
            INTENT_TICKETS_OPEN: "tickets",
            INTENT_TICKETS_ESCALATED: "tickets",
            INTENT_TICKETS_THIS_WEEK: "tickets",
            INTENT_RECENT_OPEN_TICKETS: "tickets",
            INTENT_BEST_SELLERS: "best_sellers",
            INTENT_COMMON_ISSUES: "common_issues",
            INTENT_REPORT_SUMMARY: "report_summary",
            INTENT_REPORT_HIGHLIGHTS: "report_summary",
            INTENT_REPORT_PROBLEMS: "report_summary",
            INTENT_REPORT_RECOMMENDATIONS: "report_summary",
            INTENT_REPORT_ACTIONS: "report_summary",
            INTENT_REPORT_RISK: "report_summary",
            INTENT_GENERAL_ANALYTICAL: "general",
        }
        category = category_map.get(intent, "general")
        return {category}

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

    def _infer_data_sources(
        self,
        question: str,
        answer: str,
        categories: set[str] | None = None,
    ) -> List[str]:
        """Legacy data-source inference kept for backward compatibility."""
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
