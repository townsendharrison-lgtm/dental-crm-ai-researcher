"""Document extraction core used by the durable document worker."""
import asyncio
from dataclasses import dataclass
import hashlib

from openai import APIError

from app.clients.chunk_retriever import assign_chunks_by_category, taxonomy_for_categories
from app.clients.fetch_client import DocumentChunk, DocumentParser
from app.clients.llm_client import LLMClient, ExtractionFailed, LLMExtraction
from app.factor_taxonomy import FactorTaxonomy, get_taxonomy


@dataclass(frozen=True)
class ChunkOutcome:
    chunk: DocumentChunk
    extraction: LLMExtraction | None
    error_type: str | None


@dataclass(frozen=True)
class DocumentExtraction:
    content_hash: str
    taxonomy_hash: str
    chunks: tuple[ChunkOutcome, ...]

    @property
    def complete(self):
        return all(item.error_type is None for item in self.chunks)


class DocumentExtractionAgent:
    def __init__(self, parser: DocumentParser, llm: LLMClient, taxonomy: FactorTaxonomy | None = None,
                 *, retriever=None):
        self.parser = parser
        self.llm = llm
        self.taxonomy = taxonomy or get_taxonomy()
        self.retriever = retriever or assign_chunks_by_category

    async def extract(self, data: bytes, filename: str) -> DocumentExtraction:
        chunks = await asyncio.to_thread(self.parser.parse, data, filename)
        settings = self.llm.settings
        if settings.document_extract_mode == "retrieve":
            return await self._extract_retrieve(data, chunks)
        return await self._extract_chunk(data, chunks)

    async def _extract_chunk(self, data: bytes, chunks: list[DocumentChunk]) -> DocumentExtraction:
        outcomes = []
        for chunk in chunks:
            try:
                extraction = await self.llm.extract(chunk, self.taxonomy)
                outcomes.append(ChunkOutcome(chunk, extraction, None))
            except (ExtractionFailed, APIError) as exc:
                # Keep valid chunks available; failures must never count as successful extraction.
                outcomes.append(ChunkOutcome(chunk, None, type(exc).__name__))
        return DocumentExtraction(hashlib.sha256(data).hexdigest(), self.taxonomy.content_hash, tuple(outcomes))

    async def _extract_retrieve(self, data: bytes, chunks: list[DocumentChunk]) -> DocumentExtraction:
        settings = self.llm.settings
        min_chars = settings.document_min_chunk_chars
        pending = []
        outcomes: dict[int, ChunkOutcome] = {}
        for chunk in chunks:
            if len("".join(chunk.text.split())) < min_chars:
                outcomes[chunk.index] = ChunkOutcome(chunk, None, None)
                continue
            pending.append(chunk)

        assignments = await asyncio.to_thread(
            self.retriever,
            pending,
            self.taxonomy,
            api_key=settings.openai_api_key.get_secret_value(),
            embed_model_name=settings.openai_embed_model,
            top_k=settings.document_retrieve_top_k,
        )
        selected = {item.chunk_index: item.categories for item in assignments}

        for chunk in pending:
            categories = selected.get(chunk.index)
            if not categories:
                outcomes[chunk.index] = ChunkOutcome(chunk, None, None)
                continue
            try:
                subset = taxonomy_for_categories(self.taxonomy, categories)
                extraction = await self.llm.extract(chunk, subset)
                outcomes[chunk.index] = ChunkOutcome(chunk, extraction, None)
            except (ExtractionFailed, APIError) as exc:
                outcomes[chunk.index] = ChunkOutcome(chunk, None, type(exc).__name__)

        ordered = tuple(outcomes[chunk.index] for chunk in chunks)
        return DocumentExtraction(hashlib.sha256(data).hexdigest(), self.taxonomy.content_hash, ordered)
