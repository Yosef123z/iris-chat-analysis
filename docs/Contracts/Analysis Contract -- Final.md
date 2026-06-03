# Analysis Contract — Final (Backend ↔ AI)

> Last updated after full contract review and AI team clarifications.  
> This file is the **single source of truth** for the Analysis integration.  
> Ignore all previous analysis contract files.

---

## Overview

Post-session analysis runs **after** a conversation ends.

The backend sends the full message history to the AI, and the AI returns:

- Sentiment analysis
    
- Intent analysis
    
- Topic extraction
    
- Key moments
    
- Conversation summaries
    

Results are stored in the backend database and surfaced on the business dashboard.

**Trigger:** Automatically called by the backend when `EndInteraction` is invoked.  
**Mode:** Fire-and-forget background task. It does not block the customer-facing flow.

---

## Endpoint

**Direction:** Backend → AI

```http
POST /api/v1/analysis/chat-batch
```

---

## Request Body

```json
{
  "businessId": "string",
  "sessions": [
    {
      "sessionId": "string",
      "messages": [
        {
          "role": "customer",
          "text": "عايز أطلب بيتزا"
        },
        {
          "role": "assistant",
          "text": "تمام، أي نوع بيتزا تفضل؟"
        }
      ]
    }
  ]
}
```

---

## Request Field Notes

|Field|Notes|
|---|---|
|`businessId`|camelCase. Matches the analysis response field name.|
|`sessions[].sessionId`|Maps to `Interaction.InteractionId` in the backend database.|
|`messages[].role`|Must be exactly `"customer"` or `"assistant"`. No other values are supported.|
|`messages[].text`|Non-empty string. Backend filters out blank messages before sending.|

---

## Request Rules

- In v1, the backend sends exactly **1 session per request**.
    
- The endpoint name is `chat-batch` to remain future-ready, but the officially supported batch size in the current version is `1`.
    
- Messages must be sent in chronological order, oldest first.
    
- Empty or whitespace-only messages are excluded before sending.
    
- If all messages are empty after filtering, the backend must not send the request.
    
- If a session contains at least one non-empty message, the AI must return a valid analysis response.
    

---

## Response Body

```json
{
  "businessId": "string",
  "results": [
    {
      "sessionId": "string",

      "summary": "string",
      "summaryAr": "string",

      "overallSentiment": {
        "score": 0.75,
        "label": "Positive"
      },

      "mainIntent": "CreateOrder",

      "intentsDetected": [
        {
          "name": "CreateOrder",
          "count": 3
        },
        {
          "name": "AskAboutProducts",
          "count": 1
        }
      ],

      "mainTopics": [
        "بيتزا",
        "توصيل"
      ],

      "keyMoments": [
        "العميل طلب بيتزا مارجريتا",
        "تأكيد الطلب بنجاح"
      ]
    }
  ]
}
```

---

## Response Naming Convention

All response fields must use **camelCase**.

Correct field names:

```json
{
  "businessId": "string",
  "sessionId": "string",
  "summaryAr": "string",
  "overallSentiment": {},
  "mainIntent": "CreateOrder",
  "intentsDetected": [],
  "mainTopics": [],
  "keyMoments": []
}
```

Important:

- The correct field name is `mainTopics`.
    
- The AI must not return `MainTopics`.
    
- The backend deserialization depends on exact camelCase field names.
    

---

## Response Field Details

### Root Fields

|Field|Type|Required|Notes|
|---|---|---|---|
|`businessId`|string|Yes|Business identifier. Must match the request `businessId`.|
|`results`|array|Yes|Analysis result per session. In v1, this array contains exactly one result.|

---

### Session Result Fields

|Field|Type|Required|Notes|
|---|---|---|---|
|`sessionId`|string|Yes|Session identifier. Must match request `sessions[].sessionId`.|
|`summary`|string|Yes|English summary of the full conversation.|
|`summaryAr`|string|Yes|Arabic summary of the full conversation.|
|`overallSentiment`|object|Yes|Sentiment object.|
|`mainIntent`|string|Yes|Dominant session intent. Always equals `intentsDetected[0].name`.|
|`intentsDetected`|array|Yes|Intent distribution sorted by `count` descending.|
|`mainTopics`|string[]|Yes|Main topics discussed in the session. Can be empty.|
|`keyMoments`|string[]|Yes|Important conversation moments. Can be empty.|

