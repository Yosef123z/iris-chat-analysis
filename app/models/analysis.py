"""Analysis and PII contract models."""

from typing import Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class PIIRemoveRequest(ContractModel):
    text: str = Field(..., min_length=1)


class PIIRemoveResult(ContractModel):
    original_text: str
    clean_text: str
    redactions: Dict[str, int] = Field(default_factory=dict)


class AnalysisMessage(ContractModel):
    role: Literal["customer", "assistant"]
    text: str


class AnalysisSession(ContractModel):
    session_id: str = Field(..., min_length=1)
    messages: list[AnalysisMessage] = Field(..., min_length=1)


class ChatBatchAnalysisRequest(ContractModel):
    business_id: str = Field(..., min_length=1)
    sessions: list[AnalysisSession] = Field(..., min_length=1, max_length=1)

    @model_validator(mode="after")
    def _v1_single_session_only(self):
        if len(self.sessions) != 1:
            raise ValueError("chat-batch supports exactly one session per request in v1")
        return self


class SentimentResult(ContractModel):
    score: float = Field(..., ge=-1.0, le=1.0)
    label: Literal["Positive", "Neutral", "Negative"]


class IntentCount(ContractModel):
    name: Literal[
        "CreateOrder",
        "ModifyOrder",
        "CancelOrder",
        "AskAboutProducts",
        "AskAboutPrice",
        "Complaint",
        "RequestHumanAgent",
        "Compliment",
        "Greeting",
        "Farewell",
        "GeneralQuestion",
        "Unknown",
    ]
    count: int = Field(..., ge=1)


class ChatAnalysisResult(ContractModel):
    session_id: str
    summary: str
    summary_ar: str
    overall_sentiment: SentimentResult
    main_intent: str
    intents_detected: list[IntentCount] = Field(..., min_length=1)
    main_topics: list[str] = Field(default_factory=list)
    key_moments: list[str] = Field(default_factory=list)

    @field_validator("intents_detected")
    @classmethod
    def _sort_intents(cls, value: list[IntentCount]) -> list[IntentCount]:
        return sorted(value, key=lambda item: item.count, reverse=True)

    @model_validator(mode="after")
    def _main_intent_matches_first_intent(self):
        self.main_intent = self.intents_detected[0].name
        return self


class ChatBatchAnalysisResponse(ContractModel):
    business_id: str
    results: list[ChatAnalysisResult] = Field(..., min_length=1, max_length=1)
