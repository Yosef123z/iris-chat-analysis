from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.models.analysis import PIIRemoveResult


@dataclass(frozen=True)
class _PIIPattern:
    name: str
    pattern: re.Pattern[str]
    replacement: str


class PIIService:
    """Deterministic PII redaction for transcripts before persistence."""

    _patterns = (
        _PIIPattern(
            "email",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "[EMAIL]",
        ),
        _PIIPattern(
            "egyptian_phone",
            re.compile(r"(?<!\d)(?:\+?20|0020|0)?\s?1[0125](?:[\s-]?\d){8}(?!\d)"),
            "[PHONE]",
        ),
        _PIIPattern(
            "national_id",
            re.compile(r"(?<!\d)[23]\d{13}(?!\d)"),
            "[NATIONAL_ID]",
        ),
        _PIIPattern(
            "address_hint",
            re.compile(
                r"(?i)\b(?:address|street|st\.?|building|apartment|flat|floor)\b"
                r"[:\s,-]+[^\n،.]{3,80}"
            ),
            "[ADDRESS]",
        ),
        _PIIPattern(
            "arabic_address_hint",
            re.compile(
                r"(?:عنواني|العنوان|شارع|عمارة|شقة|الدور)\s*[:،-]?\s*[^\n.]{3,80}"
            ),
            "[ADDRESS]",
        ),
        _PIIPattern(
            "english_name_hint",
            re.compile(r"(?i)\b(?:my name is|i am|this is)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}"),
            "[NAME]",
        ),
        _PIIPattern(
            "arabic_name_hint",
            re.compile(r"(?:اسمي|انا اسمي|أنا اسمي|معاك|مع حضرتك)\s+[\u0600-\u06FF]{2,}(?:\s+[\u0600-\u06FF]{2,}){0,2}"),
            "[NAME]",
        ),
    )

    def remove_pii(self, text: str) -> PIIRemoveResult:
        clean_text = text or ""
        redactions: Counter[str] = Counter()

        for pii_pattern in self._patterns:
            clean_text, count = pii_pattern.pattern.subn(
                pii_pattern.replacement, clean_text
            )
            if count:
                redactions[pii_pattern.name] += count

        return PIIRemoveResult(
            original_text=text or "",
            clean_text=clean_text,
            redactions=dict(redactions),
        )

    def remove_pii_text(self, text: str) -> str:
        return self.remove_pii(text).clean_text
