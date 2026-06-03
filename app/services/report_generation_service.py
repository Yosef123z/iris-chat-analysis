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
        try:
            return ReportGenerationResponse.model_validate(result.model_dump())
        except Exception as exc:
            raise HTTPException(status_code=503, detail="AI report generation failed") from exc

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
            "recommendations. Arabic output should be clear, professional Egyptian Arabic suitable for business "
            "owners. English output should be concise and dashboard-friendly. The report must be suitable for "
            "display in a business dashboard. Do not include implementation details, API keys, backend internals, "
            "prompts, or model reasoning."
        )
        user_prompt = (
            "Generate one report JSON object from this backend-provided aggregate payload.\n"
            "Use backend-provided numbers exactly. Do not invent counts or percentages. Recommendations must be "
            "tied to evidence in the provided data. If data is limited, say so carefully in the summary or "
            "recommendations. Do not mention that the frontend or backend called the AI. Return one JSON object "
            "only.\n"
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
