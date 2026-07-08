"""
Tests for the deterministic factual answer layer of Owner Chat.

Covers all 16 required tests plus edge-case variants:
1.  English menu list returns menu items from metrics.menuItemsList without LLM.
2.  Menu list containing a 330ml description does not trigger fallback.
3.  English price question returns exact price without LLM.
4.  English availability question for unavailable Pepsi returns "not available" without LLM.
5.  Arabic menu question returns Egyptian Arabic answer; data_sources_used includes metrics.menuItemsList.
6.  Best-selling item returns Classic Smash Burger from metrics.topOrderedItems without LLM.
7.  Best-seller quantitySold/count does NOT trigger price-validation fallback.
8.  Orders today/week/period route to correct metric fields without LLM.
9.  Open/escalated tickets route to correct metric fields without LLM.
10. Common issues use metrics.mostCommonTicketTypes when available.
11. Common issues fall back to report.problems when mostCommonTicketTypes is missing.
12. Common issues return low-confidence fallback when both metrics and report.problems are missing.
13. Unknown item price/availability returns low-confidence fallback without LLM.
14. Report recommendation/risk/summary questions still use LLM/report.sections.
15. Customer Chat regression: /api/v1/chat still works independently.
16. Old owner report sync payloads without metrics still work.
"""

import pytest

from tests.conftest import make_sync_payload, make_sync_payload_with_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def owner_chat(client, business_id, session_id, message, *, expected_status=200):
    response = client.post(
        "/api/v1/owner/chat",
        json={
            "business_id": business_id,
            "session_id": session_id,
            "message": message,
        },
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def sync_report(client, payload: dict) -> None:
    response = client.post("/api/v1/owner/reports/sync", json=payload)
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Test 1 — English menu list from metrics, no LLM call
# ---------------------------------------------------------------------------


def test_menu_list_english_no_llm(client, fake_provider):
    """Menu list must come deterministically from metrics.menuItemsList without calling LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(client, "biz-restaurant-demo", "det-1", "What do I have in the menu?")

    assert len(fake_provider.chat_calls) == 0, "LLM must NOT be called for menu list"
    assert data["confidence"] == "high"
    assert "metrics.menuItemsList" in data["data_sources_used"]
    # All fixture items must appear in the reply
    for name in ("Classic Burger", "Crispy Chicken Burger", "Lemon Mint", "Pepsi"):
        assert name in data["reply"], f"Expected {name!r} in reply"


# ---------------------------------------------------------------------------
# Test 2 — 330ml in description must NOT trigger numeric fallback
# ---------------------------------------------------------------------------


def test_menu_list_330ml_description_no_fallback(client, fake_provider):
    """A menu item description containing '330ml' must not trigger any price-validation fallback."""
    sync_report(client, make_sync_payload_with_metrics())

    # Ask specifically about the Pepsi item which has '330ml' in its description
    data = owner_chat(
        client, "biz-restaurant-demo", "det-2", "Tell me about Pepsi"
    )

    assert len(fake_provider.chat_calls) == 0
    # The reply must NOT be the low-confidence fallback
    assert "Sorry, that information is not available" not in data["reply"]
    assert "معلش" not in data["reply"] or "330ml" in data["reply"] or "Pepsi" in data["reply"]
    # Must include the item name
    assert "Pepsi" in data["reply"]


# ---------------------------------------------------------------------------
# Test 3 — Price question returns exact price, no LLM call
# ---------------------------------------------------------------------------


def test_menu_price_english_no_llm(client, fake_provider):
    """A price question must return the exact price from metrics.menuItemsList without LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-3", "How much is Classic Burger?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "120" in data["reply"]
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 4 — Pepsi availability: not available, no LLM call
# ---------------------------------------------------------------------------


def test_pepsi_availability_not_available_no_llm(client, fake_provider):
    """Asking about Pepsi availability must return 'not available' deterministically."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-4", "Is Pepsi available?"
    )

    assert len(fake_provider.chat_calls) == 0
    reply_lower = data["reply"].lower()
    # Must say 'not available' — must NOT misclassify as 'available'
    assert "not available" in reply_lower or "مش متاح" in data["reply"]
    assert "available" in reply_lower  # word exists in the 'not available' phrase
    # Double-check we didn't accidentally say it IS available
    # (i.e., the reply doesn't end in just 'available' without 'not')
    assert not reply_lower.endswith("is available.")
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 5 — Arabic menu question returns Egyptian Arabic, correct source
# ---------------------------------------------------------------------------


def test_arabic_menu_question_returns_arabic_answer(client, fake_provider):
    """Arabic menu question must yield an Egyptian Arabic reply from metrics.menuItemsList."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-5",
        "إيه الأصناف اللي عندي في المنيو؟",
    )

    assert len(fake_provider.chat_calls) == 0
    # Reply must contain Arabic characters
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"]), "Reply must be in Arabic"
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 6 — Best-selling item is Classic Smash Burger, no LLM call
# ---------------------------------------------------------------------------