---

## Summaries

|Field|Notes|
|---|---|
|`summary`|English summary of the full conversation.|
|`summaryAr`|Arabic summary of the full conversation.|

### Summary Length Guidance

The backend stores summaries in `nvarchar(max)`, so there is no strict database limitation.

However, for dashboard readability, the AI should keep summaries concise:

```text
summary: around 500 characters maximum
summaryAr: around 500 characters maximum
```

The summary should usually be 1 to 3 short sentences.

For long conversations, the summary should still be concise and should not become a transcript.

---

## Sentiment

```json
{
  "score": 0.75,
  "label": "Positive"
}
```

|Field|Type|Notes|
|---|---|---|
|`overallSentiment.score`|double|Range: `-1.0` to `+1.0`.|
|`overallSentiment.label`|string|Must be exactly `"Positive"`, `"Neutral"`, or `"Negative"`.|

### Sentiment Score Range

```text
-1.0 → Very Negative
 0.0 → Neutral
+1.0 → Very Positive
```

### Sentiment Fallback

If sentiment cannot be confidently determined, the AI must return:

```json
{
  "score": 0.0,
  "label": "Neutral"
}
```

---

## Intents

### mainIntent

`mainIntent` is the single dominant intent for the session.

It must always equal:

```text
intentsDetected[0].name
```

The AI must not return `mainIntent = null`.

If the dominant intent cannot be determined, use:

```json
{
  "mainIntent": "Unknown",
  "intentsDetected": [
    {
      "name": "Unknown",
      "count": 1
    }
  ]
}
```

---

### intentsDetected

`intentsDetected` is the full distribution of intents detected across the session.

It must be sorted by `count` descending.

```json
[
  {
    "name": "CreateOrder",
    "count": 3
  },
  {
    "name": "AskAboutProducts",
    "count": 1
  }
]
```

|Field|Notes|
|---|---|
|`intentsDetected[].name`|Must be one of the supported intent values.|
|`intentsDetected[].count`|Number of times this intent appeared in the session.|

### Intent Fallback

If intent cannot be confidently determined, the AI must return:

```json
{
  "mainIntent": "Unknown",
  "intentsDetected": [
    {
      "name": "Unknown",
      "count": 1
    }
  ]
}
```

---

## Supported Intent Values

```text
CreateOrder
ModifyOrder
CancelOrder
AskAboutProducts
AskAboutPrice
Complaint
RequestHumanAgent
Compliment
Greeting
Farewell
GeneralQuestion
Unknown
```

---

## Topics

`mainTopics` contains the main topics discussed in the session.

```json
{
  "mainTopics": [
    "بيتزا",
    "توصيل"
  ]
}
```

Rules:

- Must be an array of strings.
    
- Can be an empty array.
    
- Use concise topic names.
    
- Do not include long sentences.
    
- Field name must be exactly `mainTopics`.
    

If no meaningful topics are detected, return:

```json
{
  "mainTopics": []
}
```

---

## Key Moments

`keyMoments` contains the most important moments in the conversation.

```json
{
  "keyMoments": [
    "العميل طلب بيتزا مارجريتا",
    "تأكيد الطلب بنجاح"
  ]
}
```

Rules:

- Must be an array of human-readable strings.
    
- Can be an empty array.
    
- Should include important moments such as:
    
    - Customer placed an order
        
    - Customer confirmed an order
        
    - Customer complained
        
    - Customer requested a human agent
        
    - Customer gave positive or negative feedback
        

If no important moments are detected, return:

```json
{
  "keyMoments": []
}
```

Frontend should handle empty arrays gracefully, for example by showing:

```text
No key moments detected
```

---

## Single-Message Sessions

If a session has only one non-empty message, the AI must still return a valid analysis response.

The AI must not return an error only because the conversation is short.

