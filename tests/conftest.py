import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from app.core.provider import get_business_knowledge_service, get_session_memory_store
from app.main import app


@pytest.fixture
def client():
    get_business_knowledge_service().clear()
    get_session_memory_store().clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
