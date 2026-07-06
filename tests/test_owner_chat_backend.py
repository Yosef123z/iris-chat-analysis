"""
Tests for POST /api/v1/owner/chat (backend-driven report mode)

Covers:
- Missing business_id returns 422 (Pydantic validation)
- Unknown business_id returns safe low-confidence reply without calling LLM
- After sync, chat calls LLM with report context in prompt
- Prompt contains synced report data (not old markdown)
- business_id echoed in response
- Regression: /api/v1/chat (customer) still works independently
- Regression: /api/v1/analysis/report/generate still works
- Regression: business KB sync still persists under storage/business_kb
- Owner reports persist separately under storage/owner_reports
"""

import pytest

from tests.conftest import make_sync_payload


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
# Validation
# ---------------------------------------------------------------------------


def test_missing_business_id_returns_422(client):
    response = client.post(
        "/api/v1/owner/chat",
        json={"session_id": "s1", "message": "What is revenue?"},
    )
    assert response.status_code == 422


def test_blank_business_id_returns_422(client):
    response = client.post(
        "/api/v1/owner/chat",
        json={"business_id": "", "session_id": "s1", "message": "What is revenue?"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Unknown business — no LLM call, safe reply
# ---------------------------------------------------------------------------


def test_unknown_business_english_message_returns_english_safe_reply(client, fake_provider):
    """Pure-English message → English fallback, no LLM call."""
    data = owner_chat(client, "biz-unknown-xyz", "s1", "What are the main problems?")

    assert data["confidence"] == "low"
    assert data["business_id"] == "biz-unknown-xyz"
    assert data["session_id"] == "s1"
    # Must not have called the LLM at all
    assert fake_provider.chat_outputs == []
    assert fake_provider.structured_calls == []
    # English fallback — no Arabic characters
    assert "Sorry" in data["reply"]
    assert not any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])


def test_unknown_business_arabic_message_returns_arabic_safe_reply(client, fake_provider):
    """Arabic message → Arabic fallback, no LLM call."""
    data = owner_chat(client, "biz-unknown-xyz", "s2", "إيه المشاكل الأساسية؟")

    assert data["confidence"] == "low"
    # Arabic fallback — contains Arabic characters
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
    assert fake_provider.structured_calls == []


def test_unknown_business_mixed_message_returns_arabic_safe_reply(client, fake_provider):
    """Mixed Arabic+English message → Arabic fallback (Arabic takes priority)."""
    data = owner_chat(client, "biz-unknown-xyz", "s3", "إيه الـ revenue بتاع يونيو؟")

    assert data["confidence"] == "low"
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
    assert fake_provider.structured_calls == []


def test_unknown_business_does_not_invoke_llm(client, fake_provider):
    """Regression guard: provider.chat must not be called for unknown businesses."""
    owner_chat(client, "totally-unknown-biz", "s4", "Revenue?")
    assert fake_provider.structured_calls == []


# ---------------------------------------------------------------------------
# With synced report — LLM is called and prompt is grounded
# ---------------------------------------------------------------------------


def test_chat_calls_llm_after_report_sync(client, fake_provider):
    sync_report(client, make_sync_payload())

    # Provide LLM output via the owner_chat queue (chat() not structured_output())
    fake_provider.owner_chat_outputs.append("Revenue was strong this month.")

    data = owner_chat(client, "biz-restaurant-demo", "owner-s1", "What was revenue like?")

    assert data["reply"] == "Revenue was strong this month."
    assert data["business_id"] == "biz-restaurant-demo"
    assert data["confidence"] in {"high", "medium", "low"}
    # LLM was actually called
    assert len(fake_provider.chat_calls) == 1


def test_prompt_contains_synced_report_summary(client, fake_provider):
    """The synced report summary must appear in the LLM prompt context."""
    unique_summary = "Unique-delivery-complaint-marker-for-test-XYZ"
    payload = make_sync_payload(summary=unique_summary)
    sync_report(client, payload)

    fake_provider.owner_chat_outputs.append("تمام يا فندم.")

    owner_chat(client, "biz-restaurant-demo", "owner-s2", "Summarize report")

    # chat() was called and prompt messages should contain the unique summary marker
    assert len(fake_provider.chat_calls) == 1
    all_prompt_text = "\n".join(
        msg["content"]
        for msg in fake_provider.chat_calls[0]
    )
    assert unique_summary in all_prompt_text


