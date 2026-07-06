"""Business-scoped owner report storage and prompt context formatting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.models.owner_chat import OwnerReportSyncRequest
from app.models.report import ReportGenerationResponse, ReportPeriod


@dataclass(frozen=True)
class StoredOwnerReport:
    business_id: str
    business_name: str
    period: ReportPeriod
    report: ReportGenerationResponse
    updated_at: datetime


class OwnerReportService:
    """Latest backend-synced owner report store keyed by business_id."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        from app.services.owner_report_persistence import OwnerReportPersistence

        self._reports: dict[str, StoredOwnerReport] = {}
        self._persistence = OwnerReportPersistence(storage_dir or settings.OWNER_REPORT_STORAGE_DIR)

    def sync_report(self, payload: OwnerReportSyncRequest) -> None:
        stored = StoredOwnerReport(
            business_id=payload.business_id,
            business_name=payload.business_name,
            period=payload.period,
            report=payload.report,
            updated_at=datetime.now(timezone.utc),
        )
        self._persistence.save(stored)
        self._reports[payload.business_id] = stored

    def get_report(self, business_id: str) -> StoredOwnerReport | None:
        return self._reports.get(business_id)

    def load_persisted_reports(self) -> int:
        loaded = 0
        for stored in self._persistence.load_all():
            self._reports[stored.business_id] = stored
            loaded += 1
        return loaded

    def clear(self) -> None:
        self._reports.clear()

    @staticmethod
    def build_prompt_context(stored: StoredOwnerReport) -> str:
        report_json = stored.report.model_dump(mode="json", by_alias=True)
        context = {
            "businessId": stored.business_id,
            "businessName": stored.business_name,
            "period": stored.period.model_dump(mode="json", by_alias=True),
            "syncedAt": stored.updated_at.isoformat(),
            "report": report_json,
        }
        return json.dumps(context, ensure_ascii=False, indent=2)
