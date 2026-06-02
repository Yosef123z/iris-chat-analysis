# Manual Contract Testing

No .NET backend is required to test the AI contract endpoints manually.

## Swagger Workflow

1. Start the server:

   ```powershell
   python scripts/run_server.py
   ```

2. Open `http://localhost:8000/docs`.
3. POST one sample KB to `/api/v1/business/knowledge-base/sync`.
4. POST to `/api/v1/chat` using the same `business_id`.
5. Try product questions, price questions, adding an available item, confirming an order, unavailable items, complaints, human requests, and unknown business IDs.
6. POST to `/api/v1/analysis/chat-batch`.
7. Verify camelCase analysis fields and that PII does not appear in generated summaries/topics/key moments.

State is in memory only. Restarting the server clears synced KB and session/cart state.

## Sample Files

- `business_kb_restaurant.json`
- `business_kb_cafe.json`
- `business_kb_non_restaurant.json`
- `chat_examples.md`
- `analysis_chat_batch_examples.json`

## Seed Script

You can post a sample KB through the real public endpoint:

```powershell
python scripts/seed_manual_kb.py --base-url http://localhost:8000 --file docs/manual_testing/business_kb_restaurant.json
```
