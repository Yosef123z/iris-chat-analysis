"""Lazy service registry for the IRIS contract API."""

from functools import lru_cache

from app.config import settings
from app.core.llm_interface import LLMProvider, OpenAIProvider
from app.services.analytics_service import AnalyticsService
from app.services.business_knowledge_service import BusinessKnowledgeService
from app.services.chat_batch_analysis_service import ChatBatchAnalysisService
from app.services.chat_service import ChatService
from app.services.health_service import HealthCheckService
from app.services.owner_chat_service import OwnerChatService
from app.services.pii_service import PIIService
from app.services.session_memory import SessionMemoryStore

ACTIVE_PROVIDER = "openai"


@lru_cache
def get_llm_provider() -> LLMProvider:
    return OpenAIProvider()


@lru_cache
def get_business_knowledge_service() -> BusinessKnowledgeService:
    return BusinessKnowledgeService()


@lru_cache
def get_session_memory_store() -> SessionMemoryStore:
    return SessionMemoryStore(ttl_hours=settings.SESSION_TTL_HOURS)


@lru_cache
def get_pii_service() -> PIIService:
    return PIIService()


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(
        knowledge_service=get_business_knowledge_service(),
        memory_store=get_session_memory_store(),
        llm_provider=get_llm_provider(),
    )


@lru_cache
def get_chat_batch_analysis_service() -> ChatBatchAnalysisService:
    return ChatBatchAnalysisService(
        pii_service=get_pii_service(),
        llm_provider=get_llm_provider(),
    )


def clear_provider_caches() -> None:
    """Clear cached providers/services, mainly for tests and local reloads."""

    get_llm_provider.cache_clear()
    get_business_knowledge_service.cache_clear()
    get_session_memory_store.cache_clear()
    get_pii_service.cache_clear()
    get_chat_service.cache_clear()
    get_chat_batch_analysis_service.cache_clear()
    get_analytics_service.cache_clear()
    get_owner_chat_service.cache_clear()
    get_health_service.cache_clear()


@lru_cache
def get_analytics_service() -> AnalyticsService:
    return AnalyticsService(report_dir=settings.OWNER_ANALYTICS_REPORT_DIR)


@lru_cache
def get_owner_chat_service() -> OwnerChatService:
    return OwnerChatService(
        provider=get_llm_provider(),
        analytics_service=get_analytics_service(),
    )


@lru_cache
def get_health_service() -> HealthCheckService:
    return HealthCheckService()


class _LazyProxy:
    def __init__(self, factory):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_instance", None)

    def _get(self):
        instance = object.__getattribute__(self, "_instance")
        if instance is None:
            instance = object.__getattribute__(self, "_factory")()
            object.__setattr__(self, "_instance", instance)
        return instance

    def __getattr__(self, item):
        return getattr(self._get(), item)

    def __setattr__(self, key, value):
        if key in {"_factory", "_instance"}:
            object.__setattr__(self, key, value)
            return
        setattr(self._get(), key, value)


llm_provider: LLMProvider = _LazyProxy(get_llm_provider)  # type: ignore[assignment]
business_knowledge_service: BusinessKnowledgeService = _LazyProxy(get_business_knowledge_service)  # type: ignore[assignment]
session_memory_store: SessionMemoryStore = _LazyProxy(get_session_memory_store)  # type: ignore[assignment]
pii_service: PIIService = _LazyProxy(get_pii_service)  # type: ignore[assignment]
chat_service: ChatService = _LazyProxy(get_chat_service)  # type: ignore[assignment]
chat_batch_analysis_service: ChatBatchAnalysisService = _LazyProxy(get_chat_batch_analysis_service)  # type: ignore[assignment]
analytics_service: AnalyticsService = _LazyProxy(get_analytics_service)  # type: ignore[assignment]
owner_chat_service: OwnerChatService = _LazyProxy(get_owner_chat_service)  # type: ignore[assignment]
health_service: HealthCheckService = _LazyProxy(get_health_service)  # type: ignore[assignment]


__all__ = [
    "ACTIVE_PROVIDER",
    "llm_provider",
    "business_knowledge_service",
    "session_memory_store",
    "pii_service",
    "chat_service",
    "chat_batch_analysis_service",
    "analytics_service",
    "owner_chat_service",
    "health_service",
    "get_llm_provider",
    "get_business_knowledge_service",
    "get_session_memory_store",
    "get_pii_service",
    "get_chat_service",
    "get_chat_batch_analysis_service",
    "get_analytics_service",
    "get_owner_chat_service",
    "get_health_service",
    "clear_provider_caches",
]
