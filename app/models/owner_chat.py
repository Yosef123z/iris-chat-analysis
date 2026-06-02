"""
==============================================================
 Owner Chat Models — Business Owner Analytics Chatbot
==============================================================
 Pydantic models for the owner-facing analytics chatbot.
 Separate from customer chat to maintain domain boundaries.
==============================================================
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional


class OwnerChatRequest(BaseModel):
    """Request model for owner analytics questions."""
    session_id: str = Field(..., description="Owner session identifier")
    message: str = Field(..., min_length=1, description="Owner's analytics question")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_id": "owner-session-123",
            "message": "What was today's revenue?"
        }
    })


class OwnerChatResponse(BaseModel):
    """Response model for owner analytics chatbot."""
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
