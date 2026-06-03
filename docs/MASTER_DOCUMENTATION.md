# IRIS AI Contract Documentation

## Overview

IRIS provides the final Backend ↔ AI contract endpoints:

- Business KB sync for full per-business knowledge replacement and in-memory vector indexing.
- LLM-backed customer chat with RAG grounding, temporary session/cart memory, and structured signals.
- LLM-backed post-session chat-batch analysis after PII redaction.
- LLM-backed dashboard report generation from backend-aggregated analysis data.
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

### `POST /api/v1/analysis/report/generate`

Uses camelCase. This endpoint is called by the .NET backend, not the frontend. The backend owns business authorization, date filtering, aggregation, storage, idempotency, and report history. The AI service receives compact aggregate analysis data, calls the configured LLM with a strict JSON prompt, and returns report wording plus recommendations.

The AI service does not read the backend database, does not store reports, and does not require raw chat transcripts.

```json
{
  "businessId": "biz-restaurant-demo",
  "businessName": "Demo Restaurant",
  "period": {
    "from": "2026-06-01T00:00:00Z",
    "to": "2026-06-30T23:59:59Z"
  },
  "metrics": {
    "totalSessions": 120,
    "analyzedSessions": 115,
    "averageSentimentScore": -0.12,
    "sentimentDistribution": {
      "positive": 35,
      "neutral": 50,
      "negative": 30
    },
    "totalComplaints": 22,
    "totalHumanAgentRequests": 14,
    "totalOrdersDetected": 60
  },
  "topIntents": [{"name": "CreateOrder", "count": 60}],
  "topTopics": [{"name": "delivery", "count": 18}],
  "commonIssues": [
    {
      "issue": "Orders arriving cold",
      "count": 12,
      "examples": ["Customer received cold burger"]
    }
  ],
  "recentKeyMoments": ["Customer received cold burger"],
  "sampleSummaries": [
    {
      "sessionId": "session-001",
      "summary": "Customer ordered a Classic Burger but received it cold.",
      "summaryAr": "العميل طلب Classic Burger لكن الأوردر وصل بارد.",
      "mainIntent": "Complaint",
      "sentimentLabel": "Negative",
      "sentimentScore": -0.8
    }
  ]
}
```

Response fields include `reportTitle`, `summary`, `summaryAr`, `highlights`, `highlightsAr`, `problems`, `recommendations`, `suggestedActions`, and `riskLevel`. `riskLevel`, problem `severity`, and recommendation `priority` are limited to `low`, `medium`, `high`, or `critical`. Provider or invalid model output failures return HTTP 503 with `AI report generation failed`.

## Architecture

- No SQLite or permanent AI-side persistence.
- No semantic cache.
- No public upload endpoint.
- No static FAISS, global FAISS, or local menu fallback in contract chat.
- No backend webhook calls for order, ticket, escalation, feedback, or analysis records.
- Customer chat and chat-batch analysis do not return fake deterministic success when the LLM provider fails.
- Report generation does not return fake deterministic recommendations when the LLM provider fails.
- Automated tests inject fake LLM and fake embeddings providers and do not call OpenAI.
- Real manual testing and backend integration require `OPENAI_API_KEY`. Report generation reuses `ANALYSIS_MODEL`.
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
