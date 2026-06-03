# Manual Contract Testing

Manual testing uses the same public endpoints and code paths as backend integration.
No hidden helper route bypasses KB sync, vector indexing, chat, or analysis logic.

## Automated Contract Tests

Run:

```powershell
python -m pytest -q
```

Automated tests inject fake LLM and fake embeddings providers. They verify contracts,
per-business indexing, prompt grounding, business isolation, sanitization, and provider
failure behavior without making OpenAI or network calls.

## Manual AI Quality Tests

Manual quality testing requires a real `OPENAI_API_KEY`.

1. Set `OPENAI_API_KEY` in `.env`.
2. Start the server:

   ```powershell
   python scripts/run_server.py
   ```

3. Open `http://localhost:8000/docs`.
4. POST a sample KB to `/api/v1/business/knowledge-base/sync`.
5. Confirm sync succeeds; this builds the real per-business in-memory vector index.
6. POST to `/api/v1/chat` using the same `business_id`.
7. POST to `/api/v1/analysis/chat-batch`.

The `/tools/customer_chat.html` and `/tools/owner_chat.html` pages may be used as local
manual-testing UIs only. They must call the same public endpoints listed above.

## Report Generation Manual Test

Report generation is a backend-to-AI endpoint. In production the frontend should call
the .NET backend, and the .NET backend should call this AI endpoint with aggregated
analysis data.

1. Make sure `OPENAI_API_KEY` is set.
2. Start the AI service.
3. Open Swagger at `http://localhost:8000/docs`.
4. Call `POST /api/v1/analysis/report/generate`.
5. Use `docs/manual_testing/report_generation_example.json` as the payload.
6. Verify the response includes `reportTitle`, `summaryAr`, `highlightsAr`, `problems`,
   `recommendations`, `suggestedActions`, and `riskLevel`.
7. Verify recommendations are tied to the provided data.
8. Verify no invented numbers appear in the report.
9. Verify Arabic is business-friendly.
10. Verify the frontend does not call this endpoint directly in production.

## Seed Script

You can post a sample KB through the real public endpoint:

```powershell
python scripts/seed_manual_kb.py --base-url http://localhost:8000 --file docs/manual_testing/business_kb_restaurant.json
```

## Sample Files

- `business_kb_restaurant.json`
- `business_kb_cafe.json`
- `business_kb_non_restaurant.json`
- `chat_examples.md`
- `analysis_chat_batch_examples.json`
- `report_generation_example.json`

## Manual Checklist

- Restaurant KB sync and chat.
- Cafe KB sync and chat.
- Clinic or non-restaurant KB sync and chat.
- Missing KB chat response.
- Product/service question.
- Price question.
- Add item/service to cart.
- Confirm order or booking-style request.
- Unavailable item.
- Complaint.
- Human request.
- Complaint plus human request.
- Unknown information not present in KB.
- Business isolation across at least two `business_id` values.
- Analysis with PII.
- Analysis for a single-message session.
- Report generation from aggregated analysis data.
- Report recommendations tied to provided counts, issues, moments, and summaries.
- Provider failure behavior when possible.

Expected real AI behavior:

- Customer replies are natural Egyptian Arabic by default.
- Common formal Arabic phrases are normalized toward Egyptian Arabic after the LLM response.
- Replies are grounded in the synced KB only.
- No hallucinated items, prices, services, policies, or availability.
- Order item names match canonical KB names exactly.
- Clear customer confirmation phrases force `order_finalized=true` only when a valid cart exists.
- Informational product/service questions, such as price or details requests, do not start a cart.
- Informational product/service questions do not ask to add the item or service to an order.
- Cancel order requests clear the current session cart and return no active order details.
- Unavailable items are not finalized.
- Attempts to order unavailable items return an empty `CreateOrder` details object for contract consistency.
- Escalation-only messages request human handoff without creating a ticket; complaint plus escalation creates a high-priority ticket and handoff signal.
- Non-restaurant KBs do not receive restaurant-specific language.
- Chat-batch summaries, topics, and key moments are meaningful and PII-free; empty topics or key moments may be supplemented from the redacted transcript.
