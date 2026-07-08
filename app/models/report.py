"""Report generation contract models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel



SeverityLevel = Literal["low", "medium", "high", "critical"]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _strip_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class ReportPeriod(ContractModel):
    from_: datetime = Field(..., alias="from")
    to: datetime


class SentimentDistribution(ContractModel):
    positive: int = Field(..., ge=0)
    neutral: int = Field(..., ge=0)
    negative: int = Field(..., ge=0)


class ReportTicketSummary(ContractModel):
    subject: str | None = None
    status: str | None = None
    priority: str | None = None
    created_at: str | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ReportNameCount(ContractModel):
    name: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_type_as_name(cls, value):
        """Accept {"type": "..."} as an alias for {"name": "..."} so that
        backend payloads using either key are normalised before validation."""
        if isinstance(value, dict) and "name" not in value and "type" in value:
            value = {**value, "name": value["type"]}
        return value


class ReportTopItem(ContractModel):
    name: str
    quantity_sold: int | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ReportMenuItem(ContractModel):
    name: str
    description: str | None = None
    price: float | None = Field(default=None, ge=0)
    category: str | None = None
    is_available: bool | None = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ReportFaq(ContractModel):
    question: str
    answer: str

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ReportMetrics(ContractModel):
    # --- existing required fields (unchanged) ---
    total_sessions: int = Field(..., ge=0)
    analyzed_sessions: int = Field(..., ge=0)
    average_sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    sentiment_distribution: SentimentDistribution
    total_complaints: int = Field(..., ge=0)
    total_human_agent_requests: int = Field(..., ge=0)
    total_orders_detected: int = Field(..., ge=0)

    # --- new optional extended metrics ---
    orders_today: int | None = Field(default=None, ge=0)
    orders_in_period: int | None = Field(default=None, ge=0)
    orders_this_week: int | None = Field(default=None, ge=0)

    open_tickets_count: int | None = Field(default=None, ge=0)
    escalated_tickets_count: int | None = Field(default=None, ge=0)
    tickets_this_week: int | None = Field(default=None, ge=0)

    recent_open_tickets: list[ReportTicketSummary] = Field(default_factory=list)
    most_common_ticket_types: list[ReportNameCount] = Field(default_factory=list)
    top_ordered_items: list[ReportTopItem] = Field(default_factory=list)

    menu_items_count: int | None = Field(default=None, ge=0)
    menu_items_list: list[ReportMenuItem] = Field(default_factory=list)

    faq_count: int | None = Field(default=None, ge=0)
    faq_list: list[ReportFaq] = Field(default_factory=list)

    # Override to ignore unknown future metric fields without loosening the
    # top-level contract (ReportGenerationRequest still uses extra="forbid").
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _analyzed_not_more_than_total(self):
        if self.analyzed_sessions > self.total_sessions:
            raise ValueError("analyzedSessions cannot be greater than totalSessions")
        return self


class ReportIntentCount(ContractModel):
    name: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)


class ReportTopicCount(ContractModel):
    name: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)


class ReportCommonIssue(ContractModel):
    issue: str = Field(..., min_length=1)
    count: int = Field(..., ge=0)
    examples: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("examples")
    @classmethod
    def _drop_empty_examples(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ReportSampleSummary(ContractModel):
    session_id: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    summary_ar: str = Field(..., min_length=1)
    main_intent: str = Field(..., min_length=1)
    sentiment_label: str = Field(..., min_length=1)
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)


class ReportGenerationRequest(ContractModel):
    business_id: str = Field(..., min_length=1)
    business_name: str = Field(..., min_length=1)
    period: ReportPeriod
    metrics: ReportMetrics
    top_intents: list[ReportIntentCount] = Field(..., max_length=10)
    top_topics: list[ReportTopicCount] = Field(..., max_length=10)
    common_issues: list[ReportCommonIssue] = Field(..., max_length=10)
    recent_key_moments: list[str] = Field(..., max_length=10)
    sample_summaries: list[ReportSampleSummary] = Field(..., max_length=10)

    @field_validator("recent_key_moments")
    @classmethod
    def _drop_empty_key_moments(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ReportProblem(ContractModel):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    severity: SeverityLevel
    evidence: list[str] = Field(...)

    @field_validator("evidence")
    @classmethod
    def _drop_empty_evidence(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ReportRecommendation(ContractModel):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    priority: SeverityLevel
    expected_impact: str = Field(..., min_length=1)
    suggested_owner: str = Field(..., min_length=1)


class ReportGenerationResponse(ContractModel):
    business_id: str = Field(..., min_length=1)
    period: ReportPeriod
    report_title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    summary_ar: str = Field(..., min_length=1)
    highlights: list[str] = Field(...)
    highlights_ar: list[str] = Field(...)
    problems: list[ReportProblem] = Field(...)
    recommendations: list[ReportRecommendation] = Field(...)
    suggested_actions: list[str] = Field(...)
    risk_level: SeverityLevel

    @field_validator("highlights", "highlights_ar", "suggested_actions")
    @classmethod
    def _drop_empty_strings(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]