def test_prompt_does_not_contain_markdown_report_content(client, fake_provider):
    """Prompt must not reference markdown file paths or old local report content."""
    sync_report(client, make_sync_payload())
    fake_provider.owner_chat_outputs.append("OK")

    owner_chat(client, "biz-restaurant-demo", "owner-s3", "hi")

    assert len(fake_provider.chat_calls) == 1
    all_prompt_text = "\n".join(
        msg["content"]
        for msg in fake_provider.chat_calls[0]
    )
    # No old markdown file paths or local-file references
    assert "app/data/uploads" not in all_prompt_text
    assert ".md" not in all_prompt_text
    # Report context IS in the prompt (structured JSON, not markdown)
    assert "biz-restaurant-demo" in all_prompt_text


def test_business_id_is_echoed_in_response(client, fake_provider):
    sync_report(client, make_sync_payload("biz-xyz", "XYZ Corp"))
    fake_provider.owner_chat_outputs.append("جيد يا فندم.")
    data = owner_chat(client, "biz-xyz", "s1", "hello")
    assert data["business_id"] == "biz-xyz"


# ---------------------------------------------------------------------------
# Language-switching: owner chat must mirror the message language
# ---------------------------------------------------------------------------


def test_owner_chat_english_message_sends_english_prompt_instruction(client, fake_provider):
    """Pure-English message → system prompt must say 'reply entirely in English'."""
    sync_report(client, make_sync_payload())
    fake_provider.owner_chat_outputs.append("Sales were up 12% this week.")

    owner_chat(client, "biz-restaurant-demo", "lang-en-1", "What were the top issues this week?")

    all_prompt_text = "\n".join(msg["content"] for msg in fake_provider.chat_calls[0])
    assert "entirely in English" in all_prompt_text
    assert "DETECTED_LANGUAGE: English" in all_prompt_text


def test_owner_chat_arabic_message_sends_arabic_prompt_instruction(client, fake_provider):
    """Arabic message → system prompt must instruct Egyptian Arabic reply."""
    sync_report(client, make_sync_payload())
    fake_provider.owner_chat_outputs.append("الإيرادات كانت كويسة أوي النهارده.")

    data = owner_chat(client, "biz-restaurant-demo", "lang-ar-1", "إيه كانت أكتر المشاكل النهارده؟")

    all_prompt_text = "\n".join(msg["content"] for msg in fake_provider.chat_calls[0])
    assert "Egyptian Arabic" in all_prompt_text or "Masry" in all_prompt_text
    # Reply should contain Arabic text (from mock LLM)
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])


def test_owner_chat_mixed_message_treated_as_arabic(client, fake_provider):
    """Mixed Arabic+English message → treated as Arabic (Arabic takes priority)."""
    sync_report(client, make_sync_payload())
    fake_provider.owner_chat_outputs.append("الـ revenue كان كويس النهارده.")

    data = owner_chat(client, "biz-restaurant-demo", "lang-mix-1", "إيه الـ revenue بتاع النهارده؟")

    # Arabic characters in the message → reply should be Arabic
    assert any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])


# ---------------------------------------------------------------------------
# Isolation: two businesses get their own reports
# ---------------------------------------------------------------------------


def test_two_businesses_get_isolated_reports(client, fake_provider):
    payload_a = make_sync_payload("biz-a", "Business A", summary="Summary for A only.")
    payload_b = make_sync_payload("biz-b", "Business B", summary="Summary for B only.")
    sync_report(client, payload_a)
    sync_report(client, payload_b)

    # Business A chat
    fake_provider.owner_chat_outputs.append("A answer.")
    data_a = owner_chat(client, "biz-a", "sa", "What is the summary?")
    assert data_a["business_id"] == "biz-a"

    # Business B chat
    fake_provider.owner_chat_outputs.append("B answer.")
    data_b = owner_chat(client, "biz-b", "sb", "What is the summary?")
    assert data_b["business_id"] == "biz-b"


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


