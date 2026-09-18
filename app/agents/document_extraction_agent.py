"""Document extraction core used by the durable document worker."""
import asyncio
from dataclasses import dataclass
import hashlib

from openai import APIError

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
    def __init__(self, parser: DocumentParser, llm: LLMClient, taxonomy: FactorTaxonomy | None = None):
        self.parser = parser
        self.llm = llm
        self.taxonomy = taxonomy or get_taxonomy()

    async def extract(self, data: bytes, filename: str) -> DocumentExtraction:
        chunks = await asyncio.to_thread(self.parser.parse, data, filename)
        outcomes = []
        for chunk in chunks:
            try:
                extraction = await self.llm.extract(chunk, self.taxonomy)
                outcomes.append(ChunkOutcome(chunk, extraction, None))
            except (ExtractionFailed, APIError) as exc:
                # Keep valid chunks available; failures must never count as successful extraction.
                outcomes.append(ChunkOutcome(chunk, None, type(exc).__name__))
        return DocumentExtraction(hashlib.sha256(data).hexdigest(), self.taxonomy.content_hash, tuple(outcomes))
