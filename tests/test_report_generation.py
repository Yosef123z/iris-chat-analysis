import asyncio

import pytest

from app.config import settings
from app.core.llm_interface import AIProviderError
from app.models.report import ReportGenerationResponse
from app.services.report_generation_service import ReportGenerationService
from tests.conftest import prompt_text


def report_payload():
    return {
        "businessId": "biz-restaurant-demo",
        "businessName": "Demo Restaurant",
        "period": {
            "from": "2026-06-01T00:00:00Z",
            "to": "2026-06-30T23:59:59Z",
        },
        "metrics": {
            "totalSessions": 120,
            "analyzedSessions": 115,
            "averageSentimentScore": -0.12,
            "sentimentDistribution": {
                "positive": 35,
                "neutral": 50,
                "negative": 30,
            },
            "totalComplaints": 22,
            "totalHumanAgentRequests": 14,
            "totalOrdersDetected": 60,
        },
        "topIntents": [
            {"name": "CreateOrder", "count": 60},
            {"name": "Complaint", "count": 22},
        ],
        "topTopics": [
            {"name": "delivery", "count": 18},
            {"name": "cold food", "count": 12},
        ],
        "commonIssues": [
            {
                "issue": "Orders arriving cold",
                "count": 12,
                "examples": ["Customer received cold burger"],
            }
        ],
        "recentKeyMoments": [
            "Customer received cold burger",
            "Customer requested to speak to manager",
        ],
        "sampleSummaries": [
            {
                "sessionId": "session-001",
                "summary": "Customer ordered a Classic Burger but received it cold.",
                "summaryAr": "العميل طلب Classic Burger لكن الأوردر وصل بارد.",
                "mainIntent": "Complaint",
                "sentimentLabel": "Negative",
                "sentimentScore": -0.8,
            }
        ],
    }


