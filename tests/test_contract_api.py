import json
from pathlib import Path

from app.config import settings
from app.core.llm_interface import AIProviderError
from app.models.business_kb import BusinessKnowledgeSyncRequest
from app.models.chat import OrderLineItem
from app.services.chat_service import ChatService, contains_arabic, detect_customer_language
from tests.conftest import llm_chat_output, prompt_text


def restaurant_kb(business_id="biz-1"):
    return {
        "business_id": business_id,
        "business_name": "Contract Demo",
        "knowledge_base": {
            "menu_items": [
                {
                    "menu_item_id": "burger-1",
                    "name": "Classic Burger",
                    "description": "Beef burger with cheese",
                    "price": 120,
                    "category": "Burgers",
                    "is_available": True,
                },
                {
                    "menu_item_id": "burger-2",
                    "name": "Crispy Chicken Burger",
                    "description": "Chicken burger",
                    "price": 110,
                    "category": "Burgers",
                    "is_available": False,
                },
                {
                    "menu_item_id": "drink-1",
                    "name": "Lemon Mint",
                    "description": "Fresh drink",
                    "price": 45,
                    "category": "Drinks",
                    "is_available": True,
                },
            ],
            "faqs": [
                {
                    "question": "Delivery time",
                    "answer": "Delivery takes 30 to 45 minutes.",
                    "is_faq": True,
                },
                {
                    "question": "Return policy",
                    "answer": "Please contact support for returns.",
                    "is_faq": False,
                },
            ],
        },
    }


def clinic_kb():
    return {
        "business_id": "clinic-1",
        "business_name": "Demo Clinic",
        "knowledge_base": {
            "menu_items": [
                {
                    "menu_item_id": "svc-1",
                    "name": "Dental Cleaning",
                    "description": "Professional teeth cleaning appointment",
                    "price": 500,
                    "category": "Dental",
                    "is_available": True,
                }
            ],
            "faqs": [
                {
                    "question": "Opening hours",
                    "answer": "The clinic is open from 10 AM to 8 PM.",
                    "is_faq": True,
                }
            ],
        },
    }