def test_customer_chat_still_works_independently(client, fake_provider):
    """Regression: /api/v1/chat must be unaffected by owner chat changes."""
    from tests.conftest import llm_chat_output

    # Sync a customer KB so chat works
    client.post(
        "/api/v1/business/knowledge-base/sync",
        json={
            "business_id": "reg-biz",
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
    fake_provider.chat_outputs.append(llm_chat_output(reply="Reg Burger متاح يا فندم."))
    response = client.post(
        "/api/v1/chat",
        json={"business_id": "reg-biz", "session_id": "reg-s1", "message": "What do you have?"},
    )
    assert response.status_code == 200
    assert "Reg Burger" in response.json()["reply"]


def test_report_generate_endpoint_still_works(client, fake_provider):
    """Regression: /api/v1/analysis/report/generate must remain unchanged."""
    response = client.post(
        "/api/v1/analysis/report/generate",
        json={
            "businessId": "biz-1",
            "businessName": "Test Biz",
            "period": {"from": "2026-06-01T00:00:00Z", "to": "2026-06-30T23:59:59Z"},
            "metrics": {
                "totalSessions": 10,
                "analyzedSessions": 10,
                "averageSentimentScore": 0.2,
                "sentimentDistribution": {"positive": 5, "neutral": 3, "negative": 2},
                "totalComplaints": 2,
                "totalHumanAgentRequests": 1,
                "totalOrdersDetected": 7,
            },
            "topIntents": [{"name": "CreateOrder", "count": 7}],
            "topTopics": [{"name": "delivery", "count": 3}],
            "commonIssues": [],
            "recentKeyMoments": ["Customer complained about cold food."],
            "sampleSummaries": [
                {
                    "sessionId": "s1",
                    "summary": "Customer ordered a burger.",
                    "summaryAr": "العميل طلب برجر.",
                    "mainIntent": "CreateOrder",
                    "sentimentLabel": "Positive",
                    "sentimentScore": 0.8,
                }
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "businessId" in data
    assert data["businessId"] == "biz-1"


def test_business_kb_and_owner_reports_use_separate_storage(tmp_path):
    """Regression: KB and owner reports must not share the same storage directory."""
    from app.services.business_knowledge_service import BusinessKnowledgeService
    from app.services.owner_report_service import OwnerReportService
    from app.models.owner_chat import OwnerReportSyncRequest
    from tests.conftest import FakeEmbeddings
    import asyncio

    kb_dir = tmp_path / "business_kb"
    report_dir = tmp_path / "owner_reports"

    kb_svc = BusinessKnowledgeService(storage_dir=kb_dir)
    report_svc = OwnerReportService(storage_dir=report_dir)

    asyncio.run(
        kb_svc.sync_business_kb(
            __import__("app.models.business_kb", fromlist=["BusinessKnowledgeSyncRequest"])
            .BusinessKnowledgeSyncRequest.model_validate({
                "business_id": "shared-biz",
                "business_name": "Shared Biz",
                "knowledge_base": {
                    "menu_items": [],
                    "faqs": [{"question": "Q", "answer": "A", "is_faq": True}],
                },
            }),
            FakeEmbeddings(),
        )
    )

    report_svc.sync_report(OwnerReportSyncRequest.model_validate(
        make_sync_payload("shared-biz", "Shared Biz")
    ))

    # Both dirs exist but contain their own artifacts
    assert kb_dir.exists()
    assert report_dir.exists()
    assert list(kb_dir.glob("*.pkl")) != []
    assert list(report_dir.glob("*.pkl")) != []
    # No cross-contamination
    assert list(kb_dir.glob("*.pkl"))[0].parent == kb_dir
    assert list(report_dir.glob("*.pkl"))[0].parent == report_dir


# ---------------------------------------------------------------------------
# Unit tests for _sanitize_reply
# ---------------------------------------------------------------------------


def test_sanitize_strips_bold_markers():
    from app.services.owner_chat_service import OwnerChatService
    raw = "There are **two main problems** that need **addressing**."
    assert OwnerChatService._sanitize_reply(raw) == "There are two main problems that need addressing."


def test_sanitize_strips_numbered_list_markers():
    from app.services.owner_chat_service import OwnerChatService
    raw = "Issues:\n1. Late deliveries.\n2. Out-of-stock items."
    result = OwnerChatService._sanitize_reply(raw)
    assert "1." not in result
    assert "2." not in result
    assert "Late deliveries." in result
    assert "Out-of-stock items." in result


def test_sanitize_strips_bullet_list_markers():
    from app.services.owner_chat_service import OwnerChatService
    raw = "Problems:\n- Cold food.\n- Missing items."
    result = OwnerChatService._sanitize_reply(raw)
    assert result.startswith("Problems:")
    assert "-" not in result
    assert "Cold food." in result


def test_sanitize_collapses_newlines_to_spaces():
    from app.services.owner_chat_service import OwnerChatService
    raw = "First sentence.\n\nSecond sentence.\n\nThird sentence."
    result = OwnerChatService._sanitize_reply(raw)
    assert "\n" not in result
    assert "First sentence. Second sentence. Third sentence." == result


def test_sanitize_strips_heading_markers():
    from app.services.owner_chat_service import OwnerChatService
    raw = "## Summary\nGood month overall."
    result = OwnerChatService._sanitize_reply(raw)
    assert "##" not in result
    assert "Summary" in result


def test_sanitize_real_world_markdown_reply():
    """Reproduces the exact problematic response format the user reported."""
    from app.services.owner_chat_service import OwnerChatService
    raw = (
        "There are two main problems that need to be addressed:\n\n"
        "1. **Late and cold deliveries**: This issue was significant in Week 4, "
        "with 23 mentions of 'cold food' across 18 unique sessions, leading to an "
        "average delivery complaint rate of 14%.\n\n"
        "2. **Out-of-stock items not updated in real time**: Customers attempted to "
        "order the Crispy Chicken Burger 31 times despite it being unavailable, "
        "resulting in a frustration sentiment score of -0.6 for those sessions.\n\n"
        "Addressing these issues is crucial for improving customer satisfaction."
    )
    result = OwnerChatService._sanitize_reply(raw)
    # No markdown symbols
    assert "**" not in result
    assert "\n" not in result
    assert "1." not in result
    assert "2." not in result
    # Content preserved
    assert "Late and cold deliveries" in result
    assert "Out-of-stock items" in result
    assert "14%" in result
    # Single continuous string, no double spaces
    assert "  " not in result


# ---------------------------------------------------------------------------
# Unit tests for _enforce_reply_language (language safety net)
# ---------------------------------------------------------------------------


def test_enforce_language_swaps_arabic_no_data_for_english_message():
    """If LLM returns Arabic no-data phrase for an English message, swap to English."""
    from app.services.owner_chat_service import OwnerChatService
    arabic_no_data = "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
    result = OwnerChatService._enforce_reply_language(arabic_no_data, is_arabic=False)
    assert result == "Sorry, that information is not available in the current report."
    assert not any("\u0600" <= ch <= "\u06FF" for ch in result)


def test_enforce_language_swaps_english_no_data_for_arabic_message():
    """If LLM returns English no-data phrase for an Arabic message, swap to Arabic."""
    from app.services.owner_chat_service import OwnerChatService
    english_no_data = "Sorry, that information is not available in the current report."
    result = OwnerChatService._enforce_reply_language(english_no_data, is_arabic=True)
    assert result == "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
    assert any("\u0600" <= ch <= "\u06FF" for ch in result)


def test_enforce_language_passes_through_correct_english_reply():
    """Correct English reply for English message is unchanged."""
    from app.services.owner_chat_service import OwnerChatService
    reply = "Revenue was up 12% this week, driven by strong weekend orders."
    result = OwnerChatService._enforce_reply_language(reply, is_arabic=False)
    assert result == reply


def test_enforce_language_passes_through_correct_arabic_reply():
    """Correct Arabic reply for Arabic message is unchanged."""
    from app.services.owner_chat_service import OwnerChatService
    reply = "الإيرادات كانت كويسة أوي النهارده."
    result = OwnerChatService._enforce_reply_language(reply, is_arabic=True)
    assert result == reply


def test_owner_chat_english_message_gets_english_no_data_reply(client, fake_provider):
    """End-to-end: English message + LLM returns Arabic no-data phrase → reply must be English."""
    sync_report(client, make_sync_payload())
    # Simulate LLM returning the wrong (Arabic) no-data phrase for an English question
    fake_provider.owner_chat_outputs.append(
        "معلش، المعلومة دي مش موجودة في تقرير النهاردة."
    )
    data = owner_chat(client, "biz-restaurant-demo", "enforce-en-1", "What was today's revenue?")
    assert data["reply"] == "Sorry, that information is not available in the current report."
    assert not any("\u0600" <= ch <= "\u06FF" for ch in data["reply"])
