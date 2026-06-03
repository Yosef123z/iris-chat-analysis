# Chat Contract — Final (Backend ↔ AI)

> Last updated after full contract review, backend questions, and AI team clarifications.  
> This file is the **single source of truth** for the Chat integration.  
> Ignore all previous chat contract files.

---

## Base URL

The AI service base URL is configured in backend `appsettings.json` under:

```text
AiService:BaseUrl
```

All paths below are relative to that base URL.

---

# 1. Knowledge Base Sync

**Direction:** Backend → AI  
**Trigger:** Automatically called whenever:

- A new business is created
    
- Any menu item is added / updated / deleted
    
- Any knowledge base entry is added / updated / deleted
    

**Endpoint:**

```http
POST /api/v1/business/knowledge-base/sync
```

---

## Request Body

```json
{
  "business_id": "string",
  "business_name": "string",

  "knowledge_base": {
    "menu_items": [
      {
        "menu_item_id": "string",
        "name": "string",
        "description": "string | null",
        "price": 49.99,
        "category": "string | null",
        "is_available": true
      }
    ],
    "faqs": [
      {
        "question": "string",
        "answer": "string",
        "is_faq": true
      }
    ]
  }
}
```

---

## Field Notes

|Field|Notes|
|---|---|
|`business_id`|AI must index/cache this data by `business_id`.|
|`business_name`|Business display name. Used for context/personality if needed.|
|`knowledge_base.menu_items`|Canonical menu items for this business.|
|`menu_items[].menu_item_id`|Backend menu item identifier.|
|`menu_items[].name`|Exact canonical item name. AI must echo this exactly in `order_details.items[].name`.|
|`menu_items[].description`|Optional item description. Used for answering product questions.|
|`menu_items[].price`|Current item price. Informational in AI response; backend recomputes final prices.|
|`menu_items[].category`|Optional menu category. Useful for alternatives and product grouping.|
|`menu_items[].is_available`|AI uses this to avoid offering unavailable items when answering or building carts.|
|`knowledge_base.faqs`|Business FAQs and general knowledge base entries.|
|`faqs[].question`|FAQ question or knowledge base entry title.|
|`faqs[].answer`|Answer/content used by the AI for customer replies.|
|`faqs[].is_faq`|`true` = explicit FAQ from business owner. `false` = general knowledge base entry. Both must be used for answering.|

---

## Expected Response

```http
HTTP 200 OK
```

No response body is required.

---

## Important Rules

- This endpoint is **not session-based**.
    
- There is no `session_id` in this endpoint.
    
- The AI caches/indexes the data by `business_id`.
    
- During any chat, the AI uses `business_id` to retrieve the correct cached knowledge base.
    
- Each sync call **replaces** the entire knowledge base for that business.
    
- This is a full sync, not a delta update.
    
- The AI must not mix knowledge between businesses.
    
- If a business knowledge base is updated, the next chat message for that `business_id` should use the updated data.
    

---

# 2. Chat Message

**Direction:** Backend → AI  
**Endpoint:**

```http
POST /api/v1/chat
```

Called by the backend for every customer text message.

---

## Request Body

```json
{
  "session_id": "string",
  "business_id": "string",
  "message": "string"
}
```

---

## Request Fields

|Field|Required|Notes|
|---|---|---|
|`session_id`|Yes|Maps to `Interaction.InteractionId` in backend DB. Same value is used for the entire conversation.|
|`business_id`|Yes|AI uses this to load the correct cached KB for the business.|
|`message`|Yes|Customer's latest text message.|

---

## Conversation History Rule

The AI maintains conversation history internally by:

```text
session_id
```

The backend does **not** need to send previous messages with every chat request.