def sync(client, payload):
    response = client.post("/api/v1/business/knowledge-base/sync", json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


def chat(client, business_id, session_id, message):
    response = client.post(
        "/api/v1/chat",
        json={
            "business_id": business_id,
            "session_id": session_id,
            "message": message,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_customer_language_detection_helpers():
    assert contains_arabic("عايز برجر") is True
    assert contains_arabic("hello عايز برجر") is True
    assert contains_arabic("price كام") is True
    assert contains_arabic("hello") is False

    assert detect_customer_language("hello") == "en"
    assert detect_customer_language("What are your prices?") == "en"
    assert detect_customer_language("عايز برجر") == "ar"
    assert detect_customer_language("hello عايز برجر") == "ar"
    assert detect_customer_language("price كام") == "ar"


def test_openapi_final_paths_and_static_tools_decision(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in [
        "/api/v1/business/knowledge-base/sync",
        "/api/v1/chat",
        "/api/v1/analysis/chat-batch",
        "/api/v1/analysis/pii-remove",
    ]:
        assert path in paths

    removed = {
        "/api/v1/chat/file",
        "/api/v1/owner/chat/file",
        "/api/v1/chat/voice",
        "/api/v1/chat/voice/stream",
        "/api/v1/orders",
        "/api/v1/analysis/detect-intent",
        "/api/v1/analysis/sentiment",
        "/api/v1/analysis/batch",
        "/api/v1/datasource/upload",
        "/api/v1/feedback",
        "/api/v1/tickets",
        "/api/v1/hitl/escalate",
        "/api/v1/owner/cache/stats",
        "/api/v1/owner/synonyms",
    }
    assert removed.isdisjoint(paths)
    assert client.get("/tools/customer_chat.html").status_code in {200, 404}
    assert client.get("/tools/owner_chat.html").status_code in {200, 404}


def test_missing_kb_returns_safe_false_flags_without_llm(client, fake_provider):
    data = chat(client, "unknown", "s1", "hello")
    assert data["reply"].startswith("Sorry, this business data is not available")
    assert contains_arabic(data["reply"]) is False
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["ticket_detected"] is False
    assert data["escalation_requested"] is False
    assert data["feedback_requested"] is False
    assert data["order_details"] is None
    assert data["ticket_details"] is None
    assert fake_provider.structured_calls == []
    assert fake_provider.embeddings.query_calls == []


def test_missing_kb_arabic_message_keeps_arabic_reply_without_llm(client, fake_provider):
    data = chat(client, "unknown", "s1", "عايز برجر")
    assert data["reply"].startswith("معلش يا فندم")
    assert contains_arabic(data["reply"]) is True
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["ticket_detected"] is False
    assert data["escalation_requested"] is False
    assert data["feedback_requested"] is False
    assert data["order_details"] is None
    assert data["ticket_details"] is None
    assert fake_provider.structured_calls == []
    assert fake_provider.embeddings.query_calls == []


def test_kb_sync_builds_and_replaces_per_business_index(client, fake_provider):
    sync(client, restaurant_kb())
    assert len(fake_provider.embeddings.document_calls) == 1
    assert any("Classic Burger" in text for text in fake_provider.embeddings.document_calls[0])

    fake_provider.chat_outputs.append(
        llm_chat_output(reply="عندنا Margherita Pizza يا فندم.")
    )
    payload = restaurant_kb()
    payload["knowledge_base"]["menu_items"] = [
        {
            "menu_item_id": "p1",
            "name": "Margherita Pizza",
            "description": "Cheese pizza",
            "price": 95,
            "category": "Pizza",
            "is_available": True,
        }
    ]
    sync(client, payload)
    data = chat(client, "biz-1", "s2", "What products do you have?")
    assert "Margherita Pizza" in data["reply"]
    text = prompt_text(fake_provider)
    assert "Margherita Pizza" in text
    assert "Classic Burger" not in text


def test_two_businesses_have_isolated_indexes(client, fake_provider):
    sync(client, restaurant_kb("biz-1"))
    payload = restaurant_kb("biz-2")
    payload["business_name"] = "Pizza Shop"
    payload["knowledge_base"]["menu_items"][0]["name"] = "Margherita Pizza"
    payload["knowledge_base"]["menu_items"][0]["menu_item_id"] = "pizza-1"
    sync(client, payload)

    fake_provider.chat_outputs.append(llm_chat_output(reply="عندنا Margherita Pizza."))
    data = chat(client, "biz-2", "s1", "What products do you have?")
    assert "Margherita Pizza" in data["reply"]
    text = prompt_text(fake_provider)
    assert "biz-2" in text
    assert "Margherita Pizza" in text
    assert "Classic Burger" not in text


def test_kb_sync_embeddings_failure_returns_503_without_partial_replace(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.embeddings.fail_documents = True
    payload = restaurant_kb()
    payload["knowledge_base"]["menu_items"][0]["name"] = "Broken Replacement"
    response = client.post("/api/v1/business/knowledge-base/sync", json=payload)
    assert response.status_code == 503

    fake_provider.embeddings.fail_documents = False
    fake_provider.chat_outputs.append(llm_chat_output(reply="Classic Burger موجود."))
    data = chat(client, "biz-1", "s1", "What products do you have?")
    assert "Classic Burger" in prompt_text(fake_provider)
    assert "Broken Replacement" not in prompt_text(fake_provider)
    assert data["reply"] == "Classic Burger موجود."


def test_chat_calls_llm_and_prompt_is_grounded(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(reply="Classic Burger سعره 120 يا فندم.")
    )
    data = chat(client, "biz-1", "s1", "Tell me about Classic Burger")
    assert data["reply"] == "Classic Burger سعره 120 يا فندم."
    assert len(fake_provider.structured_calls) == 1
    assert fake_provider.embeddings.query_calls == ["Tell me about Classic Burger"]
    text = prompt_text(fake_provider)
    assert "biz-1" in text
    assert "Contract Demo" in text
    assert "Classic Burger" in text
    assert "is_available" in text
    assert "customer-service English" in text
    assert '"detected_customer_language": "en"' in text
    assert "Do not invent" in text
    assert "Never mention backend" in text
    assert "real professional waiter or cafe cashier" in text
    assert "Egyptian customer service employee" in text


def test_arabic_and_mixed_messages_add_egyptian_arabic_prompt_instruction(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(llm_chat_output(reply="أكيد يا فندم."))
    chat(client, "biz-1", "language-ar-1", "price كام")
    text = prompt_text(fake_provider)
    assert "Egyptian Arabic" in text
    assert '"detected_customer_language": "ar"' in text

    fake_provider.chat_outputs.append(llm_chat_output(reply="أكيد يا فندم."))
    chat(client, "biz-1", "language-ar-2", "hello عايز برجر")
    text = prompt_text(fake_provider)
    assert '"detected_customer_language": "ar"' in text


def test_arabic_reply_with_english_item_name_starts_rtl_naturally(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(llm_chat_output(reply="Classic Burger متاح يا فندم."))

    data = chat(client, "biz-1", "direction-ar-1", "Classic Burger متاح؟")

    assert data["reply"].startswith("\u200Fتمام يا فندم، Classic Burger")
    assert contains_arabic(data["reply"]) is True


def test_canonical_names_prices_and_cart_across_turns(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام يا فندم، ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="classic burger sandwich", quantity=2, price=1)],
        )
    )
    first = chat(client, "biz-1", "cart-1", "عايز Classic Burger")
    assert first["order_details"]["items"][0]["name"] == "Classic Burger"
    assert first["order_details"]["items"][0]["price"] == 120
    assert first["order_details"]["total_amount"] == 240

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام يا فندم، تم تأكيد الطلب.",
            order_detected=True,
            order_finalized=True,
            items=[OrderLineItem(name="Classic Burger", quantity=2, price=120)],
        )
    )
    second = chat(client, "biz-1", "cart-1", "تمام أكد الطلب")
    assert second["order_detected"] is True
    assert second["order_finalized"] is True
    assert second["order_details"]["items"][0]["name"] == "Classic Burger"
    assert "system_events" not in second


def test_confirm_order_phrase_forces_finalization(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger. هل ترغب في تأكيد الطلب؟",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    first = chat(client, "biz-1", "manual-order-1", "عايز Classic Burger")
    assert first["order_detected"] is True
    assert first["order_finalized"] is False
    assert "تأكيد الطلب" not in first["reply"]
    assert "تأكد الطلب" not in first["reply"]
    assert "هل ترغب" not in first["reply"]
    # The natural acknowledgment part should be preserved.
    assert first["reply"]  # reply is non-empty

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام، هل ترغب في تأكيد الطلب؟",
            order_detected=True,
            order_finalized=False,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    data = chat(client, "biz-1", "manual-order-1", "تمام أكد الطلب")
    assert data["order_detected"] is True
    assert data["order_finalized"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"
    assert "هل ترغب" not in data["reply"]


def test_open_order_does_not_ask_delivery_or_takeaway_before_confirmation(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply=(
                "تمام، سجلتلك Classic Burger و Lemon Mint. "
                "تحبي تاكليه في المطعم ولا تحبي توصليه للبيت؟"
            ),
            order_detected=True,
            items=[
                OrderLineItem(name="Classic Burger", quantity=1, price=120),
                OrderLineItem(name="Lemon Mint", quantity=1, price=45),
            ],
        )
    )

    data = chat(client, "biz-1", "open-order-logical-1", "عايز Classic Burger و Lemon Mint")

    assert data["order_detected"] is True
    assert data["order_finalized"] is False
    assert "توصيل" not in data["reply"]
    assert "توصليه" not in data["reply"]
    assert "للبيت" not in data["reply"]
    assert "في المطعم" not in data["reply"]
    assert "takeaway" not in data["reply"]
    assert "تحب تضيف حاجة تانية" in data["reply"]


def test_natural_arabic_no_more_items_finalizes_active_cart(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام يا فندم، ضفت Classic Burger. تحب تضيف معاه Lemon Mint؟",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    first = chat(client, "biz-1", "natural-close-ar-1", "عايز Classic Burger")
    assert first["order_finalized"] is False
    assert "Lemon Mint" in first["reply"]

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام، هل ترغب في تأكيد الطلب؟",
            order_detected=True,
            order_finalized=False,
        )
    )
    data = chat(client, "biz-1", "natural-close-ar-1", "لا شكرا مش عايز حاجة تانية")
    assert data["order_detected"] is True
    assert data["order_finalized"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"
    assert "هل ترغب" not in data["reply"]
    assert contains_arabic(data["reply"]) is True


def test_english_finalization_forced_reply_stays_english(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Added Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "english-order-1", "I want Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام يا فندم، تم تأكيد الطلب.",
            order_detected=True,
            order_finalized=False,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    data = chat(client, "biz-1", "english-order-1", "yes confirm")
    assert data["order_detected"] is True
    assert data["order_finalized"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"
    assert data["reply"] == (
        "Your order is confirmed, and we will start preparing it for you. "
        "Will you be dining in or taking it takeaway?"
    )
    assert contains_arabic(data["reply"]) is False


def test_confirmed_order_asks_fulfillment_then_preference_reply_is_not_duplicate_order(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "fulfillment-1", "عايز Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمام، الطلب اتأكد.",
            order_detected=True,
            order_finalized=False,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    confirmed = chat(client, "biz-1", "fulfillment-1", "تمام أكد الطلب")
    calls_after_confirmation = len(fake_provider.structured_calls)

    assert confirmed["order_finalized"] is True
    assert "في المطعم" in confirmed["reply"]
    assert "takeaway" in confirmed["reply"]

    preference = chat(client, "biz-1", "fulfillment-1", "takeaway")

    assert preference["reply"] == "تمام يا فندم، طلبك بيتجهز دلوقتي."
    assert preference["order_detected"] is False
    assert preference["order_finalized"] is False
    assert preference["order_details"] is None
    assert len(fake_provider.structured_calls) == calls_after_confirmation


def test_english_no_more_items_finalizes_active_cart(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Added Classic Burger. Would you like anything else with it?",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "natural-close-en-1", "I want Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Would you like to confirm your order?",
            order_detected=True,
            order_finalized=False,
        )
    )
    data = chat(client, "biz-1", "natural-close-en-1", "no thanks")
    assert data["order_detected"] is True
    assert data["order_finalized"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"
    assert contains_arabic(data["reply"]) is False


def test_cancel_order_clears_cart_and_returns_false_order_flags(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "cancel-cart-1", "عايز Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Lemon Mint.",
            order_detected=True,
            items=[OrderLineItem(name="Lemon Mint", quantity=1, price=45)],
        )
    )
    chat(client, "biz-1", "cancel-cart-1", "ضيف Lemon Mint")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="معلش، مش هقدر ألغي الأوردر دلوقتي.",
            order_detected=True,
            items=[
                OrderLineItem(name="Classic Burger", quantity=1, price=120),
                OrderLineItem(name="Lemon Mint", quantity=1, price=45),
            ],
        )
    )
    data = chat(
        client,
        "biz-1",
        "cancel-cart-1",
        "بقولك ايه خلاص عايز الغى الاوردر معلش",
    )
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["order_details"] is None
    assert "لغيت" in data["reply"] or "اتلغى" in data["reply"]
    assert "مش هقدر" not in data["reply"]


def test_cancel_order_clears_cart_before_new_order_same_session(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "cancel-cart-2", "عايز Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="معلش، مش هقدر ألغي الأوردر دلوقتي.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "cancel-cart-2", "خلاص ألغي الطلب")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Lemon Mint.",
            order_detected=True,
            items=[OrderLineItem(name="Lemon Mint", quantity=1, price=45)],
        )
    )
    data = chat(client, "biz-1", "cancel-cart-2", "عايز Lemon Mint")
    names = [item["name"] for item in data["order_details"]["items"]]
    assert names == ["Lemon Mint"]
    assert "Classic Burger" not in names


def test_add_item_and_finalization_same_turn(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "manual-order-2", "عايز Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Lemon Mint. هل ترغب في تأكيد الطلب؟",
            order_detected=True,
            order_finalized=False,
            items=[OrderLineItem(name="Lemon Mint", quantity=1, price=45)],
        )
    )
    data = chat(client, "biz-1", "manual-order-2", "تمام ضيف الليمون و بس كدة")
    names = [item["name"] for item in data["order_details"]["items"]]
    assert names == ["Classic Burger", "Lemon Mint"]
    assert data["order_finalized"] is True
    assert "اتأكد" in data["reply"]


