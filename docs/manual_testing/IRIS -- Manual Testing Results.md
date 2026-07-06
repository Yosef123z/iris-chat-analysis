## Phase 1 -- تشغيل السيرفر والـ health

### Tests

#### 1.1

```
GET http://localhost:8000/health
```

#### 1.2

```
GET http://localhost:8000/health/integration
```

#### 1.3

```
GET http://localhost:8000/metrics
```

### Results

#### 1.1 Result

```
{
- "status": "healthy",
- "timestamp": "2026-06-03T06:38:51.388696+00:00",
- "version": "2.0.0",
- "persistence": "in_memory_only",
- ["components"](http://localhost:8000/health): {
    - ["business_knowledge_store"](http://localhost:8000/health): {
        - "status": "up"},
    - ["session_memory"](http://localhost:8000/health): {
        - "status": "up"},
    - ["analysis"](http://localhost:8000/health): {
        - "status": "up"},
    - ["pii"](http://localhost:8000/health): {
        - "status": "up"}}
}
```

#### 1.2 Result

```
{
- "status": "ready",
- "mode": "signal_based_contract",
- "persistence": "in_memory_only",
- "backend_record_creation": "backend_owned",
- ["routes"](http://localhost:8000/health/integration): {
    - "chat": true,
    - "businessKnowledgeSync": true,
    - "analysisChatBatch": true,
    - "piiRemove": true},
- ["sideEffects"](http://localhost:8000/health/integration): {
    - "createsOrders": false,
    - "createsTickets": false,
    - "createsEscalations": false,
    - "storesFeedback": false,
    - "storesAnalysis": false}
}
```

#### 1.3 Result

```
{
- "runtime": "in_memory_only",
- "contract_mode": "signal_based",
- "version": "2.0.0"
}
```

---

## Phase 2 -- OpenAPI surface check

```
Everything is Good Here.
```

---

## Phase 3 -- KB Sync testing

### Tests

```
POST /api/v1/business/knowledge-base/sync
```

### Result

```
{ "status": "ok" } --> For All Sync Tests: 3.1 (Sync restaurant KB), 3.2 (Sync cafe KB), 3.3 (3.3 Sync clinic/non-restaurant KB)
```

---

## Phase 4 -- Chat - missing KB

### Tests

```
POST /api/v1/chat
```

```json
{
  "session_id": "manual-missing-kb-001",
  "business_id": "unknown-business",
  "message": "عندكم ايه؟"
}
```

### Results

```json
{
  "session_id": "manual-missing-kb-001",
  "reply": "معلش يا فندم، بيانات النشاط ده لسه مش متاحة عندي. من فضلك ابعت بيانات النشاط الأول وبعدها أقدر أساعد حضرتك.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 0
}
```

---

## Phase 5 -- Chat: restaurant KB quality

### Tests

#### 5.1 -- General product question

```json
{
  "session_id": "manual-rest-001",
  "business_id": "biz-restaurant-demo",
  "message": "عندكم ايه متاح؟"
}
```

#### 5.2 -- Price question

```json
{
  "session_id": "manual-rest-002",
  "business_id": "biz-restaurant-demo",
  "message": "Classic Burger بكام؟"
}
```

#### 5.3 -- Unknown info

```json
{
  "session_id": "manual-rest-003",
  "business_id": "biz-restaurant-demo",
  "message": "عندكم فروع في اسكندرية؟"
}
```

### Results

#### 5.1 (General product question) Result

```json
{
  "session_id": "manual-rest-001",
  "reply": "عندنا متاح \"Lemon Mint\" بسعر 45.0 و \"Classic Burger\" بسعر 120.0. تحب تطلب حاجة منهم؟",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 5450
}
```

#### 5.2 (Price question) Result

```json
{
  "session_id": "manual-rest-002",
  "reply": "Classic Burger بسعر 120.0.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 2386
}
```

#### 5.3 (Unknown info) Result

```json
{
  "session_id": "manual-rest-003",
  "reply": "معلش، مش متاح دلوقتي معلومات عن الفروع في اسكندرية. لو عايز تسأل عن حاجة تانية، أنا هنا.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 5487
}
```

---

## Phase 6 -- Chat - order/cart flow

### Tests

#### 6.1 -- Add available item

```json
{
  "session_id": "manual-order-001",
  "business_id": "biz-restaurant-demo",
  "message": "عايز Classic Burger"
}
```

