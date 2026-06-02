"""In-memory business-scoped knowledge-base storage and retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from rapidfuzz import fuzz

from app.models.business_kb import (
    BusinessFAQ,
    BusinessKnowledgeSyncRequest,
    BusinessMenuItem,
)


_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670]")
_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = _ARABIC_DIACRITICS_RE.sub("", text)
    replacements = {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return " ".join(_WORD_RE.findall(text))


def _tokens(text: str) -> set[str]:
    return {token for token in normalize_text(text).split() if len(token) > 1}


@dataclass(frozen=True)
class StoredBusinessKnowledge:
    business_id: str
    business_name: str
    menu_items: list[BusinessMenuItem]
    faqs: list[BusinessFAQ]
    updated_at: datetime

    @property
    def available_items(self) -> list[BusinessMenuItem]:
        return [item for item in self.menu_items if item.is_available]


class BusinessKnowledgeService:
    """Temporary in-memory KB store keyed by business_id."""

    def __init__(self) -> None:
        self._store: dict[str, StoredBusinessKnowledge] = {}

    def sync_business_kb(self, payload: BusinessKnowledgeSyncRequest) -> None:
        self._store[payload.business_id] = StoredBusinessKnowledge(
            business_id=payload.business_id,
            business_name=payload.business_name,
            menu_items=list(payload.knowledge_base.menu_items),
            faqs=list(payload.knowledge_base.faqs),
            updated_at=datetime.now(timezone.utc),
        )

    def get_business_kb(self, business_id: str) -> StoredBusinessKnowledge | None:
        return self._store.get(business_id)

    def clear(self) -> None:
        self._store.clear()

    def find_menu_item(
        self,
        business_id: str,
        user_text_or_name: str,
        *,
        min_score: int = 72,
    ) -> BusinessMenuItem | None:
        kb = self.get_business_kb(business_id)
        if kb is None or not kb.menu_items:
            return None

        normalized_query = normalize_text(user_text_or_name)
        if not normalized_query:
            return None

        for item in kb.menu_items:
            normalized_name = normalize_text(item.name)
            if normalized_name and normalized_name in normalized_query:
                return item

        best_item: BusinessMenuItem | None = None
        best_score = 0
        for item in kb.menu_items:
            searchable = " ".join(
                part
                for part in [
                    item.name,
                    item.description or "",
                    item.category or "",
                ]
                if part
            )
            score = max(
                fuzz.token_set_ratio(normalized_query, normalize_text(item.name)),
                fuzz.partial_ratio(normalized_query, normalize_text(searchable)),
            )
            if score > best_score:
                best_score = score
                best_item = item

        return best_item if best_item is not None and best_score >= min_score else None

    def search_menu_items(
        self,
        business_id: str,
        query: str,
        *,
        available_only: bool = True,
        limit: int = 5,
    ) -> list[BusinessMenuItem]:
        kb = self.get_business_kb(business_id)
        if kb is None:
            return []

        query_tokens = _tokens(query)
        candidates = kb.available_items if available_only else kb.menu_items
        if not query_tokens:
            return candidates[:limit]

        scored: list[tuple[int, BusinessMenuItem]] = []
        for item in candidates:
            searchable = " ".join(
                part
                for part in [item.name, item.description or "", item.category or ""]
                if part
            )
            item_tokens = _tokens(searchable)
            overlap = len(query_tokens & item_tokens) * 20
            fuzzy = fuzz.partial_ratio(normalize_text(query), normalize_text(searchable))
            score = max(overlap, int(fuzzy))
            if score > 35:
                scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def search_faqs(
        self,
        business_id: str,
        query: str,
        *,
        limit: int = 3,
    ) -> list[BusinessFAQ]:
        kb = self.get_business_kb(business_id)
        if kb is None:
            return []

        query_tokens = _tokens(query)
        scored: list[tuple[int, BusinessFAQ]] = []
        for faq in kb.faqs:
            searchable = f"{faq.question} {faq.answer}"
            faq_tokens = _tokens(searchable)
            overlap = len(query_tokens & faq_tokens) * 25
            fuzzy = fuzz.partial_ratio(normalize_text(query), normalize_text(searchable))
            score = max(overlap, int(fuzzy))
            if score > 35:
                scored.append((score, faq))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [faq for _, faq in scored[:limit]]

    def alternatives_for(self, business_id: str, item: BusinessMenuItem) -> list[BusinessMenuItem]:
        kb = self.get_business_kb(business_id)
        if kb is None:
            return []
        category = normalize_text(item.category or "")
        same_category = [
            candidate
            for candidate in kb.available_items
            if candidate.name != item.name
            and category
            and normalize_text(candidate.category or "") == category
        ]
        return same_category[:3] or [candidate for candidate in kb.available_items if candidate.name != item.name][:3]

    @staticmethod
    def summarize_items(items: Iterable[BusinessMenuItem]) -> str:
        parts = []
        for item in items:
            price = f" بسعر {item.price:g} جنيه" if item.price is not None else ""
            parts.append(f"{item.name}{price}")
        return "، ".join(parts)