def test_finalization_after_cart_summary_phrase(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="ضفت Classic Burger.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    chat(client, "biz-1", "manual-order-3", "عايز Classic Burger")

    fake_provider.chat_outputs.append(
        llm_chat_output(reply="إجمالي الطلب 120. هل ترغب في تأكيد الطلب؟", order_detected=True)
    )
    data = chat(client, "biz-1", "manual-order-3", "ايوه خلاص هو دا الطلب")
    assert data["order_finalized"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"


def test_informational_clinic_query_does_not_start_order(client, fake_provider):
    sync(client, clinic_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Dental Cleaning هو خدمة تنظيف الأسنان. هل ترغب في تأكيد الطلب؟",
            order_detected=True,
            items=[OrderLineItem(name="Dental Cleaning", quantity=1, price=500)],
        )
    )
    data = chat(client, "clinic-1", "clinic-info-1", "قولي تفاصيل Dental Cleaning")
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["order_details"] is None
    assert "هل ترغب" not in data["reply"]


def test_informational_query_reply_does_not_offer_add_to_order(client, fake_provider):
    sync(client, clinic_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Dental Cleaning هي خدمة تنظيف الأسنان. سعرها 500 جنيه. تحب أضيفها لطلبك؟",
            order_detected=True,
            items=[OrderLineItem(name="Dental Cleaning", quantity=1, price=500)],
        )
    )
    data = chat(client, "clinic-1", "clinic-info-2", "قولي تفاصيل Dental Cleaning")
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["order_details"] is None
    assert "أضيفها لطلبك" not in data["reply"]
    assert "تحب أضيف" not in data["reply"]
    assert "تأكيد الطلب" not in data["reply"]
    assert "Dental Cleaning" in data["reply"]
    assert "500" in data["reply"]


