"""LlamaIndex retrieval: pick only taxonomy-relevant chunks before LLM extraction.

Instead of calling GPT-4o on every chunk with the full 131-factor taxonomy, we:
1. Embed parsed chunks once (cheap embedding model).
2. For each taxonomy category, retrieve the top-k similar chunks.
3. Extract each selected chunk once against only the categories that matched it.

That cuts both call count and prompt tokens — the main latency/TPM bottleneck.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from llama_index.core import Document, Settings as LlamaSettings, VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding

from app.clients.fetch_client import DocumentChunk
from app.factor_taxonomy import FactorDefinition, FactorTaxonomy


@dataclass(frozen=True)
class ChunkAssignment:
    """Which taxonomy categories should be extracted from a given chunk."""
    chunk_index: int
    categories: tuple[str, ...]


def taxonomy_for_categories(taxonomy: FactorTaxonomy, categories: tuple[str, ...] | list[str]) -> FactorTaxonomy:
    """Build a subset taxonomy containing only the requested categories."""
    wanted = tuple(dict.fromkeys(categories))
    factors = tuple(f for f in taxonomy.factors if f.category in wanted)
    if not factors:
        raise ValueError("No factors match the requested categories")
    present = tuple(c for c in taxonomy.categories if c in wanted)
    return FactorTaxonomy(version=taxonomy.version, categories=present, factors=factors)


def _category_query(category: str, factors: tuple[FactorDefinition, ...]) -> str:
    lines = [category, *[f"{factor.key}: {factor.description}" for factor in factors]]
    return "\n".join(lines)


def _default_embed_model(api_key: str, model: str) -> BaseEmbedding:
    return OpenAIEmbedding(model=model, api_key=api_key)


def assign_chunks_by_category(
    chunks: list[DocumentChunk],
    taxonomy: FactorTaxonomy,
    *,
    api_key: str,
    embed_model_name: str = "text-embedding-3-small",
    top_k: int = 3,
    embed_model: BaseEmbedding | None = None,
) -> list[ChunkAssignment]:
    """Return per-chunk category assignments via LlamaIndex vector retrieval.

    Chunks that never rank in any category's top-k are omitted (caller should skip them).
    """
    eligible = [c for c in chunks if c.text and c.text.strip()]
    if not eligible:
        return []

    model = embed_model or _default_embed_model(api_key, embed_model_name)
    # Isolate from any process-global LlamaIndex defaults.
    LlamaSettings.embed_model = model

    documents = [
        Document(
            text=chunk.text,
            metadata={"chunk_index": chunk.index, "page_number": chunk.page_number or -1},
            excluded_embed_metadata_keys=["chunk_index", "page_number"],
            excluded_llm_metadata_keys=["chunk_index", "page_number"],
        )
        for chunk in eligible
    ]
    index = VectorStoreIndex.from_documents(documents, embed_model=model)
    k = max(1, min(top_k, len(documents)))

    by_chunk: dict[int, set[str]] = defaultdict(set)
    factors_by_category: dict[str, tuple[FactorDefinition, ...]] = {
        category: tuple(f for f in taxonomy.factors if f.category == category)
        for category in taxonomy.categories
    }

    for category, factors in factors_by_category.items():
        if not factors:
            continue
        retriever = index.as_retriever(similarity_top_k=k)
        nodes = retriever.retrieve(_category_query(category, factors))
        for node in nodes:
            raw = node.node.metadata.get("chunk_index")
            if raw is None:
                continue
            by_chunk[int(raw)].add(category)

    # Preserve original chunk order for deterministic worker progress.
    order = {chunk.index: position for position, chunk in enumerate(chunks)}
    return [
        ChunkAssignment(chunk_index=index, categories=tuple(c for c in taxonomy.categories if c in cats))
        for index, cats in sorted(by_chunk.items(), key=lambda item: order.get(item[0], item[0]))
        if cats
    ]