def test_report_endpoint_is_in_openapi_and_legacy_report_paths_absent(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/analysis/report/generate" in paths
    assert "/api/v1/reports/generate" not in paths
    assert "/api/v1/report/generate" not in paths


def test_valid_report_payload_returns_structured_response(client, fake_provider):
    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["businessId"] == "biz-restaurant-demo"
    assert data["period"] == report_payload()["period"]
    assert data["reportTitle"]
    assert data["summary"]
    assert data["summaryAr"]
    assert data["highlights"]
    assert data["highlightsAr"]
    assert data["problems"][0]["severity"] in {"low", "medium", "high", "critical"}
    assert data["recommendations"][0]["priority"] in {"low", "medium", "high", "critical"}
    assert data["suggestedActions"]
    assert data["riskLevel"] in {"low", "medium", "high", "critical"}

    assert len(fake_provider.structured_calls) == 1
    call = fake_provider.structured_calls[0]
    assert call["output_model"] is ReportGenerationResponse
    assert call["temperature"] == 0.0


def test_report_prompt_contains_aggregate_data_and_guardrails(client, fake_provider):
    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 200

    text = prompt_text(fake_provider)
    for expected in [
        "biz-restaurant-demo",
        "Demo Restaurant",
        "period",
        "metrics",
        "topIntents",
        "topTopics",
        "commonIssues",
        "recentKeyMoments",
        "sampleSummaries",
        "Do not invent numbers",
        "Return JSON only",
        "Use backend-provided numbers exactly",
        "dashboard-friendly Egyptian Arabic",
        "business owners",
        "non-technical owner",
        "not slangy",
        "Avoid stiff Modern Standard Arabic",
        "واضح إن",
        "When commonIssues contains more than one meaningful issue",
        "separate recommendations for the top issues",
        "Calibrate riskLevel conservatively",
        "between -0.2 and 0.2",
        "prefer medium",
        "Use critical only for severe, repeated, business-impacting issues",
    ]:
        assert expected in text


def test_report_service_downgrades_high_risk_for_neutral_low_ratio_payload(fake_provider):
    fake_provider.report_outputs.append(
        {
            "businessId": "biz-restaurant-demo",
            "period": report_payload()["period"],
            "reportTitle": "Customer Experience Report",
            "summary": "Summary grounded in the input.",
            "summaryAr": "ملخص مناسب للبيانات المرسلة.",
            "highlights": [],
            "highlightsAr": [],
            "problems": [],
            "recommendations": [],
            "suggestedActions": [],
            "riskLevel": "high",
        }
    )
    service = ReportGenerationService(fake_provider)
    from app.models.report import ReportGenerationRequest

    result = asyncio.run(
        service.generate_report(ReportGenerationRequest.model_validate(report_payload()))
    )

    assert result.risk_level == "medium"


def test_report_service_forces_request_business_id_and_period(client, fake_provider):
    fake_provider.report_outputs.append(
        {
            "businessId": "wrong-business",
            "period": {
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-01-31T23:59:59Z",
            },
            "reportTitle": "Customer Experience Report",
            "summary": "Summary grounded in the input.",
            "summaryAr": "ملخص مناسب للبيانات المرسلة.",
            "highlights": [],
            "highlightsAr": [],
            "problems": [],
            "recommendations": [],
            "suggestedActions": [],
            "riskLevel": "low",
        }
    )
    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["businessId"] == report_payload()["businessId"]
    assert data["period"] == report_payload()["period"]


@pytest.mark.parametrize(
    "output_patch",
    [
        {"riskLevel": "urgent"},
        {"problems": [{"title": "Issue", "description": "Bad issue", "severity": "urgent", "evidence": []}]},
        {
            "recommendations": [
                {
                    "title": "Fix issue",
                    "description": "Fix it",
                    "priority": "urgent",
                    "expectedImpact": "Improves quality",
                    "suggestedOwner": "Ops",
                }
            ]
        },
    ],
)
def test_invalid_report_llm_output_returns_503(client, fake_provider, output_patch):
    output = {
        "businessId": "biz-restaurant-demo",
        "period": report_payload()["period"],
        "reportTitle": "Customer Experience Report",
        "summary": "Summary grounded in the input.",
        "summaryAr": "ملخص مناسب للبيانات المرسلة.",
        "highlights": [],
        "highlightsAr": [],
        "problems": [],
        "recommendations": [],
        "suggestedActions": [],
        "riskLevel": "low",
    }
    output.update(output_patch)
    fake_provider.report_outputs.append(output)

    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 503
    assert response.json()["detail"] == "AI report generation failed"


def test_report_provider_failure_returns_503(client, fake_provider):
    fake_provider.report_outputs.append(AIProviderError("provider down"))
    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 503
    assert response.json()["detail"] == "AI report generation failed"


@pytest.mark.parametrize(
    "patch",
    [
        {"businessId": ""},
        {"metrics": {"averageSentimentScore": 1.5}},
        {"metrics": {"totalComplaints": -1}},
        {"metrics": {"totalSessions": 1, "analyzedSessions": 2}},
        {"topIntents": [{"name": "Complaint", "count": -1}]},
    ],
)
def test_report_request_validation_rejects_invalid_payloads(client, patch):
    payload = report_payload()
    if "metrics" in patch:
        payload["metrics"].update(patch["metrics"])
    else:
        payload.update(patch)

    response = client.post("/api/v1/analysis/report/generate", json=payload)
    assert response.status_code == 422


def test_api_key_middleware_protects_report_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "AI_BACKEND_API_KEY", "secret")

    response = client.post("/api/v1/analysis/report/generate", json=report_payload())
    assert response.status_code == 401

    response = client.post(
        "/api/v1/analysis/report/generate",
        json=report_payload(),
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 200, response.text


def test_report_service_calls_structured_output(fake_provider):
    service = ReportGenerationService(fake_provider)
    from app.models.report import ReportGenerationRequest

    result = asyncio.run(
        service.generate_report(ReportGenerationRequest.model_validate(report_payload()))
    )

    assert result.business_id == "biz-restaurant-demo"
    assert len(fake_provider.structured_calls) == 1
    assert fake_provider.structured_calls[0]["output_model"] is ReportGenerationResponse
