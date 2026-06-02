# IRIS AI Contract API

IRIS is a FastAPI AI service for backend integration. It exposes business knowledge-base sync, customer chat signals, chat-batch analysis, PII removal, and optional owner analytics.

## Public API

- `GET /`
- `GET /health`
- `GET /health/integration`
- `GET /metrics`
- `POST /api/v1/business/knowledge-base/sync`
- `POST /api/v1/chat`
- `POST /api/v1/analysis/chat-batch`
- `POST /api/v1/analysis/pii-remove`

Auxiliary owner analytics routes may be used by the dashboard:

- `POST /api/v1/owner/chat`
- `GET /api/v1/owner/report`
- `POST /api/v1/owner/reload`

## Runtime Model

- Business KB sync uses snake_case and stores data in memory by `business_id`.
- Chat uses snake_case and only reads synced KB for the request business.
- Analysis uses camelCase and applies PII redaction before analysis.
- Session/cart memory is temporary and expires after roughly two hours.
- The AI returns order, ticket, escalation, and feedback signals only.
- The backend owns all permanent records and persistence.
- There is no semantic cache, database, static menu fallback, hidden file route, public upload route, backend webhook creation, or voice interface.

## Setup

```powershell
Copy-Item env.example .env
pip install -r requirements.txt
python scripts/run_server.py
```

`OPENAI_API_KEY` is optional for deterministic contract behavior, but required for LLM-backed owner analytics.

Manual contract testing is documented in [docs/manual_testing/README.md](docs/manual_testing/README.md).

## Tests

```powershell
python -m pytest -q
```