def test_best_seller_returns_classic_smash_burger_no_llm(client, fake_provider):
    """Best-seller question must return Classic Smash Burger from topOrderedItems without LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-6", "What is my best-selling item?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "Classic Smash Burger" in data["reply"]
    assert "metrics.topOrderedItems" in data["data_sources_used"]
    assert data["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 7 — quantitySold / count must NOT trigger menu price fallback
# ---------------------------------------------------------------------------


def test_best_seller_quantity_sold_no_price_fallback(client, fake_provider):
    """quantitySold values in topOrderedItems must not be rejected as invalid menu prices."""
    # Fixture has quantitySold=42 and quantitySold=30 — neither is a menu price
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-7", "What is the most popular item?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert data["confidence"] == "high"
    assert "Sorry" not in data["reply"]
    assert "Classic Smash Burger" in data["reply"]


# ---------------------------------------------------------------------------
# Test 8 — Orders route to correct fields
# ---------------------------------------------------------------------------


def test_orders_today_routes_to_correct_field(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-8a", "How many orders today?")

    assert len(fake_provider.chat_calls) == 0
    assert "12" in data["reply"]  # fixture: ordersToday=12
    assert "metrics.orderMetrics" in data["data_sources_used"]


def test_orders_this_week_routes_to_correct_field(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-8b", "How many orders this week?")

    assert len(fake_provider.chat_calls) == 0
    assert "45" in data["reply"]  # fixture: ordersThisWeek=45
    assert "metrics.orderMetrics" in data["data_sources_used"]


def test_orders_in_period_routes_to_correct_field(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(
        client, "biz-restaurant-demo", "det-8c", "How many orders in this period?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "60" in data["reply"]  # fixture: ordersInPeriod=60
    assert "metrics.orderMetrics" in data["data_sources_used"]


# ---------------------------------------------------------------------------
# Test 9 — Tickets route to correct fields
# ---------------------------------------------------------------------------


def test_open_tickets_route_to_correct_field(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(
        client, "biz-restaurant-demo", "det-9a", "How many open tickets do I have?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "3" in data["reply"]  # fixture: openTicketsCount=3
    assert "metrics.ticketMetrics" in data["data_sources_used"]


def test_escalated_tickets_route_to_correct_field(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(
        client, "biz-restaurant-demo", "det-9b", "How many escalated tickets?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "1" in data["reply"]  # fixture: escalatedTicketsCount=1
    assert "metrics.ticketMetrics" in data["data_sources_used"]


# ---------------------------------------------------------------------------
# Test 10 — Common issues use metrics.mostCommonTicketTypes
# ---------------------------------------------------------------------------


def test_common_issues_use_most_common_ticket_types(client, fake_provider):
    """Common-issue question must use metrics.mostCommonTicketTypes deterministically."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-10", "What is the most common complaint?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "cold food" in data["reply"].lower()
    assert "metrics.mostCommonTicketTypes" in data["data_sources_used"]
    assert data["confidence"] == "high"


# ---------------------------------------------------------------------------
# Test 11 — Common issues fall back to report.problems
# ---------------------------------------------------------------------------


def test_common_issues_fall_back_to_report_problems(client, fake_provider):
    """When mostCommonTicketTypes is missing, use report.problems via deterministic path."""
    payload = make_sync_payload_with_metrics(mostCommonTicketTypes=None)
    payload["report"]["problems"] = [
        {
            "title": "Late deliveries",
            "description": "Orders are arriving late.",
            "severity": "high",
            "evidence": ["5 complaints"],
        }
    ]
    sync_report(client, payload)

    data = owner_chat(
        client, "biz-restaurant-demo", "det-11", "What is the most common issue?"
    )

    # Deterministic path from report.problems — no LLM call
    assert len(fake_provider.chat_calls) == 0
    assert "late" in data["reply"].lower() or "delivery" in data["reply"].lower()
    assert data["confidence"] in {"high", "medium"}


# ---------------------------------------------------------------------------
# Test 12 — Common issues: low confidence when both sources are missing
# ---------------------------------------------------------------------------