#### 6.2 -- Confirm order

```json
{
  "session_id": "manual-order-001",
  "business_id": "biz-restaurant-demo",
  "message": " كدة تمام أكد الطلب "
}
```

### Additional Scenarios

#### 6.3 -- Update Order

```json
{
  "session_id": "manual-order-001",
  "business_id": "biz-restaurant-demo",
  "message": "ممكن معلش تضيف كمان ليمون بالنعناع للاوردر و بس كدة"
}
```

#### 6.4 -- Cancel Order

```json
{
  "session_id": "manual-order-001",
  "business_id": "biz-restaurant-demo",
  "message": "بقولك ايه خلاص عايز الغى الاوردر معلش"
}
```

### Results

#### 6.1 (Add available item) Result

```json
{
  "session_id": "manual-order-001",
  "reply": "تحب أطلب لك Classic Burger بسعر 120.0؟",
  "order_detected": true,
  "order_finalized": false,
  "order_details": {
    "intent": "CreateOrder",
    "items": [
      { "name": "Classic Burger", "quantity": 1, "price": 120, "notes": null }
    ],
    "total_amount": 120
  },
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3669
}
```

#### 6.2 (Confirm order) Result

```json
{
  "session_id": "manual-order-001",
  "reply": "تمام يا فندم، كده الطلب اتأكد. هنبدأ نجهزه لحضرتك.",
  "order_detected": true,
  "order_finalized": true,
  "order_details": {
    "intent": "CreateOrder",
    "items": [
      { "name": "Classic Burger", "quantity": 1, "price": 120, "notes": null }
    ],
    "total_amount": 120
  },
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3357
}
```

### Additional Scenarios

#### 6.3 (Update Order) Result

```json
{
  "session_id": "manual-order-001",
  "reply": "تمام يا فندم، كده الطلب اتأكد. هنبدأ نجهزه لحضرتك.",
  "order_detected": true,
  "order_finalized": true,
  "order_details": {
    "intent": "ModifyOrder",
    "items": [
      { "name": "Classic Burger", "quantity": 1, "price": 120, "notes": null },
      { "name": "Lemon Mint", "quantity": 1, "price": 45, "notes": null }
    ],
    "total_amount": 165
  },
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3235
}
```

#### 6.4 (Cancel Order) Result

```json
{
  "session_id": "manual-order-001",
  "reply": "تمام يا فندم، لغيتلك الطلب. لو احتجت أي حاجة تانية أنا معاك.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 2643
}
```

---

## Phase 7 -- Unavailable item

### Tests

```json
{
  "session_id": "manual-unavailable-001",
  "business_id": "biz-restaurant-demo",
  "message": "عايز Crispy Chicken Burger"
}
```

### Results

```json
{
  "session_id": "manual-unavailable-001",
  "reply": "معلش يا فندم، Crispy Chicken Burger مش متاح دلوقتي. المتاح بدلًا منه: Classic Burger بسعر 120 جنيه.",
  "order_detected": true,
  "order_finalized": false,
  "order_details": { "intent": "CreateOrder", "items": [], "total_amount": 0 },
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 2501
}
```

---

## Phase 8 -- Complaint / escalation signals

### Tests

#### 8.1 -- Complaint only

```json
{
  "session_id": "manual-ticket-001",
  "business_id": "biz-restaurant-demo",
  "message": "الأوردر وصل بارد"
}
```

#### 8.2 -- Escalation only

```json
{
  "session_id": "manual-escalation-001",
  "business_id": "biz-restaurant-demo",
  "message": "عايز أكلم المدير"
}
```

#### 8.3 -- Complaint + escalation

```json
{
  "session_id": "manual-ticket-escalation-001",
  "business_id": "biz-restaurant-demo",
  "message": "الأوردر وصل غلط وعايز أكلم المدير حالًا"
}
```

### Results

#### 8.1 (Complaint only) Result

```json
{
  "session_id": "manual-ticket-001",
  "reply": "معلش يا فندم، هسجل المشكلة لفريق الدعم عشان يتابعوها.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": true,
  "ticket_details": {
    "subject": "الأوردر وصل بارد",
    "description": "الأوردر وصل بارد",
    "priority": "normal",
    "category": "quality"
  },
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3990
}
```

#### 8.2 (Escalation only) Result

