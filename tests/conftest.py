import json

import pytest
from fastapi.testclient import TestClient

from app.core.llm_interface import AIProviderError, LLMProvider
from app.core.provider import (
    clear_provider_caches,
    get_business_knowledge_service,
    get_chat_batch_analysis_service,
    get_chat_service,
    get_llm_provider,
)
from app.main import app
from app.models.chat import OrderDetails
from app.services.business_knowledge_service import BusinessKnowledgeService
from app.services.chat_batch_analysis_service import ChatBatchAnalysisService
from app.services.chat_service import ChatService, CustomerChatLLMOutput
from app.services.pii_service import PIIService
from app.services.session_memory import SessionMemoryStore


class FakeEmbeddings:
    def __init__(self):
        self.document_calls = []
        self.query_calls = []
        self.fail_documents = False
        self.fail_query = False

    async def aembed_documents(self, texts):
        self.document_calls.append(list(texts))
        if self.fail_documents:
            raise AIProviderError("fake embeddings failure")
        return [self._vector(text) for text in texts]

    async def aembed_query(self, text):
        self.query_calls.append(text)
        if self.fail_query:
            raise AIProviderError("fake query failure")
        return self._vector(text)

    @staticmethod
    def _vector(text):
        normalized = (text or "").lower()
        return [
            float(len(normalized)),
            float(normalized.count("burger") + normalized.count("برجر")),
            float(normalized.count("pizza")),
            float(normalized.count("dental")),
            float(normalized.count("latte")),
            float(sum(ord(char) for char in normalized) % 997),
        ]


class FakeLLMProvider(LLMProvider):
    def __init__(self):
        self.embeddings = FakeEmbeddings()
        self.chat_outputs = []
        self.analysis_outputs = []
        self.structured_calls = []

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024):
        raise AIProviderError("chat should not be used in contract tests")

    async def structured_output(self, messages, model, output_model, temperature=0.0):
        self.structured_calls.append(
            {
                "messages": messages,
                "model": model,
                "output_model": output_model,
                "temperature": temperature,
            }
        )
        if output_model is CustomerChatLLMOutput:
            output = self.chat_outputs.pop(0) if self.chat_outputs else {
                "reply": "أكيد يا فندم، المعلومة دي متاحة من بيانات النشاط.",
                "order_detected": False,
                "order_finalized": False,
                "order_details": None,
                "ticket_detected": False,
                "ticket_details": None,
                "escalation_requested": False,
                "feedback_requested": False,
            }
        else:
            output = self.analysis_outputs.pop(0) if self.analysis_outputs else {
                "sessionId": "analysis-1",
                "summary": "Customer asked a general question.",
                "summaryAr": "العميل سأل سؤال عام.",
                "overallSentiment": {"score": 0.0, "label": "Neutral"},
                "mainIntent": "GeneralQuestion",
                "intentsDetected": [{"name": "GeneralQuestion", "count": 1}],
                "mainTopics": [],
                "keyMoments": [],
            }
        if isinstance(output, Exception):
            raise output
        if isinstance(output, str):
            return output_model.model_validate_json(output)
        return output_model.model_validate(output)

    def get_embeddings_model(self):
        return self.embeddings


@pytest.fixture
def fake_provider():
    return FakeLLMProvider()


@pytest.fixture
def client(fake_provider):
    clear_provider_caches()
    knowledge = BusinessKnowledgeService()
    memory = SessionMemoryStore()
    pii = PIIService()

    app.dependency_overrides[get_llm_provider] = lambda: fake_provider
    app.dependency_overrides[get_business_knowledge_service] = lambda: knowledge
    app.dependency_overrides[get_chat_service] = lambda: ChatService(
        knowledge_service=knowledge,
        memory_store=memory,
        llm_provider=fake_provider,
    )
    app.dependency_overrides[get_chat_batch_analysis_service] = lambda: ChatBatchAnalysisService(
        pii_service=pii,
        llm_provider=fake_provider,
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    clear_provider_caches()


def llm_chat_output(
    *,
    reply="تمام يا فندم.",
    order_detected=False,
    order_finalized=False,
    items=None,
    ticket_detected=False,
    ticket_details=None,
    escalation_requested=False,
    feedback_requested=False,
):
    details = None
    if items is not None:
        details = OrderDetails(
            intent="CreateOrder",
            items=items,
            total_amount=sum(item.quantity * item.price for item in items),
        ).model_dump()
    return {
        "reply": reply,
        "order_detected": order_detected,
        "order_finalized": order_finalized,
        "order_details": details,
        "ticket_detected": ticket_detected,
        "ticket_details": ticket_details,
        "escalation_requested": escalation_requested,
        "feedback_requested": feedback_requested,
    }


def prompt_text(fake_provider):
    return "\n".join(
        message["content"]
        for call in fake_provider.structured_calls
        for message in call["messages"]
    )