def test_common_issues_low_confidence_when_both_missing(client, fake_provider):
    """When both mostCommonTicketTypes and report.problems are absent, return low-confidence fallback."""
    payload = make_sync_payload_with_metrics(mostCommonTicketTypes=None)
    payload["report"]["problems"] = []
    sync_report(client, payload)

    data = owner_chat(
        client, "biz-restaurant-demo", "det-12", "What is the most common issue?"
    )

    assert data["confidence"] == "low"
    assert len(fake_provider.chat_calls) == 0


# ---------------------------------------------------------------------------
# Test 13 — Unknown item price/availability → low-confidence fallback, no LLM
# ---------------------------------------------------------------------------


def test_unknown_item_price_returns_low_confidence_no_llm(client, fake_provider):
    """Asking the price of an item not in the menu must return a fallback without LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-13a",
        "How much is Spaghetti Carbonara?",
    )

    assert len(fake_provider.chat_calls) == 0
    # Reply must be a no-data fallback (not a hallucinated price)
    assert "sorry" in data["reply"].lower() or "not available" in data["reply"].lower()
    assert data["confidence"] == "low"
    assert data["data_sources_used"] == []


def test_unknown_item_availability_returns_low_confidence_no_llm(client, fake_provider):
    """Asking availability of an unknown item must return fallback without LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-13b",
        "Is Truffle Pizza available?",
    )

    assert len(fake_provider.chat_calls) == 0
    assert "sorry" in data["reply"].lower() or "not available" in data["reply"].lower()
    assert data["confidence"] == "low"
    assert data["data_sources_used"] == []


def test_unknown_faq_policy_returns_low_confidence_no_llm(client, fake_provider):
    """Asking about an unlisted FAQ policy must return fallback without LLM."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-13c",
        "What is the pet policy?",
    )

    assert len(fake_provider.chat_calls) == 0
    assert data["confidence"] == "low"
    assert data["data_sources_used"] == []


# ---------------------------------------------------------------------------
# Test 14 — Report analytical questions use LLM / report.sections
# ---------------------------------------------------------------------------


def test_report_summary_uses_llm(client, fake_provider):
    """Summary questions must reach the LLM and use report.sections."""
    sync_report(client, make_sync_payload_with_metrics())
    fake_provider.owner_chat_outputs.append(
        "Overall, the business performed well with delivery issues to address."
    )

    data = owner_chat(
        client, "biz-restaurant-demo", "det-14a", "Can you summarize the report?"
    )

    assert len(fake_provider.chat_calls) == 1, "LLM must be called for report summary"
    assert "report.sections" in data["data_sources_used"]


def test_report_recommendations_uses_llm(client, fake_provider):
    """Recommendation questions must reach the LLM."""
    sync_report(client, make_sync_payload_with_metrics())
    fake_provider.owner_chat_outputs.append(
        "I recommend improving the delivery packaging process."
    )

    data = owner_chat(
        client, "biz-restaurant-demo", "det-14b", "What are your recommendations?"
    )

    assert len(fake_provider.chat_calls) == 1
    assert "report.sections" in data["data_sources_used"]


def test_report_risk_uses_llm(client, fake_provider):
    """Risk questions must reach the LLM."""
    sync_report(client, make_sync_payload_with_metrics())
    fake_provider.owner_chat_outputs.append("The risk level is medium due to delivery complaints.")

    data = owner_chat(
        client, "biz-restaurant-demo", "det-14c", "What is the risk level?"
    )

    assert len(fake_provider.chat_calls) == 1
    assert "report.sections" in data["data_sources_used"]


# ---------------------------------------------------------------------------
# Test 15 — Customer Chat regression
# ---------------------------------------------------------------------------


def test_customer_chat_regression(client, fake_provider):
    """/api/v1/chat must remain completely unaffected by owner chat changes."""
    from tests.conftest import llm_chat_output

    client.post(
        "/api/v1/business/knowledge-base/sync",
        json={
            "business_id": "det-reg-biz",
            "business_name": "Regression Biz",
            "knowledge_base": {
                "menu_items": [
                    {
                        "menu_item_id": "item-1",
                        "name": "Reg Burger",
                        "description": "Test item",
                        "price": 100,
                        "category": "Main",
                        "is_available": True,
                    }
                ],
                "faqs": [],
            },
        },
    )
    fake_provider.chat_outputs.append(llm_chat_output(reply="Reg Burger is available."))

    response = client.post(
        "/api/v1/chat",
        json={
            "business_id": "det-reg-biz",
            "session_id": "reg-s1",
            "message": "What do you have?",
        },
    )
    assert response.status_code == 200
    assert "Reg Burger" in response.json()["reply"]


# ---------------------------------------------------------------------------
# Test 16 — Old sync payloads without metrics still work
# ---------------------------------------------------------------------------


def test_old_sync_payload_without_metrics_works(client, fake_provider):
    """Payloads without a 'metrics' field (old backend) must not crash and must handle
    factual questions with low-confidence fallback and analytical questions via LLM."""
    # make_sync_payload() does NOT include a 'metrics' key
    payload = make_sync_payload("biz-legacy", "Legacy Restaurant")
    assert "metrics" not in payload, "make_sync_payload must not include metrics"
    sync_report(client, payload)

    # Factual question → no metrics → low-confidence fallback, no LLM
    data = owner_chat(client, "biz-legacy", "det-16a", "What do I have in the menu?")
    assert data["confidence"] == "low"
    assert len(fake_provider.chat_calls) == 0

    # Analytical question → LLM is called as before
    fake_provider.owner_chat_outputs.append("Delivery was the main issue this period.")
    data = owner_chat(client, "biz-legacy", "det-16b", "Summarize the report.")
    assert len(fake_provider.chat_calls) == 1
    assert "report.sections" in data["data_sources_used"]


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_best_seller_arabic_no_llm(client, fake_provider):
    """Arabic best-seller question must use deterministic path."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-extra-1",
        "إيه أكتر صنف مبيعاً عندي؟",
    )

    assert len(fake_provider.chat_calls) == 0
    # Reply must be in Arabic
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
    assert "metrics.topOrderedItems" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_availability_not_misidentified_as_available(client, fake_provider):
    """'not available' must never be mistaken for 'available'."""
    sync_report(client, make_sync_payload_with_metrics())

    # Pepsi is isAvailable=False
    data = owner_chat(
        client, "biz-restaurant-demo", "det-extra-2", "Is Pepsi available right now?"
    )

    assert len(fake_provider.chat_calls) == 0
    reply_lower = data["reply"].lower()
    # Must NOT contain bare "is available" without "not"
    # Simple check: the phrase "not available" or Arabic equivalent must appear
    assert "not available" in reply_lower or "مش متاح" in data["reply"]
    # And "is available" without negation must NOT appear alone
    import re
    positive_only = re.sub(r"not available", "", reply_lower)
    assert "is available" not in positive_only


