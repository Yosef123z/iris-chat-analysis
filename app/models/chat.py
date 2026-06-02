"""Chat contract request and response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    business_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=2000)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "interaction-123",
                "business_id": "biz-restaurant-1",
                "message": "عايز Classic Burger",
            }
        }
    )


class OrderLineItem(BaseModel):
    name: str = Field(..., min_length=1)
    quantity: int = Field(..., ge=1)
    price: float = Field(..., ge=0)
    notes: str | None = None


class OrderDetails(BaseModel):
    intent: str | None = "CreateOrder"
    items: list[OrderLineItem] = Field(default_factory=list)
    total_amount: float = Field(..., ge=0)


class TicketDetails(BaseModel):
    subject: str = Field(..., min_length=1)
    description: str | None = None
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    category: str | None = "other"


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    order_detected: bool = False
    order_finalized: bool = False
    order_details: OrderDetails | None = None
    ticket_detected: bool = False
    ticket_details: TicketDetails | None = None
    escalation_requested: bool = False
    feedback_requested: bool = False
    processing_time_ms: int | None = None

    @model_validator(mode="after")
    def _finalization_implies_detection(self):
        if self.order_finalized:
            self.order_detected = True
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "interaction-123",
                "reply": "تمام يا فندم، ضفت Classic Burger. تحب تضيف حاجة تانية؟",
                "order_detected": True,
                "order_finalized": False,
                "order_details": {
                    "intent": "CreateOrder",
                    "items": [
                        {
                            "name": "Classic Burger",
                            "quantity": 1,
                            "price": 49.99,
                            "notes": None,
                        }
                    ],
                    "total_amount": 49.99,
                },
                "ticket_detected": False,
                "ticket_details": None,
                "escalation_requested": False,
                "feedback_requested": False,
                "processing_time_ms": 120,
            }
        }
    )
