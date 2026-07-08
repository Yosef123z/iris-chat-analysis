"""
==============================================================
 Owner Chat Models — Business Owner Analytics Chatbot
==============================================================
 Pydantic models for the owner-facing analytics chatbot.
 Separate from customer chat to maintain domain boundaries.
==============================================================
"""

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


from app.models.report import ReportGenerationResponse, ReportPeriod


class OwnerChatRequest(BaseModel):
    """Request model for owner analytics questions."""
    business_id: str = Field(..., min_length=1, description="Business identifier")
    session_id: str = Field(..., description="Owner session identifier")
    message: str = Field(..., min_length=1, description="Owner's analytics question")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "business_id": "biz-restaurant-demo",
            "session_id": "owner-session-123",
            "message": "What was today's revenue?"
        }
    })


class OwnerChatResponse(BaseModel):
    """Response model for owner analytics chatbot."""
    business_id: str
    session_id: str
    reply: str = Field(..., description="AI assistant's analytics response")
    data_sources_used: List[str] = Field(
        default_factory=list,
        description="List of analytics sections referenced (e.g., 'Financial Performance', 'Menu Analysis')"
    )
    confidence: str = Field(
        default="high",
        description="Confidence level: 'high' (data-backed), 'medium' (partial data), 'low' (no data)"
    )
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "business_id": "biz-restaurant-demo",
            "session_id": "owner-session-123",
            "reply": "Today's revenue was 18,500 EGP, which is a 12% increase compared to last Saturday.",
            "data_sources_used": ["Financial Performance", "Executive Overview"],
            "confidence": "high"
        }
    })


class AnalyticsReportResponse(BaseModel):
    """Response model for retrieving the full analytics report."""
    report_content: str = Field(..., description="Full analytics report text")
    sections_available: List[str] = Field(
        default_factory=list,
        description="List of available section names"
    )
    last_updated: Optional[str] = Field(None, description="Last update timestamp")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "report_content": "# IRIS Daily Executive Summary...",
            "sections_available": [
                "Executive Overview",
                "Financial Performance",
                "Menu Analysis",
                "Operational Health",
                "Customer Sentiment",
                "Recommendations"
            ],
            "last_updated": "2026-02-15T23:59:59"
        }
    })


class ReloadResponse(BaseModel):
    """Response model for analytics reload operation."""
    status: str = Field(..., description="Reload status")
    reports_loaded: int = Field(..., description="Number of reports loaded")
    message: str = Field(..., description="Human-readable message")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "success",
            "reports_loaded": 1,
            "message": "Analytics data reloaded successfully."
        }
    })


class OwnerReportSyncRequest(BaseModel):
    """Backend-provided report payload used to ground owner chat."""

    business_id: str = Field(..., min_length=1)
    business_name: str = Field(..., min_length=1)
    period: ReportPeriod
    report: ReportGenerationResponse
    metrics: dict[str, Any] | None = Field(
        default=None,
        description="Optional raw backend metrics that ground factual owner questions.",
    )

    @model_validator(mode="after")
    def _report_matches_owner_scope(self):
        if self.report.business_id != self.business_id:
            raise ValueError("report.businessId must match business_id")
        if self.report.period != self.period:
            raise ValueError("report.period must match period")
        return self

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "business_id": "biz-restaurant-demo",
            "business_name": "Demo Restaurant",
            "period": {
                "from": "2026-06-01T00:00:00Z",
                "to": "2026-06-30T23:59:59Z",
            },
            "report": {
                "businessId": "biz-restaurant-demo",
                "period": {
                    "from": "2026-06-01T00:00:00Z",
                    "to": "2026-06-30T23:59:59Z",
                },
                "reportTitle": "Customer Experience Report",
                "summary": "Customer service was mostly neutral with delivery complaints.",
                "summaryAr": "Business-friendly Arabic summary.",
                "highlights": ["CreateOrder was the most common intent."],
                "highlightsAr": ["Arabic highlight."],
                "problems": [],
                "recommendations": [],
                "suggestedActions": ["Review delivery process this week."],
                "riskLevel": "medium",
            },
            "metrics": {
                "menuItemsList": [
                    {
                        "name": "Classic Burger",
                        "description": "Beef burger with cheese",
                        "price": 120,
                        "category": "Burgers",
                        "isAvailable": True,
                    }
                ],
                "faqList": [
                    {"question": "Delivery time", "answer": "30 to 45 minutes."}
                ],
                "ordersToday": 12,
                "openTicketsCount": 2,
            },
        }
    })



class OwnerReportSyncResponse(BaseModel):
    """Response model for owner report sync."""

    status: str = "ok"


class SynonymsReloadResponse(BaseModel):
    """Response model for synonym reload operation."""
    status: str = Field(..., description="Reload status")
    source: str = Field(..., description="Where synonyms were loaded from")
    entries_loaded: int = Field(..., description="Number of synonym entries loaded")
    message: str = Field(..., description="Human-readable message")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "status": "success",
            "source": "db",
            "entries_loaded": 42,
            "message": "Synonyms reloaded successfully."
        }
    })
