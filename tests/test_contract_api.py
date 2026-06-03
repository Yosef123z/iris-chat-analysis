import json
from pathlib import Path

from app.config import settings
from app.core.llm_interface import AIProviderError
from app.models.business_kb import BusinessKnowledgeSyncRequest
from app.models.chat import OrderLineItem
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
    assert "Egyptian Arabic" in text
    assert "Do not invent" in text
    assert "Never mention backend" in text


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
    assert data["ticket_details"]["priority"] == "normal"
    assert data["ticket_details"]["category"] == "other"
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


def test_non_restaurant_kb_uses_generic_business_context(client, fake_provider):
    sync(client, clinic_kb())
    fake_provider.chat_outputs.append(llm_chat_output(reply="Dental Cleaning متاحة يا فندم."))
    data = chat(client, "clinic-1", "clinic-session", "Tell me about Dental Cleaning")
    assert "Dental Cleaning" in data["reply"]
    text = prompt_text(fake_provider)
    assert "Dental Cleaning" in text
    assert "products/services/items" in text
    assert "restaurant" not in text.lower()


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
        "business_kb_non_restaurant.json",
    ]:
        sample = Path("docs/manual_testing") / name
        assert sample.exists()
        payload = json.loads(sample.read_text(encoding="utf-8"))
        BusinessKnowledgeSyncRequest.model_validate(payload)
        sync(client, payload)
