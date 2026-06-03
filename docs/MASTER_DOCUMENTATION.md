# IRIS AI Contract Documentation

## Overview

IRIS provides the final Backend ↔ AI contract endpoints:

- Business KB sync for full per-business knowledge replacement and in-memory vector indexing.
- LLM-backed customer chat with RAG grounding, temporary session/cart memory, and structured signals.
- LLM-backed post-session chat-batch analysis after PII redaction.
- Standalone PII removal reused inside analysis.
- Optional owner analytics over local markdown report files.

The service keeps runtime state in memory only. Restarting the server clears synced KB, sessions, and carts.

## Contract Routes

### `POST /api/v1/business/knowledge-base/sync`

Stores a full business KB in memory by `business_id` and builds/replaces that business's in-memory vector index. Each call replaces the previous KB and index for that business only.

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

Uses snake_case. The AI retrieves from the per-business vector index for the supplied `business_id`, builds a grounded prompt, calls the configured LLM provider, validates structured signals, and returns the final response.
After the LLM returns, deterministic post-validation policies enforce contract-critical signals: explicit order confirmation finalizes only a valid cart, informational product/service questions do not create orders, unavailable item order attempts return an empty `CreateOrder` payload, escalation-only handoff does not create a ticket, and customer-facing Arabic is lightly normalized toward Egyptian Arabic.

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

PII is redacted before the LLM receives the transcript. Provider failure returns a controlled service error; model uncertainty can still produce valid fallback analysis values.
Analysis output is also post-validated: Egyptian Arabic summary wording is lightly normalized, complaint plus human-handoff sessions are ranked as complaint-led, and empty topics/key moments may be supplemented from the redacted transcript.

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
- No static FAISS, global FAISS, or local menu fallback in contract chat.
- No backend webhook calls for order, ticket, escalation, feedback, or analysis records.
- Customer chat and chat-batch analysis do not return fake deterministic success when the LLM provider fails.
- Automated tests inject fake LLM and fake embeddings providers and do not call OpenAI.
- Real manual testing and backend integration require `OPENAI_API_KEY`.
- Owner analytics is auxiliary and reads markdown files from `OWNER_ANALYTICS_REPORT_DIR`.
- The `/tools` static pages are local manual-testing UIs only and call the same public endpoints used by backend integration.

## Removed Features

- Voice, STT, and TTS.
- Hidden file helper routes.
- Public order creation, ticket, feedback, HITL, upload, synonym admin, and cache admin routes.
- Semantic cache and cache prewarm.
- Static menu fallback for contract chat.
- Old backend webhook creation services.

## Manual Testing

Use [docs/manual_testing/README.md](manual_testing/README.md). The supported workflow is: start the app, sync a KB through Swagger or `scripts/seed_manual_kb.py`, then test chat and analysis through the real public endpoints.