def test_escalation_only_does_not_create_ticket(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="لو حابب، ممكن أساعدك في تقديم شكوى.",
            ticket_detected=True,
            ticket_details={
                "subject": "طلب التحدث مع المدير",
                "description": None,
                "priority": "normal",
                "category": "other",
            },
            escalation_requested=True,
        )
    )
    data = chat(client, "biz-1", "manual-escalation-1", "عايز أكلم المدير")
    assert data["ticket_detected"] is False
    assert data["ticket_details"] is None
    assert data["escalation_requested"] is True
    assert "الإدارة" in data["reply"]
    assert "تقديم شكوى" not in data["reply"]


def test_english_escalation_only_reply_stays_english(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="Would you like to file a complaint?",
            ticket_detected=True,
            ticket_details={
                "subject": "Manager request",
                "description": None,
                "priority": "normal",
                "category": "other",
            },
            escalation_requested=True,
        )
    )
    data = chat(client, "biz-1", "english-escalation-1", "Can I speak to a manager?")
    assert data["ticket_detected"] is False
    assert data["ticket_details"] is None
    assert data["escalation_requested"] is True
    assert "management" in data["reply"]
    assert contains_arabic(data["reply"]) is False


def test_complaint_plus_escalation_action_reply(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="هل ترغب في تقديم شكوى؟",
            ticket_detected=True,
            ticket_details={
                "subject": "Complaint",
                "description": None,
                "priority": "normal",
                "category": "other",
            },
            escalation_requested=True,
        )
    )
    data = chat(
        client,
        "biz-1",
        "manual-ticket-escalation-1",
        "الأوردر وصل غلط وعايز أكلم المدير حالًا",
    )
    assert data["ticket_detected"] is True
    assert data["escalation_requested"] is True
    assert data["ticket_details"]["priority"] in {"high", "critical"}
    assert data["ticket_details"]["category"] in {"wrong_order", "complaint"}
    assert "هسجل المشكلة" in data["reply"]
    assert "هل ترغب" not in data["reply"]


