"""Business knowledge-base sync contract models."""

from pydantic import BaseModel, ConfigDict, Field


class BusinessMenuItem(BaseModel):
    menu_item_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str | None = None
    price: float = Field(..., ge=0)
    category: str | None = None
    is_available: bool = True


class BusinessFAQ(BaseModel):
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    is_faq: bool = True


class BusinessKnowledgeBase(BaseModel):
    menu_items: list[BusinessMenuItem] = Field(default_factory=list)
    faqs: list[BusinessFAQ] = Field(default_factory=list)


class BusinessKnowledgeSyncRequest(BaseModel):
    business_id: str = Field(..., min_length=1)
    business_name: str = Field(..., min_length=1)
    knowledge_base: BusinessKnowledgeBase

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "business_id": "biz-restaurant-1",
                "business_name": "Demo Restaurant",
                "knowledge_base": {
                    "menu_items": [
                        {
                            "menu_item_id": "item-1",
                            "name": "Classic Burger",
                            "description": "Beef burger with cheese",
                            "price": 120,
                            "category": "Burgers",
                            "is_available": True,
                        }
                    ],
                    "faqs": [
                        {
                            "question": "Delivery time",
                            "answer": "Delivery usually takes 30 to 45 minutes.",
                            "is_faq": True,
                        }
                    ],
                },
            }
        }
    )


class BusinessKnowledgeSyncResponse(BaseModel):
    status: str = "ok"