The backend only sends the latest customer message:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "عايز أضيف كولا"
}
```

The AI uses `session_id` to remember the conversation context, such as:

- Previous customer messages
    
- Previous assistant replies
    
- Current cart state
    
- Whether the customer is still building an order
    
- Whether the customer is confirming an order
    

The backend should still store all messages in its own database because the backend remains the source of truth and uses the full conversation later for post-session analysis.

---

# 3. Chat Response

**Direction:** AI → Backend

```json
{
  "session_id": "string",
  "reply": "string",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

---

## Response Body When Order Is Detected

```json
{
  "session_id": "string",
  "reply": "تمام يا فندم، ضفت Classic Burger. تحب تضيف حاجة تانية؟",

  "order_detected": true,
  "order_finalized": false,
  "order_details": {
    "intent": "CreateOrder",
    "items": [
      {
        "name": "Classic Burger",
        "quantity": 1,
        "price": 49.99,
        "notes": null
      }
    ],
    "total_amount": 49.99
  },

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

---

## Response Body When Ticket Is Detected

```json
{
  "session_id": "string",
  "reply": "معلش يا فندم، هسجل المشكلة لفريق الدعم.",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": true,
  "ticket_details": {
    "subject": "Customer Complaint",
    "description": "الأوردر وصل بارد",
    "priority": "high",
    "category": "delivery"
  },

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

---

# 4. Response Field Rules

## Always Returned Fields

|Field|Type|Required|Notes|
|---|---|---|---|
|`session_id`|string|Yes|Same session id received in the request.|
|`reply`|string|Yes|Natural language response shown to the customer.|
|`order_detected`|boolean|Yes|Whether an ordering flow is active or detected.|
|`order_finalized`|boolean|Yes|Whether the customer confirmed the final order.|
|`ticket_detected`|boolean|Yes|Whether a complaint / issue was detected.|
|`escalation_requested`|boolean|Yes|Whether the session should be transferred to a human.|
|`feedback_requested`|boolean|Yes|Whether frontend should prompt the customer for rating.|
|`processing_time_ms`|number/null|No|Optional processing time in milliseconds. Useful for logs/monitoring.|

---

## Order Fields

|Field|When|Backend Action|
|---|---|---|
|`order_detected: true`|Customer is building a cart|No DB action. Backend only shows reply.|
|`order_finalized: true`|Customer confirmed the order|Backend creates Order in DB.|
|`order_details`|When `order_detected` or `order_finalized` is true|Backend matches `items[].name` to `MenuItem.Name` to get `MenuItemId`.|
|`order_details.items[].price`|When item exists|Informational only. Backend uses price from menu DB.|
|`order_details.total_amount`|When order details exist|Informational only. Backend recomputes from menu DB prices.|

---

## Order Details Structure

```json
{
  "intent": "CreateOrder",
  "items": [
    {
      "name": "Classic Burger",
      "quantity": 1,
      "price": 49.99,
      "notes": null
    }
  ],
  "total_amount": 49.99
}
```

|Field|Type|Required|Notes|
|---|---|---|---|
|`intent`|string/null|No|Usually `"CreateOrder"`, `"ModifyOrder"`, or `"CancelOrder"` if applicable.|
|`items`|array|Yes|Extracted cart items.|
|`items[].name`|string|Yes|Must exactly match canonical `menu_items[].name` from KB sync.|
|`items[].quantity`|number|Yes|Requested quantity.|
|`items[].price`|number|Yes|Informational price from AI KB. Backend recomputes.|
|`items[].notes`|string/null|No|Customer customizations or notes.|
|`total_amount`|number|Yes|Informational total. Backend recomputes.|

---

## Order Finalization Rule

If:

```json
{
  "order_finalized": true
}
```

then the AI must also return:

```json
{
  "order_detected": true
}
```

`order_finalized = true` always implies `order_detected = true`.

The backend should create an order only when:

```json
{
  "order_finalized": true
}
```

`order_detected = true` alone means the customer is still building the cart and the backend must not create an order yet.

---

## Ticket Fields

|Field|When|Backend Action|
|---|---|---|
|`ticket_detected: true`|Complaint / issue detected|Backend creates Ticket in DB.|
|`ticket_details.priority`|Supported values: `low`, `normal`, `high`, `critical`|Maps to `Ticket.PriorityLevel`.|
|`ticket_details.category`|e.g. `complaint`, `quality`, `delivery`, `payment`, `wrong_order`|Maps to `Ticket.TicketType`.|

---

## Ticket Details Structure

```json
{
  "subject": "Customer Complaint",
  "description": "الأوردر وصل بارد",
  "priority": "high",
  "category": "delivery"
}
```

|Field|Type|Required|Notes|
|---|---|---|---|
|`subject`|string|Yes|Short ticket subject.|
|`description`|string/null|No|Customer issue in their own words.|
|`priority`|string|Yes|One of: `low`, `normal`, `high`, `critical`.|
|`category`|string/null|No|Ticket category.|

---

## Supported Ticket Priorities

```text
low
normal
high
critical
```

---

## Supported Ticket Categories

Recommended values:

```text
complaint
quality
delivery
payment
wrong_order
missing
other
```

---

## Escalation & Feedback

|Field|Backend Action|
|---|---|
|`escalation_requested: true`|Create a HumanEscalation ticket and set `Interaction.Status = "Escalated"`.|
|`feedback_requested: true`|Prompt customer for a rating from 1 to 5. Backend stores feedback in DB.|

---

## Ticket vs Escalation Rule

`ticket_detected` and `escalation_requested` are separate signals.

They can be returned separately or together.

---

### Complaint Only

```json
{
  "ticket_detected": true,
  "escalation_requested": false
}
```

Meaning:

- Backend creates a Complaint Ticket.
    
- Conversation can continue normally with AI.
    

---

### Escalation Only

```json
{
  "ticket_detected": false,
  "escalation_requested": true
}
```

Meaning:

- Backend creates a HumanEscalation ticket.
    
- Backend sets `Interaction.Status = "Escalated"`.
    
- Future customer messages should be routed according to the backend human handoff flow.
    

---

### Complaint + Escalation Together

```json
{
  "ticket_detected": true,
  "escalation_requested": true
}
```

This can happen when the customer reports an issue and also asks for a human/manager, or when frustration is very high.

Example customer message:

```text
الأوردر وصل غلط وأنا عايز أكلم المدير حالًا
```

Backend behavior:

- Create Complaint Ticket.
    
- Create HumanEscalation ticket.
    
- Set `Interaction.Status = "Escalated"`.
    

---

# 5. Session Lifecycle

## Session History Ownership

The AI maintains temporary conversation history by `session_id`.

The backend maintains permanent conversation storage in its database.

---

## Reusing session_id After Interaction Closure

A `session_id` should not be reused after the backend closes the interaction.

Recommended rule:

```text
Closed interaction = do not reuse session_id
New interaction = create a new session_id
```

If the backend reuses the same `session_id` after closure, the AI may treat the message as a continuation of the old conversation if the old history still exists.

Therefore, backend must generate a new `session_id` for each new interaction.

---

## Session Expiry on AI Side

The AI may expire inactive session history after:

```text
2 hours of inactivity
```

This expiry only affects AI temporary memory.

It does not affect backend storage.

If the backend sends a message with an old `session_id` after the AI memory expired, the AI may treat it as a fresh session from the AI memory perspective.

Normal expected backend behavior:

- Use same `session_id` during one active interaction.
    
- Call Analysis when the interaction ends.
    
- Do not reuse the closed `session_id`.
    
- Create a new `session_id` for a new interaction.
    

---

# 6. Knowledge Base and Availability Rules

## Business-Based KB Routing

During chat, the AI must use:

```text
business_id
```

to load the correct cached KB.

Example:

```json
{
  "business_id": "biz-1",
  "message": "عندكم ايه من برجر؟"
}
```

The AI must answer from the KB cached for `biz-1`.

The AI must not answer using another business's KB.

---

## Canonical Menu Names

The AI must return canonical menu item names exactly as received in:

```text
knowledge_base.menu_items[].name
```

Correct:

```json
{
  "name": "Classic Burger"
}
```

Incorrect:

```json
{
  "name": "classic burger sandwich"
}
```

Incorrect:

```json
{
  "name": "كلاسيك برجر"
}
```

Incorrect:

```json
{
  "name": "Burger"
}
```

---

## Availability

The AI uses:

```text
menu_items[].is_available
```

to avoid offering unavailable items when answering customers or building carts.

If the customer asks for an unavailable item, the AI should:

- Not add the unavailable item to the cart.
    
- Not include it in a finalized order.
    
- Tell the customer that the item is currently unavailable.
    
- Suggest available alternatives if possible, preferably from the same category.
    

The backend must still validate availability before saving any order because the backend database is the final source of truth.

---

# 7. Full Flow

```text
1. Business setup / data change
   └─► Backend calls POST /api/v1/business/knowledge-base/sync
       └─► AI indexes/caches KB by business_id

2. Customer sends a message
   └─► Backend calls POST /api/v1/chat { session_id, business_id, message }
       └─► AI loads KB by business_id
       └─► AI loads conversation memory by session_id
       └─► AI processes message
       └─► AI returns reply + signals

3. Backend reads signals:
   ├── order_finalized = true
   │   └─► Create Order in DB after validating items, prices, and availability
   │
   ├── ticket_detected = true
   │   └─► Create Ticket in DB
   │
   └── escalation_requested = true
       └─► Create HumanEscalation ticket + update Interaction status

4. Same session_id is used for all messages in one active conversation.

5. When conversation ends:
   └─► Backend invokes EndInteraction
       └─► Backend triggers Analysis
           └─► See AI_ANALYSIS_CONTRACT_FINAL.md
```

---

# 8. Worked Examples

## Menu Browsing

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "عندكم ايه من برجر؟"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "عندنا Classic Burger و Crispy Chicken Burger. تحب أقولك تفاصيل أي واحد فيهم؟",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

---

## Cart Building

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "عايز Classic Burger"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "تمام يا فندم، ضفت Classic Burger. تحب تضيف حاجة تانية؟",

  "order_detected": true,
  "order_finalized": false,
  "order_details": {
    "intent": "CreateOrder",
    "items": [
      {
        "name": "Classic Burger",
        "quantity": 1,
        "price": 49.99,
        "notes": null
      }
    ],
    "total_amount": 49.99
  },

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

---

## Order Finalization

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "تمام كده أكد الطلب"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "تمام يا فندم، جاري تحضير طلبك.",

  "order_detected": true,
  "order_finalized": true,
  "order_details": {
    "intent": "CreateOrder",
    "items": [
      {
        "name": "Classic Burger",
        "quantity": 1,
        "price": 49.99,
        "notes": null
      }
    ],
    "total_amount": 49.99
  },

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

Backend action:

- Validate item exists.
    
- Validate item belongs to `business_id`.
    
- Validate item is available.
    
- Recompute price.
    
- Create Order in DB.
    

---

## Complaint Only

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "الأوردر وصل بارد"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "معلش يا فندم، هسجل المشكلة لفريق الدعم.",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": true,
  "ticket_details": {
    "subject": "Customer Complaint",
    "description": "الأوردر وصل بارد",
    "priority": "high",
    "category": "delivery"
  },

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

Backend action:

- Create Complaint Ticket.
    
- Keep conversation in normal AI mode unless escalation is also requested.
    

---

## Escalation Only

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "عايز أكلم حد من الإدارة"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "تمام يا فندم، حد من الإدارة هيتابع مع حضرتك.",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": true,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

Backend action:

- Create HumanEscalation ticket.
    
- Set `Interaction.Status = "Escalated"`.
    

---

## Complaint + Escalation

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "الأوردر وصل غلط وأنا عايز أكلم المدير حالًا"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "معلش جدًا يا فندم، هسجل المشكلة فورًا وحد من الإدارة هيتابع مع حضرتك.",

  "order_detected": false,
  "order_finalized": false,
  "order_details": null,

  "ticket_detected": true,
  "ticket_details": {
    "subject": "Customer Complaint",
    "description": "الأوردر وصل غلط والعميل طلب التحدث مع المدير",
    "priority": "critical",
    "category": "wrong_order"
  },

  "escalation_requested": true,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

Backend action:

- Create Complaint Ticket.
    
- Create HumanEscalation ticket.
    
- Set `Interaction.Status = "Escalated"`.
    

---

## Out of Stock Item

If synced KB says:

```json
{
  "name": "Classic Burger",
  "is_available": false
}
```

Backend request:

```json
{
  "session_id": "interaction-123",
  "business_id": "biz-1",
  "message": "عايز Classic Burger"
}
```

AI response:

```json
{
  "session_id": "interaction-123",
  "reply": "معلش يا فندم، Classic Burger مش متاح حاليًا. ممكن تختار صنف تاني من البرجر؟",

  "order_detected": true,
  "order_finalized": false,
  "order_details": {
    "intent": "CreateOrder",
    "items": [],
    "total_amount": 0
  },

  "ticket_detected": false,
  "ticket_details": null,

  "escalation_requested": false,
  "feedback_requested": false,

  "processing_time_ms": 120
}
```

Backend action:

- Do not create order.
    
- Do not save unavailable item.
    
- Continue conversation normally.
    

---

# 9. Backend Responsibilities

The backend must:

- Call KB sync when a business is created.
    
- Call KB sync when menu items or KB entries change.
    
- Send `session_id`, `business_id`, and latest `message` for each chat request.
    
- Use a new `session_id` for each new interaction.
    
- Not reuse `session_id` after interaction closure.
    
- Store all messages permanently in DB.
    
- Trigger Analysis when `EndInteraction` is invoked.
    
- Create orders only when `order_finalized = true`.
    
- Validate item names against backend menu DB.
    
- Validate item belongs to the same business.
    
- Recompute prices from backend DB.
    
- Validate availability before saving an order.
    
- Create tickets when `ticket_detected = true`.
    
- Create human escalations when `escalation_requested = true`.
    
- Prompt for feedback when `feedback_requested = true`.
    

---

# 10. AI Responsibilities

The AI must:

- Cache/index KB by `business_id`.
    
- Use only the correct business KB during chat.
    
- Maintain temporary conversation history by `session_id`.
    
- Expire inactive session history after around 2 hours of inactivity.
    
- Understand the latest message using conversation history.
    
- Return natural language `reply`.
    
- Return all required signal flags.
    
- Return `order_finalized = true` only after explicit customer confirmation.
    
- Ensure `order_finalized = true` always implies `order_detected = true`.
    
- Return canonical menu item names exactly as synced.
    
- Avoid offering unavailable items when `is_available = false`.
    
- Not create orders, tickets, escalations, or feedback records internally.
    
- Return `ticket_detected` and `escalation_requested` separately when appropriate.
    
- Allow both `ticket_detected` and `escalation_requested` to be true in the same response when the conversation requires both.
    

---

# 11. Final Contract Rules

- The KB sync endpoint is `POST /api/v1/business/knowledge-base/sync`.
    
- The chat endpoint is `POST /api/v1/chat`.
    
- KB sync is business-based, not session-based.
    
- Each KB sync replaces the full KB for that business.
    
- Chat requests send only the latest customer message, not the full history.
    
- The AI stores temporary conversation history by `session_id`.
    
- The backend stores permanent conversation history in DB.
    
- A closed `session_id` must not be reused for a new interaction.
    
- AI session memory may expire after 2 hours of inactivity.
    
- `order_finalized = true` always implies `order_detected = true`.
    
- The backend creates orders only when `order_finalized = true`.
    
- `ticket_detected` and `escalation_requested` can both be true in the same response.
    
- The backend may create both a Complaint Ticket and a HumanEscalation ticket when both flags are true.
    
- The AI must never create backend business records directly.
    
- The backend remains the single source of truth for persistence.