def test_unavailable_item_returns_empty_order_details(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="عذرًا، Crispy Chicken Burger غير متوفر حاليًا.",
            order_detected=True,
            items=None,
        )
    )
    data = chat(client, "biz-1", "manual-unavailable-1", "عايز Crispy Chicken Burger")
    assert data["order_detected"] is True
    assert data["order_finalized"] is False
    assert data["order_details"] is not None
    assert data["order_details"]["intent"] == "CreateOrder"
    assert data["order_details"]["items"] == []
    assert data["order_details"]["total_amount"] == 0
    assert "معلش" in data["reply"]
    assert "مش متاح دلوقتي" in data["reply"]


def test_english_unavailable_item_reply_stays_english(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="عذرًا، Crispy Chicken Burger غير متوفر حاليًا.",
            order_detected=True,
            items=None,
        )
    )
    data = chat(client, "biz-1", "english-unavailable-1", "I want Crispy Chicken Burger")
    assert data["order_detected"] is True
    assert data["order_finalized"] is False
    assert data["order_details"]["intent"] == "CreateOrder"
    assert data["order_details"]["items"] == []
    assert data["order_details"]["total_amount"] == 0
    assert data["reply"].startswith("Sorry, Crispy Chicken Burger is not available right now.")
    assert contains_arabic(data["reply"]) is False


