# IRIS AI Contract Documentation

## Overview

IRIS provides the final Backend ↔ AI contract endpoints:

- Business KB sync for full per-business knowledge replacement.
- Customer chat with temporary session/cart memory and structured signals.
- Post-session chat-batch analysis.
- Standalone PII removal reused inside analysis.
- Optional owner analytics over local markdown report files.

The service keeps runtime state in memory only. Restarting the server clears synced KB, sessions, and carts.

## Contract Routes

### `POST /api/v1/business/knowledge-base/sync`

Stores a full business KB in memory by `business_id`. Each call replaces the previous KB for that business.

```json
{
  "business_id": "biz-restaurant-demo",
  "business_name": "Demo Restaurant",
  "knowledge_base": {
    "menu_items": [
      {
        "menu_item_id": "item-1",
        "name": "Classic Burger",
        "description": "Beef burger with cheese",
        "price": 120,
        "category": "Burgers",
        "is_available": true
      }
    ],
    "faqs": [
      {
        "question": "Delivery time",
        "answer": "Delivery takes 30 to 45 minutes.",
        "is_faq": true
      }
    ]
  }
}
```

### `POST /api/v1/chat`

Uses snake_case. The AI only uses the synced KB for the supplied `business_id`.

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-restaurant-demo",
  "message": "عايز Classic Burger"
}
```

Response includes `order_detected`, `order_finalized`, `ticket_detected`, `escalation_requested`, and `feedback_requested`. The AI never creates backend records.

### `POST /api/v1/analysis/chat-batch`

Uses camelCase. V1 supports exactly one session per request.

```json
{
  "businessId": "biz-restaurant-demo",
  "sessions": [
    {
      "sessionId": "interaction-123",
      "messages": [
        {"role": "customer", "text": "عايز أطلب"},
        {"role": "assistant", "text": "تمام يا فندم"}
      ]
    }
  ]
}
```

PII is redacted before summaries, topics, and key moments are generated.

### `POST /api/v1/analysis/pii-remove`

```json
{
  "text": "email me at test@example.com"
}
```

## Architecture

- No SQLite or permanent AI-side persistence.
- No semantic cache.
- No public upload endpoint.
- No static FAISS or local menu fallback in contract chat.
- No backend webhook calls for order, ticket, escalation, feedback, or analysis records.
- Owner analytics is auxiliary and reads markdown files from `OWNER_ANALYTICS_REPORT_DIR`.
- Tests are deterministic and do not require external network or OpenAI calls.

## Removed Features

- Voice, STT, and TTS.
- Hidden file helper routes.
- Public order creation, ticket, feedback, HITL, upload, synonym admin, and cache admin routes.
- Semantic cache and cache prewarm.
- Static menu fallback for contract chat.
- Old backend webhook creation services.

## Manual Testing

Use [docs/manual_testing/README.md](manual_testing/README.md). The supported workflow is: start the app, sync a KB through Swagger or `scripts/seed_manual_kb.py`, then test chat and analysis through the real public endpoints.