```json
{
  "session_id": "manual-escalation-001",
  "reply": "تمام يا فندم، هحوّل حضرتك لحد من الإدارة يتابع معاك.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": true,
  "feedback_requested": false,
  "processing_time_ms": 5123
}
```

#### 8.3 (Complaint + escalation) Result

```json
{
  "session_id": "manual-ticket-escalation-001",
  "reply": "معلش جدًا يا فندم على اللي حصل، هسجل المشكلة فورًا وهحوّل حضرتك لحد من الإدارة يتابع معاك.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": true,
  "ticket_details": {
    "subject": "الأوردر وصل غلط",
    "description": "الأوردر وصل غلط وعايز أكلم المدير حالًا",
    "priority": "high",
    "category": "wrong_order"
  },
  "escalation_requested": true,
  "feedback_requested": false,
  "processing_time_ms": 3072
}
```

---

## Phase 9 -- Business isolation

### Tests

#### 9.1 -- Cafe test

```json
{
  "session_id": "manual-cafe-001",
  "business_id": "biz-cafe-demo",
  "message": "عندكم ايه متاح؟"
}
```

#### 9.2 -- Clinic/non-restaurant test

```json
{
  "session_id": "manual-clinic-001",
  "business_id": "biz-clinic-demo",
  "message": "قولي تفاصيل Dental Cleaning"
}
```

### Results

#### 9.1 (Cafe test) Result

```json
{
  "session_id": "manual-cafe-001",
  "reply": "عندنا متاح \"Honey Cake\" بسعر 95 جنيه و\"Iced Latte\" بسعر 85 جنيه. تحب تطلب حاجة منهم؟",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3057
}
```

#### 9.2 (Clinic/non-restaurant test) Result

```json
{
  "session_id": "manual-clinic-001",
  "reply": "Dental Cleaning هو خدمة تنظيف الأسنان الاحترافية. سعرها 500.0. تحب أطلبها لك؟",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 2392
}
```

---

## Phase 10 -- Analysis chat-batch

### Tests

```
POST /api/v1/analysis/chat-batch
```

```json
{
  "businessId": "biz-restaurant-demo",
  "sessions": [
    {
      "sessionId": "manual-analysis-001",
      "messages": [
        {
          "role": "customer",
          "text": "اسمي يوسف ورقمي 01012345678 وعايز Classic Burger"
        },
        {
          "role": "assistant",
          "text": "تمام يا فندم، ضفت Classic Burger."
        },
        {
          "role": "customer",
          "text": "الأوردر وصل بارد وعايز أكلم المدير"
        }
      ]
    }
  ]
}
```

### Result

```json
{
  "businessId": "biz-restaurant-demo",
  "results": [
    {
      "sessionId": "manual-analysis-001",
      "summary": "Customer ordered a Classic Burger but received it cold and requested to speak to the manager.",
      "summaryAr": "العميل طلب Classic Burger لكن الأوردر وصل بارد وعايز يكلم المدير.",
      "overallSentiment": { "score": -0.8, "label": "Negative" },
      "mainIntent": "Complaint",
      "intentsDetected": [
        { "name": "Complaint", "count": 3 },
        { "name": "RequestHumanAgent", "count": 1 }
      ],
      "mainTopics": ["order issue", "cold food", "manager request"],
      "keyMoments": [
        "Customer received cold burger",
        "Customer requested to speak to manager"
      ]
    }
  ]
}
```

---

## Phase 11 -- Restart memory/index behavior

### Tests (After Restarting Server)

```json
{
  "session_id": "manual-after-restart-001",
  "business_id": "biz-restaurant-demo",
  "message": "عندكم ايه؟"
}
```

### Result

#### Before Sync

```json
{
  "session_id": "manual-after-restart-001",
  "reply": "معلش يا فندم، بيانات النشاط ده لسه مش متاحة عندي. من فضلك ابعت بيانات النشاط الأول وبعدها أقدر أساعد حضرتك.",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 0
}
```

#### After Sync

```json
{
  "session_id": "manual-after-restart-001",
  "reply": "عندنا مشروبات زي Lemon Mint وسندوتشات زي Classic Burger. تحب تطلب حاجة منهم؟",
  "order_detected": false,
  "order_finalized": false,
  "order_details": null,
  "ticket_detected": false,
  "ticket_details": null,
  "escalation_requested": false,
  "feedback_requested": false,
  "processing_time_ms": 3613
}
```