def test_customer_replies_are_egyptian_arabic_sanitized(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="عذرًا، تم إضافة Classic Burger. هل ترغب في إضافة أي شيء آخر؟ غير متوفر حاليًا.",
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )
    data = chat(client, "biz-1", "dialect-1", "عايز Classic Burger")
    forbidden = ["هل ترغب", "عذرًا", "غير متوفر حاليًا", "تم إضافة"]
    assert not any(term in data["reply"] for term in forbidden)


def test_reply_text_cleanup_removes_formatting_noise_and_preserves_content():
    noisy = 'Assistant: ```json\n{"reply":"Classic Burger costs 120 EGP!!! 😊"}\n```'

    assert ChatService._sanitize_reply_text(noisy) == "Classic Burger costs 120 EGP!"


def test_reply_text_cleanup_repairs_mojibake_and_strips_invisible_characters():
    noisy = "Reply: \u00d9\u0085\u00d8\u00b9\u00d9\u0084\u00d8\u00b4\u200f\u0007!!! Classic Burger   120"

    cleaned = ChatService._sanitize_reply_text(noisy)

    assert cleaned == "معلش! Classic Burger 120"
    assert "\u200f" not in cleaned
    assert "\u0007" not in cleaned


def test_chat_reply_cleanup_keeps_contract_signals_and_item_payload(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply='Assistant: ```json\n{"reply":"تم إضافة Classic Burger!!! 😊"}\n```',
            order_detected=True,
            items=[OrderLineItem(name="Classic Burger", quantity=1, price=120)],
        )
    )

    data = chat(client, "biz-1", "reply-cleanup-1", "عايز Classic Burger")

    assert data["reply"] == "\u200Fضفت Classic Burger!"
    assert data["order_detected"] is True
    assert data["order_details"]["items"][0]["name"] == "Classic Burger"
    assert data["order_details"]["items"][0]["price"] == 120


def test_unavailable_and_invented_items_are_sanitized(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="تمت إضافة الأصناف.",
            order_detected=True,
            order_finalized=True,
            items=[
                OrderLineItem(name="Crispy Chicken Burger", quantity=1, price=110),
                OrderLineItem(name="Imaginary Fries", quantity=1, price=10),
            ],
        )
    )
    data = chat(client, "biz-1", "s-unavailable", "عايز Crispy Chicken Burger")
    assert data["order_detected"] is True
    assert data["order_finalized"] is False
    assert data["order_details"]["items"] == []
    assert "Crispy Chicken Burger" in data["reply"]
    assert "Imaginary Fries" not in json.dumps(data, ensure_ascii=False)


