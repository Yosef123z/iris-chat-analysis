"""Business-scoped knowledge-base storage and retrieval."""

from __future__ import annotations

import re
import math
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from rapidfuzz import fuzz

from app.config import settings
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


@dataclass(frozen=True)
class BusinessKnowledgeDocument:
    id: str
    type: Literal["menu_item", "faq", "knowledge_entry"]
    title: str
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrievedKnowledgeDocument:
    document: BusinessKnowledgeDocument
    score: float


@dataclass(frozen=True)
class BusinessVectorIndex:
    business_id: str
    business_name: str
    documents: list[BusinessKnowledgeDocument]
    vectors: list[list[float]]
    updated_at: datetime


@dataclass(frozen=True)
class BusinessRetrievalContext:
    business_id: str
    business_name: str
    documents: list[RetrievedKnowledgeDocument]
    candidate_items: list[BusinessMenuItem]
    relevant_faqs: list[BusinessFAQ]


class BusinessKnowledgeService:
    """Business KB store keyed by business_id with local index persistence."""

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        from app.services.business_kb_persistence import BusinessKBPersistence

        self._store: dict[str, StoredBusinessKnowledge] = {}
        self._indexes: dict[str, BusinessVectorIndex] = {}
        self._persistence = BusinessKBPersistence(storage_dir or settings.BUSINESS_KB_STORAGE_DIR)

    async def sync_business_kb(
        self,
        payload: BusinessKnowledgeSyncRequest,
        embeddings_model: Any,
    ) -> None:
        documents = self._build_documents(payload)
        vectors = await self._embed_documents(embeddings_model, [doc.content for doc in documents])
        if len(vectors) != len(documents):
            raise ValueError("Embeddings count did not match document count")

        updated_at = datetime.now(timezone.utc)
        stored = StoredBusinessKnowledge(
            business_id=payload.business_id,
            business_name=payload.business_name,
            menu_items=list(payload.knowledge_base.menu_items),
            faqs=list(payload.knowledge_base.faqs),
            updated_at=updated_at,
        )
        index = BusinessVectorIndex(
            business_id=payload.business_id,
            business_name=payload.business_name,
            documents=documents,
            vectors=vectors,
            updated_at=updated_at,
        )

        self._persistence.save(stored, index)
        self._store[payload.business_id] = stored
        self._indexes[payload.business_id] = index

    def get_business_kb(self, business_id: str) -> StoredBusinessKnowledge | None:
        return self._store.get(business_id)

    def get_business_index(self, business_id: str) -> BusinessVectorIndex | None:
        return self._indexes.get(business_id)

    def load_persisted_indexes(self) -> int:
        loaded = 0
        for stored, index in self._persistence.load_all():
            self._store[stored.business_id] = stored
            self._indexes[index.business_id] = index
            loaded += 1
        return loaded

    def clear(self) -> None:
        self._store.clear()
        self._indexes.clear()

    async def retrieve_context(
        self,
        business_id: str,
        query: str,
        embeddings_model: Any,
        *,
        limit: int = 6,
    ) -> BusinessRetrievalContext | None:
        kb = self.get_business_kb(business_id)
        index = self.get_business_index(business_id)
        if kb is None or index is None:
            return None

        vector_scores: list[float] = [0.0 for _ in index.documents]
        if index.documents and index.vectors:
            query_vector = await self._embed_query(embeddings_model, query)
            vector_scores = [
                self._cosine_similarity(query_vector, vector)
                for vector in index.vectors
            ]

        scored: list[tuple[float, BusinessKnowledgeDocument]] = []
        normalized_query = normalize_text(query)
        for idx, document in enumerate(index.documents):
            score = vector_scores[idx] if idx < len(vector_scores) else 0.0
            searchable = normalize_text(f"{document.title} {document.content}")
            if document.type == "menu_item":
                item_name = normalize_text(str(document.metadata.get("name", "")))
                category = normalize_text(str(document.metadata.get("category", "")))
                if item_name and item_name in normalized_query:
                    score += 1.0
                if category and category in normalized_query:
                    score += 0.45
                score += fuzz.token_set_ratio(normalized_query, item_name) / 250
                score += fuzz.partial_ratio(normalized_query, searchable) / 500
            else:
                score += fuzz.partial_ratio(normalized_query, searchable) / 400
            scored.append((score, document))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_docs = [
            RetrievedKnowledgeDocument(document=doc, score=score)
            for score, doc in scored[:limit]
            if score > 0
        ]

        candidate_items = self._candidate_items_from_documents(kb, top_docs)
        if not candidate_items:
            candidate_items = self.search_menu_items(
                business_id,
                query,
                available_only=False,
                limit=limit,
            )
        if not candidate_items and self._looks_like_catalog_request(query):
            candidate_items = kb.available_items[:limit]

        relevant_faqs = self._faqs_from_documents(kb, top_docs)
        if not relevant_faqs:
            relevant_faqs = self.search_faqs(business_id, query, limit=3)

        return BusinessRetrievalContext(
            business_id=kb.business_id,
            business_name=kb.business_name,
            documents=top_docs,
            candidate_items=candidate_items[:limit],
            relevant_faqs=relevant_faqs[:3],
        )

    @staticmethod
    async def _embed_documents(embeddings_model: Any, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async_embed = getattr(embeddings_model, "aembed_documents", None)
        if callable(async_embed):
            return await async_embed(texts)
        return await asyncio.to_thread(embeddings_model.embed_documents, texts)

    @staticmethod
    async def _embed_query(embeddings_model: Any, text: str) -> list[float]:
        async_embed = getattr(embeddings_model, "aembed_query", None)
        if callable(async_embed):
            return await async_embed(text)
        return await asyncio.to_thread(embeddings_model.embed_query, text)

    @staticmethod
    def _build_documents(payload: BusinessKnowledgeSyncRequest) -> list[BusinessKnowledgeDocument]:
        documents: list[BusinessKnowledgeDocument] = []
        for item in payload.knowledge_base.menu_items:
            availability = "available" if item.is_available else "unavailable"
            content = "\n".join(
                part
                for part in [
                    f"Name: {item.name}",
                    f"Description: {item.description or ''}",
                    f"Category: {item.category or ''}",
                    f"Price: {item.price}",
                    f"Availability: {availability}",
                ]
                if part is not None
            )
            documents.append(
                BusinessKnowledgeDocument(
                    id=f"menu_item:{item.menu_item_id}",
                    type="menu_item",
                    title=item.name,
                    content=content,
                    metadata={
                        "menu_item_id": item.menu_item_id,
                        "name": item.name,
                        "description": item.description,
                        "price": item.price,
                        "category": item.category,
                        "is_available": item.is_available,
                    },
                )
            )

        for index, faq in enumerate(payload.knowledge_base.faqs):
            documents.append(
                BusinessKnowledgeDocument(
                    id=f"faq:{index}:{normalize_text(faq.question)[:40]}",
                    type="faq" if faq.is_faq else "knowledge_entry",
                    title=faq.question,
                    content=f"Question: {faq.question}\nAnswer: {faq.answer}",
                    metadata={
                        "question": faq.question,
                        "answer": faq.answer,
                        "is_faq": faq.is_faq,
                    },
                )
            )
        return documents

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        length = min(len(left), len(right))
        dot = sum(left[i] * right[i] for i in range(length))
        left_norm = math.sqrt(sum(value * value for value in left[:length]))
        right_norm = math.sqrt(sum(value * value for value in right[:length]))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    @staticmethod
    def _candidate_items_from_documents(
        kb: StoredBusinessKnowledge,
        documents: list[RetrievedKnowledgeDocument],
    ) -> list[BusinessMenuItem]:
        by_id = {item.menu_item_id: item for item in kb.menu_items}
        items: list[BusinessMenuItem] = []
        for result in documents:
            if result.document.type != "menu_item":
                continue
            item_id = result.document.metadata.get("menu_item_id")
            item = by_id.get(str(item_id))
            if item is not None and item not in items:
                items.append(item)
        return items

    @staticmethod
    def _faqs_from_documents(
        kb: StoredBusinessKnowledge,
        documents: list[RetrievedKnowledgeDocument],
    ) -> list[BusinessFAQ]:
        faqs: list[BusinessFAQ] = []
        for result in documents:
            if result.document.type not in {"faq", "knowledge_entry"}:
                continue
            question = result.document.metadata.get("question")
            for faq in kb.faqs:
                if faq.question == question and faq not in faqs:
                    faqs.append(faq)
        return faqs

    @staticmethod
    def _looks_like_catalog_request(query: str) -> bool:
        normalized = normalize_text(query)
        
        # Core keywords where any substring match indicates catalog request
        keywords = {
            "menu", "catalog", "product", "service", "item", "price", "list", "offer",
            "منيو", "كتالوج", "منتج", "خدمه", "خدمة", "سعر", "اسعار", "اصناف", "أصناف",
            "قائمه", "قائمة", "قايمه", "قايمة", "عرض", "عروض", "اكلات", "مشروبات"
        }
        if any(normalize_text(kw) in normalized for kw in keywords):
            return True
            
        # Natural phrases (normalized) that ask "what do you have / what is there"
        phrases = {
            "what do you have", "what is available", "what's available", "what is there", 
            "what's there", "what you have", "what do you offer", "what you offer",
            "عندك ايه", "عندكم ايه", "عندكو ايه", "عندكوا ايه", "موجود ايه", "فيه ايه", "في ايه",
            "ايه المتاح", "متاح ايه", "ايه عندك", "ايه عندكم", "ايه عندكوا", "ايه اللي عندكم", 
            "ايه الي عندكم", "عندك اكل", "عندكم اكل", "عندك شرب", "عندكم شرب"
        }
        return any(normalize_text(phrase) in normalized for phrase in phrases)

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