Example single-message session:

```json
{
  "businessId": "biz-1",
  "sessions": [
    {
      "sessionId": "session-001",
      "messages": [
        {
          "role": "customer",
          "text": "أهلا"
        }
      ]
    }
  ]
}
```

Expected valid response example:

```json
{
  "businessId": "biz-1",
  "results": [
    {
      "sessionId": "session-001",
      "summary": "Customer greeted the assistant but did not continue the conversation.",
      "summaryAr": "العميل ألقى التحية ولم يكمل المحادثة.",
      "overallSentiment": {
        "score": 0.0,
        "label": "Neutral"
      },
      "mainIntent": "Greeting",
      "intentsDetected": [
        {
          "name": "Greeting",
          "count": 1
        }
      ],
      "mainTopics": [],
      "keyMoments": []
    }
  ]
}
```

If the single message is unclear, use the standard fallback:

```json
{
  "mainIntent": "Unknown",
  "intentsDetected": [
    {
      "name": "Unknown",
      "count": 1
    }
  ],
  "overallSentiment": {
    "score": 0.0,
    "label": "Neutral"
  },
  "mainTopics": [],
  "keyMoments": []
}
```

---

## What the Backend Does with the Response

|AI Field|Backend Storage|
|---|---|
|`summary`|`InteractionAnalysis.Summary`|
|`summaryAr`|`InteractionAnalysis.SummaryAr`|
|`overallSentiment.score`|`InteractionAnalysis.SentimentScore`|
|`overallSentiment.label`|`InteractionAnalysis.SentimentLabel`|
|`mainIntent`|`InteractionAnalysis.MainIntent`|
|`intentsDetected`|`InteractionAnalysis.IntentsDetectedJson` serialized JSON array|
|`mainTopics`|`InteractionAnalysis.MainTopicsJson` serialized JSON array|
|`keyMoments`|`InteractionAnalysis.KeyMomentsJson` serialized JSON array|

---

## Dashboard Usage

The stored analysis drives these dashboard sections:

|Dashboard Section|Uses|
|---|---|
|Sentiment Analysis|`SentimentLabel`, `SentimentScore`|
|Chat Analysis → Top Intents|`IntentsDetectedJson`|
|Chat Analysis → Top Topics|`MainTopicsJson`|
|Chat Analysis → Top Key Moments|`KeyMomentsJson`|
|Chat Analysis → Recent Sessions|`Summary`, `SummaryAr`, `MainIntent`, `SentimentLabel`, `SentimentScore`|

---

## Idempotency

The backend skips storing if an analysis for the same `sessionId` already exists.

Duplicate triggers, retries, or repeated `EndInteraction` calls are safe.

The AI does not need to handle idempotency internally.

The backend is responsible for preventing duplicate analysis storage.

---

## Error and Fallback Behavior

The AI should avoid failing the full request when the session contains at least one valid non-empty message.

If analysis is uncertain, return fallback values instead of an error.

### Standard Fallback Response Values

```json
{
  "overallSentiment": {
    "score": 0.0,
    "label": "Neutral"
  },
  "mainIntent": "Unknown",
  "intentsDetected": [
    {
      "name": "Unknown",
      "count": 1
    }
  ],
  "mainTopics": [],
  "keyMoments": []
}
```

The backend should not send requests where all messages are empty or whitespace.

---

## Final Contract Rules

- The endpoint is `POST /api/v1/analysis/chat-batch`.
    
- In v1, batch size is exactly 1 session per request.
    
- All request and response fields use camelCase.
    
- `mainTopics` is the correct field name.
    
- `MainTopics` is invalid.
    
- `mainIntent` must always equal `intentsDetected[0].name`.
    
- `mainIntent` must never be `null`.
    
- Use `"Unknown"` when intent is undetermined.
    
- `keyMoments` can be an empty array.
    
- `mainTopics` can be an empty array.
    
- Sessions with at least one non-empty message must receive a valid analysis response.
    
- Summaries should be concise, usually around 500 characters maximum.
    
- The AI does not persist analysis results.
    
- The backend remains the single source of truth for stored analysis.