def test_ticket_category_normalization_and_reply_sanitization(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(
        llm_chat_output(
            reply="The backend API JSON will create a ticket.",
            ticket_detected=True,
            ticket_details={
                "subject": "Complaint",
                "description": "wrong order",
                "priority": "urgent",
                "category": "random",
            },
            escalation_requested=True,
        )
    )
    data = chat(client, "biz-1", "support-1", "wrong order and need manager")
    assert data["ticket_detected"] is True
    assert data["ticket_details"]["priority"] == "high"
    assert data["ticket_details"]["category"] == "wrong_order"
    assert data["escalation_requested"] is True
    forbidden = ["backend", "api", "json", "contract", "rag", "vector", "system prompt"]
    assert not any(term in data["reply"].lower() for term in forbidden)


def test_llm_and_query_embedding_failures_return_503(client, fake_provider):
    sync(client, restaurant_kb())
    fake_provider.chat_outputs.append(AIProviderError("bad json after retry"))
    response = client.post(
        "/api/v1/chat",
        json={"business_id": "biz-1", "session_id": "s1", "message": "hi"},
    )
    assert response.status_code == 503

    fake_provider.embeddings.fail_query = True
    response = client.post(
        "/api/v1/chat",
        json={"business_id": "biz-1", "session_id": "s2", "message": "hi"},
    )
    assert response.status_code == 503


def test_chat_prompt_is_restaurant_and_cafe_oriented(client, fake_provider):
    sync(client, clinic_kb())
    fake_provider.chat_outputs.append(llm_chat_output(reply="Dental Cleaning متاحة يا فندم."))
    data = chat(client, "clinic-1", "clinic-session", "Tell me about Dental Cleaning")
    assert "Dental Cleaning" in data["reply"]
    text = prompt_text(fake_provider)
    assert "Dental Cleaning" in text
    assert "restaurant or cafe" in text
    assert "restaurant/cafe menu items" in text


def test_analysis_chat_batch_calls_llm_after_pii_redaction(client, fake_provider):
    fake_provider.analysis_outputs.append(
        {
            "sessionId": "analysis-1",
            "summary": "Customer complained. test@example.com",
            "summaryAr": "العميل اشتكى.",
            "overallSentiment": {"score": -0.5, "label": "Negative"},
            "mainIntent": "Complaint",
            "intentsDetected": [{"name": "Complaint", "count": 1}],
            "mainTopics": ["delivery", "test@example.com"],
            "keyMoments": ["Customer sent test@example.com then complained"],
        }
    )
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [
                {
                    "sessionId": "analysis-1",
                    "messages": [
                        {"role": "customer", "text": "My email is test@example.com"},
                        {"role": "customer", "text": "الأوردر وصل بارد"},
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    result = data["results"][0]
    assert data["businessId"] == "biz-1"
    assert result["mainIntent"] == result["intentsDetected"][0]["name"]
    assert "mainTopics" in result
    assert "MainTopics" not in result
    model_prompt = prompt_text(fake_provider)
    assert "test@example.com" not in model_prompt
    assert "[EMAIL]" in model_prompt
    combined = " ".join([result["summary"], result["summaryAr"], *result["mainTopics"], *result["keyMoments"]])
    assert "test@example.com" not in combined


def test_analysis_adds_topics_and_key_moments_for_complaint_handoff(client, fake_provider):
    fake_provider.analysis_outputs.append(
        {
            "sessionId": "manual-analysis-1",
            "summary": "Customer complained and asked for manager.",
            "summaryAr": "العميل اشتكى وطلب المدير.",
            "overallSentiment": {"score": -0.5, "label": "Negative"},
            "mainIntent": "Complaint",
            "intentsDetected": [{"name": "Complaint", "count": 1}],
            "mainTopics": [],
            "keyMoments": [],
        }
    )
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [
                {
                    "sessionId": "manual-analysis-1",
                    "messages": [
                        {"role": "customer", "text": "اسمي يوسف ورقمي 01012345678 وعايز Classic Burger"},
                        {"role": "assistant", "text": "تمام يا فندم، ضفت Classic Burger."},
                        {"role": "customer", "text": "الأوردر وصل بارد وعايز أكلم المدير"},
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["mainTopics"]
    assert result["keyMoments"]
    assert any("Classic Burger" in topic for topic in result["mainTopics"])
    assert any("وصل بارد" in moment for moment in result["keyMoments"])


def test_analysis_summary_ar_is_egyptianized(client, fake_provider):
    fake_provider.analysis_outputs.append(
        {
            "sessionId": "manual-analysis-2",
            "summary": "Customer wants manager.",
            "summaryAr": "العميل يريد التحدث إلى المدير بعد أن استلمه بارداً.",
            "overallSentiment": {"score": -0.5, "label": "Negative"},
            "mainIntent": "Complaint",
            "intentsDetected": [{"name": "Complaint", "count": 1}],
            "mainTopics": ["شكوى"],
            "keyMoments": ["الأوردر وصل بارد"],
        }
    )
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [
                {
                    "sessionId": "manual-analysis-2",
                    "messages": [{"role": "customer", "text": "الأوردر وصل بارد وعايز أكلم المدير"}],
                }
            ],
        },
    )
    result = response.json()["results"][0]
    assert "يريد التحدث" not in result["summaryAr"]
    assert "استلمه بارداً" not in result["summaryAr"]
    assert "عايز يكلم المدير" in result["summaryAr"]


def test_analysis_complaint_handoff_intent_dominates(client, fake_provider):
    fake_provider.analysis_outputs.append(
        {
            "sessionId": "manual-analysis-3",
            "summary": "Customer ordered then complained.",
            "summaryAr": "العميل طلب وبعدين اشتكى.",
            "overallSentiment": {"score": -0.5, "label": "Negative"},
            "mainIntent": "CreateOrder",
            "intentsDetected": [
                {"name": "CreateOrder", "count": 3},
                {"name": "Complaint", "count": 1},
            ],
            "mainTopics": [],
            "keyMoments": [],
        }
    )
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [
                {
                    "sessionId": "manual-analysis-3",
                    "messages": [
                        {"role": "customer", "text": "عايز Classic Burger"},
                        {"role": "customer", "text": "الأوردر وصل بارد وعايز أكلم المدير"},
                    ],
                }
            ],
        },
    )
    result = response.json()["results"][0]
    assert result["mainIntent"] == "Complaint"
    assert result["intentsDetected"][0]["name"] == "Complaint"
    assert any(intent["name"] == "RequestHumanAgent" for intent in result["intentsDetected"])


def test_analysis_uncertainty_fallback_is_valid_but_provider_failure_is_503(client, fake_provider):
    fake_provider.analysis_outputs.append(
        {
            "sessionId": "single",
            "summary": "Unclear single-message session.",
            "summaryAr": "جلسة قصيرة غير واضحة.",
            "overallSentiment": {"score": 0.0, "label": "Neutral"},
            "mainIntent": "Unknown",
            "intentsDetected": [{"name": "Unknown", "count": 1}],
            "mainTopics": [],
            "keyMoments": [],
        }
    )
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [{"sessionId": "single", "messages": [{"role": "customer", "text": "؟"}]}],
        },
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["mainIntent"] == "Unknown"

    fake_provider.analysis_outputs.append(AIProviderError("provider down"))
    response = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [{"sessionId": "bad", "messages": [{"role": "customer", "text": "hi"}]}],
        },
    )
    assert response.status_code == 503


