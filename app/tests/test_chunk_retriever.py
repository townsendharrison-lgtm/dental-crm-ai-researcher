"""Tests for LlamaIndex category retrieval and retrieve-mode document worker."""
import pytest
from llama_index.core.embeddings import BaseEmbedding

from app.clients.chunk_retriever import assign_chunks_by_category, taxonomy_for_categories
from app.clients.fetch_client import DocumentChunk
from app.factor_taxonomy import FactorTaxonomy
from app.workers.document_worker import DocumentWorker

from .test_document_extraction import fact, make_llm, response
from .test_document_workflow import memory_service


class KeywordEmbed(BaseEmbedding):
    """Deterministic fake embedder: similar texts share keyword-driven vectors."""

    def _vector(self, text: str) -> list[float]:
        lower = text.lower()
        return [
            1.0 if "gpa" in lower or "academics" in lower or "average overall" in lower else 0.0,
            1.0 if "dat" in lower or "academic average" in lower else 0.0,
            1.0 if "shadow" in lower else 0.0,
            0.1,
        ]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)


@pytest.fixture
def multi_taxonomy():
    return FactorTaxonomy.model_validate({
        "version": "retrieve-test",
        "categories": ["Academics", "DAT", "Shadowing"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average overall GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "avg_dat_aa", "category": "DAT", "description": "Average DAT Academic Average",
             "value_type": "number", "unit": "points"},
            {"key": "shadowing_hours", "category": "Shadowing", "description": "Required shadowing hours",
             "value_type": "number", "unit": "hours"},
        ],
    })


def test_taxonomy_for_categories_keeps_subset_only(multi_taxonomy):
    subset = taxonomy_for_categories(multi_taxonomy, ["DAT"])
    assert subset.categories == ("DAT",)
    assert [f.key for f in subset.factors] == ["avg_dat_aa"]


def test_assign_chunks_by_category_selects_relevant_chunks(multi_taxonomy):
    chunks = [
        DocumentChunk(index=0, text="Average overall GPA 3.7 for the class", page_number=1,
                      section=None, used_ocr=False),
        DocumentChunk(index=1, text="Average DAT Academic Average is 20", page_number=2,
                      section=None, used_ocr=False),
        DocumentChunk(index=2, text="Parking permits are available at the lobby desk", page_number=3,
                      section=None, used_ocr=False),
        DocumentChunk(index=3, text="Minimum shadowing hours required: 100", page_number=4,
                      section=None, used_ocr=False),
    ]
    assignments = assign_chunks_by_category(
        chunks, multi_taxonomy, api_key="unused", top_k=1, embed_model=KeywordEmbed(),
    )
    by_index = {item.chunk_index: set(item.categories) for item in assignments}
    assert "Academics" in by_index[0]
    assert "DAT" in by_index[1]
    assert "Shadowing" in by_index[3]
    assert 2 not in by_index  # parking boilerplate never retrieved


async def test_worker_retrieve_mode_extracts_only_selected_chunks(settings):
    settings = settings.model_copy(update={"document_extract_mode": "retrieve", "document_retrieve_top_k": 1})
    taxonomy = FactorTaxonomy.model_validate({
        "version": "retrieve-workflow",
        "categories": ["Academics", "DAT"],
        "factors": [
            {"key": "avg_gpa", "category": "Academics", "description": "Average overall GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "avg_science_gpa", "category": "Academics", "description": "Average science GPA",
             "value_type": "number", "unit": "gpa"},
            {"key": "avg_dat_aa", "category": "DAT", "description": "Average DAT AA",
             "value_type": "number", "unit": "points"},
        ],
    })
    service = memory_service(settings, taxonomy)

    def fake_retriever(chunks, taxonomy, **_kwargs):
        from app.clients.chunk_retriever import ChunkAssignment
        return [ChunkAssignment(chunk_index=chunks[0].index, categories=("Academics",))]

    llm, calls = make_llm(settings, [response([
        fact(),
        fact(factor_key="avg_science_gpa", value=3.6, raw_text_snippet="Average science GPA 3.6"),
    ])])
    try:
        outcome = await DocumentWorker(service, llm, heartbeat=False, retriever=fake_retriever).run_once()
    finally:
        await llm.close()

    assert outcome["status"] == "succeeded"
    assert outcome["fact_count"] == 2
    assert service.state["job"]["result"]["retrieve"]["mode"] == "llamaindex"
    # Only Academics factors were sent to the model (DAT omitted from prompt).
    assert "avg_dat_aa" not in str(calls[0])
    assert "avg_gpa" in str(calls[0])
    assert len(calls) == 1
