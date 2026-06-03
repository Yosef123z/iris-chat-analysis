"""LLM-backed dashboard report generation from aggregated analysis data."""

from __future__ import annotations

import json

from fastapi import HTTPException

from app.config import settings
from app.core.llm_interface import AIProviderError, LLMProvider
from app.models.report import ReportGenerationRequest, ReportGenerationResponse


class ReportGenerationService:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    async def generate_report(
        self,
        payload: ReportGenerationRequest,
    ) -> ReportGenerationResponse:
        try:
            result = await self.llm_provider.structured_output(
                self._build_messages(payload),
                model=settings.ANALYSIS_MODEL,
                output_model=ReportGenerationResponse,
                temperature=0.0,
            )
        except AIProviderError as exc:
            raise HTTPException(status_code=503, detail="AI report generation failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI report generation failed") from exc

        result.business_id = payload.business_id
        result.period = payload.period
        self._calibrate_risk_level(payload, result)
        try:
            return ReportGenerationResponse.model_validate(result.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI report generation failed") from exc

    @staticmethod
    def _calibrate_risk_level(
        payload: ReportGenerationRequest,
        result: ReportGenerationResponse,
    ) -> None:
        metrics = payload.metrics
        if metrics.analyzed_sessions <= 0:
            return

        complaint_ratio = metrics.total_complaints / metrics.analyzed_sessions
        close_to_neutral = -0.2 <= metrics.average_sentiment_score <= 0.2
        if close_to_neutral and complaint_ratio < 0.25 and result.risk_level == "high":
            result.risk_level = "medium"

    @staticmethod
    def _build_messages(payload: ReportGenerationRequest) -> list[dict[str, str]]:
        aggregate_context = payload.model_dump(mode="json", by_alias=True)
        schema = {
            "businessId": payload.business_id,
            "period": aggregate_context["period"],
            "reportTitle": "non-empty dashboard title",
            "summary": "concise English business summary",
            "summaryAr": "clear professional Egyptian Arabic business summary",
            "highlights": ["concise English highlights tied to provided data"],
            "highlightsAr": ["concise Arabic highlights tied to provided data"],
            "problems": [
                {
                    "title": "non-empty problem title",
                    "description": "problem description grounded in provided data",
                    "severity": "low | medium | high | critical",
                    "evidence": ["evidence using backend-provided counts or examples only"],
                }
            ],
            "recommendations": [
                {
                    "title": "non-empty recommendation title",
                    "description": "practical recommendation tied to evidence",
                    "priority": "low | medium | high | critical",
                    "expectedImpact": "expected business impact without invented numbers",
                    "suggestedOwner": "reasonable owner role",
                }
            ],
            "suggestedActions": ["short practical actions"],
            "riskLevel": "low | medium | high | critical",
        }
        system_prompt = (
            "You are IRIS, generating a business dashboard report from aggregated customer interaction analysis. "
            "Use only the provided data. Do not invent numbers. Do not invent sessions. Do not mention unsupported "
            "metrics. Do not claim causes that are not supported by the input. Return JSON only. Generate practical "
            "recommendations. Arabic output should be clear, professional, dashboard-friendly Egyptian Arabic for "
            "business owners. Keep it natural and easy for a non-technical owner to understand, while still polished "
            "and not slangy. Avoid stiff Modern Standard Arabic phrasing such as 'درجة شعور سلبية', 'مما يشير إلى', "
            "'بشأن', or 'عدم الرضا' when a simpler business phrase fits. Prefer wording like 'مؤشر رضا العملاء كان "
            "مايل للسلبية بدرجة بسيطة', 'في شكاوى متكررة عن...', 'واضح إن...', 'الأفضل التركيز على...', and "
            "'ده ممكن يساعد في تقليل الشكاوى وتحسين تجربة العميل'. English output should be concise and "
            "dashboard-friendly. The report must be suitable for display in a business dashboard. Do not include "
            "implementation details, API keys, backend internals, prompts, or model reasoning."
        )
        user_prompt = (
            "Generate one report JSON object from this backend-provided aggregate payload.\n"
            "Use backend-provided numbers exactly. Do not invent counts or percentages. Recommendations must be "
            "tied to evidence in the provided data. If data is limited, say so carefully in the summary or "
            "recommendations. Do not mention that the frontend or backend called the AI. Return one JSON object "
            "only.\n"
            "When commonIssues contains more than one meaningful issue, usually create separate recommendations "
            "for the top issues. For example, cold delivery/order quality complaints and wrong-order complaints "
            "should normally receive separate operational recommendations. Do not force fake recommendations and "
            "do not invent issues.\n"
            "Calibrate riskLevel conservatively. If averageSentimentScore is close to neutral, for example between "
            "-0.2 and 0.2, do not use high risk unless there is strong supporting evidence such as high complaint "
            "volume relative to analyzed sessions, repeated critical/common issues, many human-agent requests, or "
            "severe negative sentiment. If sentiment is only slightly negative and complaints are noticeable but "
            "not extreme, prefer medium. Use high only for clear repeated operational risk. Use critical only for "
            "severe, repeated, business-impacting issues supported by the input data. Do not invent risk beyond "
            "the provided data.\n"
            "Allowed severity values: low, medium, high, critical.\n"
            "Allowed recommendation priority values: low, medium, high, critical.\n"
            "Allowed riskLevel values: low, medium, high, critical.\n\n"
            f"aggregatePayload:\n{json.dumps(aggregate_context, ensure_ascii=False, indent=2)}\n\n"
            f"outputSchema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