def test_analysis_validation(client):
    too_many = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [
                {"sessionId": "s1", "messages": [{"role": "customer", "text": "hi"}]},
                {"sessionId": "s2", "messages": [{"role": "customer", "text": "hi"}]},
            ],
        },
    )
    assert too_many.status_code == 422

    bad_role = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [{"sessionId": "s1", "messages": [{"role": "system", "text": "hi"}]}],
        },
    )
    assert bad_role.status_code == 422

    blank = client.post(
        "/api/v1/analysis/chat-batch",
        json={
            "businessId": "biz-1",
            "sessions": [{"sessionId": "s1", "messages": [{"role": "customer", "text": "   "}]}],
        },
    )
    assert blank.status_code == 422


def test_pii_remove_endpoint(client):
    response = client.post(
        "/api/v1/analysis/pii-remove",
        json={"text": "email me at test@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["cleanText"] == "email me at [EMAIL]"


def test_health_integration_contract_readiness(client):
    data = client.get("/health/integration").json()
    assert data["mode"] == "signal_based_contract"
    assert data["persistence"] == "in_memory_only"
    assert data["backend_record_creation"] == "backend_owned"
    assert data["routes"]["businessKnowledgeSync"] is True
    assert data["sideEffects"]["createsOrders"] is False


def test_api_key_middleware_protects_api_routes(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_BACKEND_API_KEY", "secret")
    response = client.post("/api/v1/chat", json={})
    assert response.status_code == 401

    response = client.post("/api/v1/chat", json={}, headers={"X-API-Key": "secret"})
    assert response.status_code == 422


def test_manual_sample_kbs_validate_and_index(client):
    for name in [
        "business_kb_restaurant.json",
        "business_kb_cafe.json",
    ]:
        sample = Path("docs/manual_testing") / name
        assert sample.exists()
        payload = json.loads(sample.read_text(encoding="utf-8"))
        BusinessKnowledgeSyncRequest.model_validate(payload)
        sync(client, payload)
