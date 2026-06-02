from app.config import settings
from app.models.business_kb import BusinessKnowledgeSyncRequest


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
                }
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
    assert response.status_code == 200
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


def test_openapi_final_paths(client):
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


def test_missing_business_id_returns_422(client):
    response = client.post("/api/v1/chat", json={"session_id": "s1", "message": "hi"})
    assert response.status_code == 422


def test_missing_kb_returns_safe_false_flags(client):
    data = chat(client, "unknown", "s1", "hello")
    assert data["order_detected"] is False
    assert data["order_finalized"] is False
    assert data["ticket_detected"] is False
    assert data["escalation_requested"] is False
    assert data["feedback_requested"] is False
    assert data["order_details"] is None
    assert data["ticket_details"] is None


def test_kb_sync_replaces_existing_business_kb(client):
    sync(client, restaurant_kb())
    data = chat(client, "biz-1", "s1", "What products do you have?")
    assert "Classic Burger" in data["reply"]

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
    assert "Classic Burger" not in data["reply"]


def test_business_isolation(client):
    sync(client, restaurant_kb("biz-1"))
    payload = restaurant_kb("biz-2")
    payload["knowledge_base"]["menu_items"][0]["name"] = "Margherita Pizza"
    sync(client, payload)
    data = chat(client, "biz-2", "s1", "What products do you have?")
    assert "Margherita Pizza" in data["reply"]
    assert "Classic Burger" not in data["reply"]


def test_non_restaurant_kb_response(client):
    sync(client, clinic_kb())
    data = chat(client, "clinic-1", "clinic-session", "Tell me about Dental Cleaning")
    assert "Dental Cleaning" in data["reply"]
    assert data["order_detected"] is False


def test_cart_building_and_confirmation(client):
    sync(client, restaurant_kb())
    first = chat(client, "biz-1", "cart-1", "عايز Classic Burger")
    assert first["order_detected"] is True
    assert first["order_finalized"] is False
    assert first["order_details"]["items"][0]["name"] == "Classic Burger"
    assert "system_events" not in first

    second = chat(client, "biz-1", "cart-1", "تمام أكد الطلب")
    assert second["order_detected"] is True
    assert second["order_finalized"] is True
    assert second["order_details"]["items"][0]["name"] == "Classic Burger"


def test_unavailable_item_not_added(client):
    sync(client, restaurant_kb())
    data = chat(client, "biz-1", "s-unavailable", "عايز Crispy Chicken Burger")
    assert data["order_detected"] is True
    assert data["order_finalized"] is False
    assert data["order_details"]["items"] == []
    assert "Crispy Chicken Burger" in data["reply"]


def test_ticket_escalation_and_combined_signals(client):
    sync(client, restaurant_kb())
    ticket = chat(client, "biz-1", "support-1", "الأوردر وصل بارد")
    assert ticket["ticket_detected"] is True
    assert ticket["ticket_details"]["priority"] == "high"
    assert ticket["escalation_requested"] is False

    escalation = chat(client, "biz-1", "support-2", "عايز أكلم المدير")
    assert escalation["ticket_detected"] is False
    assert escalation["escalation_requested"] is True

    both = chat(client, "biz-1", "support-3", "الأوردر وصل غلط وعايز أكلم المدير حالا")
    assert both["ticket_detected"] is True
    assert both["escalation_requested"] is True
    assert both["ticket_details"]["priority"] == "critical"


def test_analysis_chat_batch_contract_and_pii(client):
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
    combined = " ".join([result["summary"], result["summaryAr"], *result["mainTopics"], *result["keyMoments"]])
    assert "test@example.com" not in combined


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


def test_manual_sample_kb_validates():
    import json
    from pathlib import Path

    sample = Path("docs/manual_testing/business_kb_restaurant.json")
    assert sample.exists()
    BusinessKnowledgeSyncRequest.model_validate(json.loads(sample.read_text(encoding="utf-8")))
