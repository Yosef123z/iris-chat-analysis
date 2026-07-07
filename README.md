# IRIS AI Contract API

IRIS is a FastAPI AI service for backend integration. It exposes business knowledge-base sync, LLM-backed customer chat signals, LLM-backed chat-batch analysis, PII removal, and optional owner analytics.

## Public API

- `GET /`
- `GET /health`
- `GET /health/integration`
- `GET /metrics`
- `POST /api/v1/business/knowledge-base/sync`
- `POST /api/v1/chat`
- `POST /api/v1/analysis/chat-batch`
- `POST /api/v1/analysis/pii-remove`
- `POST /api/v1/analysis/report/generate`

Auxiliary owner analytics routes may be used by the dashboard:

- `POST /api/v1/owner/reports/sync`
- `POST /api/v1/owner/chat`

## Runtime Model

- Business KB sync uses snake_case and builds/replaces an in-memory vector index by `business_id`.
- Customer Chat uses snake_case and performs LLM-backed RAG over **only** the synced Business Knowledge Base for the request business.
- Analysis uses camelCase and calls the LLM after PII redaction.
- Report generation uses camelCase and is called by the backend with already aggregated analysis results.
- Owner Chat is grounded in the latest synced owner report context pushed via `POST /api/v1/owner/reports/sync`. The synced context now contains both the generated `report` and raw backend `metrics`.
- Owner Chat uses raw `metrics` first for factual questions (menu items, prices, availability, FAQs, orders, tickets, best sellers) and the generated `report` sections for summaries, problems, recommendations, and risk.
- Owner Chat metrics update only when a new report is generated/synced. Real-time owner menu sync is a separate future request.
- Session/cart memory is temporary and expires after roughly two hours.
- The AI returns order, ticket, escalation, and feedback signals only.
- The backend owns all permanent records and persistence.
- The AI report endpoint returns structured report wording and recommendations only; it does not store reports or access the backend database.
- There is no semantic cache, database, static menu fallback, global FAISS, hidden helper API route, public upload route, backend webhook creation, or voice interface.

## Setup

```powershell
Copy-Item env.example .env
pip install -r requirements.txt
python scripts/run_server.py --> http://localhost:8000/docs
```

`OPENAI_API_KEY` is required for real KB vector indexing, real customer chat, real chat-batch analysis, real report generation, and LLM-backed owner analytics. Report generation reuses `ANALYSIS_MODEL`. Automated tests do not require it because they inject fake LLM and fake embeddings providers.

Manual contract testing is documented in [docs/manual_testing/README.md](docs/manual_testing/README.md).

## Tests

```powershell
python -m pytest -q
```
