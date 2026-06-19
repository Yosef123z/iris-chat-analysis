import pytest

from app.models.business_kb import BusinessKnowledgeSyncRequest
from app.services.business_knowledge_service import BusinessKnowledgeService
from tests.conftest import FakeEmbeddings


def kb_payload(business_id="biz-1", *, item_name="Classic Burger", item_id="burger-1"):
    return BusinessKnowledgeSyncRequest.model_validate(
        {
            "business_id": business_id,
            "business_name": f"{business_id} Demo",
            "knowledge_base": {
                "menu_items": [
                    {
                        "menu_item_id": item_id,
                        "name": item_name,
                        "description": f"{item_name} description",
                        "price": 120,
                        "category": "Main",
                        "is_available": True,
                    }
                ],
                "faqs": [
                    {
                        "question": "Delivery time",
                        "answer": "Delivery takes 30 to 45 minutes.",
                        "is_faq": True,
                    }
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_persists_business_kb_after_successful_sync(tmp_path):
    service = BusinessKnowledgeService(storage_dir=tmp_path)
    embeddings = FakeEmbeddings()

    await service.sync_business_kb(kb_payload(), embeddings)

    artifacts = list(tmp_path.glob("*.pkl"))
    assert len(artifacts) == 1
    assert service.get_business_kb("biz-1") is not None
    assert service.get_business_index("biz-1") is not None
    assert len(embeddings.document_calls) == 1


@pytest.mark.asyncio
async def test_loads_persisted_kb_into_new_service_instance(tmp_path):
    original = BusinessKnowledgeService(storage_dir=tmp_path)
    await original.sync_business_kb(kb_payload(), FakeEmbeddings())

    restarted = BusinessKnowledgeService(storage_dir=tmp_path)
    loaded_count = restarted.load_persisted_indexes()

    assert loaded_count == 1
    assert restarted.get_business_kb("biz-1").business_name == "biz-1 Demo"
    assert restarted.get_business_index("biz-1").documents[0].title == "Classic Burger"


@pytest.mark.asyncio
async def test_restart_simulation_retrieves_without_regenerating_document_embeddings(tmp_path):
    original_embeddings = FakeEmbeddings()
    original = BusinessKnowledgeService(storage_dir=tmp_path)
    await original.sync_business_kb(kb_payload(), original_embeddings)

    restarted_embeddings = FakeEmbeddings()
    restarted = BusinessKnowledgeService(storage_dir=tmp_path)
    restarted.load_persisted_indexes()

    context = await restarted.retrieve_context(
        "biz-1",
        "Tell me about Classic Burger",
        restarted_embeddings,
    )

    assert context is not None
    assert context.candidate_items[0].name == "Classic Burger"
    assert restarted_embeddings.document_calls == []
    assert restarted_embeddings.query_calls == ["Tell me about Classic Burger"]


@pytest.mark.asyncio
async def test_overwrites_existing_business_kb_artifact_and_memory(tmp_path):
    service = BusinessKnowledgeService(storage_dir=tmp_path)
    await service.sync_business_kb(kb_payload(), FakeEmbeddings())
    await service.sync_business_kb(
        kb_payload(item_name="Margherita Pizza", item_id="pizza-1"),
        FakeEmbeddings(),
    )

    assert len(list(tmp_path.glob("*.pkl"))) == 1
    assert service.get_business_index("biz-1").documents[0].title == "Margherita Pizza"

    restarted = BusinessKnowledgeService(storage_dir=tmp_path)
    restarted.load_persisted_indexes()
    titles = [doc.title for doc in restarted.get_business_index("biz-1").documents]
    assert "Margherita Pizza" in titles
    assert "Classic Burger" not in titles


def test_corrupted_persisted_file_is_ignored(tmp_path):
    (tmp_path / "corrupted.pkl").write_bytes(b"not a pickle artifact")

    service = BusinessKnowledgeService(storage_dir=tmp_path)

    assert service.load_persisted_indexes() == 0
    assert service.get_business_kb("biz-1") is None


@pytest.mark.asyncio
async def test_corrupted_file_does_not_block_loading_remaining_businesses(tmp_path):
    original = BusinessKnowledgeService(storage_dir=tmp_path)
    await original.sync_business_kb(kb_payload("biz-1"), FakeEmbeddings())
    (tmp_path / "corrupted.pkl").write_bytes(b"not a pickle artifact")

    restarted = BusinessKnowledgeService(storage_dir=tmp_path)

    assert restarted.load_persisted_indexes() == 1
    assert restarted.get_business_kb("biz-1") is not None


@pytest.mark.asyncio
async def test_persisted_business_indexes_remain_isolated_after_reload(tmp_path):
    original = BusinessKnowledgeService(storage_dir=tmp_path)
    await original.sync_business_kb(
        kb_payload("biz-restaurant", item_name="Classic Burger", item_id="burger-1"),
        FakeEmbeddings(),
    )
    await original.sync_business_kb(
        kb_payload("biz-clinic", item_name="Dental Cleaning", item_id="svc-1"),
        FakeEmbeddings(),
    )

    restarted = BusinessKnowledgeService(storage_dir=tmp_path)
    restarted.load_persisted_indexes()

    restaurant_titles = [
        doc.title for doc in restarted.get_business_index("biz-restaurant").documents
    ]
    clinic_titles = [doc.title for doc in restarted.get_business_index("biz-clinic").documents]
    assert "Classic Burger" in restaurant_titles
    assert "Dental Cleaning" not in restaurant_titles
    assert "Dental Cleaning" in clinic_titles
    assert "Classic Burger" not in clinic_titles