---

## Reports Feature Test

### Test

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
  "topIntents": [
    {
      "name": "CreateOrder",
      "count": 60
    },
    {
      "name": "Complaint",
      "count": 22
    },
    {
      "name": "AskAboutProducts",
      "count": 18
    }
  ],
  "topTopics": [
    {
      "name": "delivery",
      "count": 18
    },
    {
      "name": "cold food",
      "count": 12
    },
    {
      "name": "manager request",
      "count": 8
    }
  ],
  "commonIssues": [
    {
      "issue": "Orders arriving cold",
      "count": 12,
      "examples": [
        "Customer received cold burger",
        "Customer complained that the order arrived cold"
      ]
    },
    {
      "issue": "Wrong order",
      "count": 7,
      "examples": ["Customer said the order was wrong"]
    }
  ],
  "recentKeyMoments": [
    "Customer received cold burger",
    "Customer requested to speak to manager",
    "Customer complained about wrong order"
  ],
  "sampleSummaries": [
    {
      "sessionId": "session-001",
      "summary": "Customer ordered a Classic Burger but received it cold and requested the manager.",
      "summaryAr": "العميل طلب Classic Burger لكن الأوردر وصل بارد وطلب يكلم المدير.",
      "mainIntent": "Complaint",
      "sentimentLabel": "Negative",
      "sentimentScore": -0.8
    },
    {
      "sessionId": "session-002",
      "summary": "Customer asked about delivery time and completed an order.",
      "summaryAr": "العميل سأل عن وقت التوصيل وكمل طلب.",
      "mainIntent": "CreateOrder",
      "sentimentLabel": "Neutral",
      "sentimentScore": 0.0
    }
  ]
}
```

### Result

```json
{
  "businessId": "biz-restaurant-demo",
  "period": { "from": "2026-06-01T00:00:00Z", "to": "2026-06-30T23:59:59Z" },
  "reportTitle": "Customer Interaction Analysis Report",
  "summary": "The analysis indicates a slight negative sentiment with notable complaints about cold food and wrong orders. Overall, customer interactions show areas for improvement.",
  "summaryAr": "التحليل بيظهر شعور سلبي بسيط مع شكاوى ملحوظة عن الأكل البارد والأوردرات الغلط. بشكل عام، تفاعلات العملاء بتوضح مجالات للتحسين.",
  "highlights": [
    "Average sentiment score is -0.12, indicating slight negativity.",
    "22 total complaints were recorded, with 12 related to cold food.",
    "7 complaints were about wrong orders."
  ],
  "highlightsAr": [
    "متوسط درجة الشعور -0.12، مما يدل على سلبية بسيطة.",
    "تم تسجيل 22 شكوى إجمالية، منها 12 تتعلق بالأكل البارد.",
    "7 شكاوى كانت عن الأوردرات الغلط."
  ],
  "problems": [
    {
      "title": "Cold Food Complaints",
      "description": "12 complaints were received regarding orders arriving cold, impacting customer satisfaction.",
      "severity": "high",
      "evidence": [
        "12 complaints about cold food, including examples like 'Customer received cold burger'."
      ]
    },
    {
      "title": "Wrong Orders",
      "description": "7 complaints were reported about wrong orders, which can lead to customer frustration.",
      "severity": "medium",
      "evidence": [
        "7 complaints about wrong orders, with examples like 'Customer said the order was wrong'."
      ]
    }
  ],
  "recommendations": [
    {
      "title": "Improve Food Delivery Temperature",
      "description": "Focus on ensuring that food is delivered at the right temperature to reduce complaints about cold food.",
      "priority": "high",
      "expectedImpact": "This could help in reducing complaints and improving overall customer satisfaction.",
      "suggestedOwner": "Operations Manager"
    },
    {
      "title": "Enhance Order Accuracy",
      "description": "Implement a double-check system for orders to minimize the occurrence of wrong orders.",
      "priority": "medium",
      "expectedImpact": "This could lead to fewer complaints and a better customer experience.",
      "suggestedOwner": "Quality Control Manager"
    }
  ],
  "suggestedActions": [
    "Review delivery processes to ensure food is kept warm.",
    "Train staff on order accuracy and verification.",
    "Monitor customer feedback closely to identify trends."
  ],
  "riskLevel": "medium"
}
```