def test_item_price_lemon_mint(client, fake_provider):
    """Price for Lemon Mint (45 EGP) must be returned deterministically."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client, "biz-restaurant-demo", "det-extra-3", "What is the price of Lemon Mint?"
    )

    assert len(fake_provider.chat_calls) == 0
    assert "45" in data["reply"]
    assert "metrics.menuItemsList" in data["data_sources_used"]


def test_arabic_common_issue_question(client, fake_provider):
    """Arabic common-issue question must route to mostCommonTicketTypes deterministically."""
    sync_report(client, make_sync_payload_with_metrics())

    data = owner_chat(
        client,
        "biz-restaurant-demo",
        "det-extra-4",
        "إيه أكتر شكوى بتجي؟",
    )

    assert len(fake_provider.chat_calls) == 0
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
    assert "metrics.mostCommonTicketTypes" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_arabic_count_orders_today(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-5", "كام طلب جه النهارده؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.orderMetrics" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_arabic_count_orders_this_week(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-6", "كام أوردر الأسبوع ده؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.orderMetrics" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_arabic_count_tickets_open(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-7", "كام تذكرة مفتوحة؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.ticketMetrics" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_arabic_count_tickets_escalated(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-8", "كام تذكرة متصاعدة؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.ticketMetrics" in data["data_sources_used"]
    assert data["confidence"] == "high"


def test_generic_menu_list_english(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-9", "What do you have?")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"
    assert "menu" in data["reply"].lower() or "available" in data["reply"].lower()


def test_generic_menu_list_arabic(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-10", "عندك إيه في المنيو؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])


def test_specific_item_availability_english(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-11", "Do you have Pepsi?")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"
    assert "pepsi" in data["reply"].lower()


def test_specific_item_availability_arabic(client, fake_provider):
    sync_report(client, make_sync_payload_with_metrics())
    data = owner_chat(client, "biz-restaurant-demo", "det-extra-12", "عندك Pepsi؟")
    assert len(fake_provider.chat_calls) == 0
    assert "metrics.menuItemsList" in data["data_sources_used"]
    assert data["confidence"] == "high"
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
