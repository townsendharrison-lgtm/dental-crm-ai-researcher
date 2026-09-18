"""Strict extraction output and deterministic citation/value validation."""
from decimal import Decimal
import re

from pydantic import BaseModel, ConfigDict, Field

from app.clients.fetch_client import DocumentChunk, normalize_text
from app.factor_taxonomy import FactorTaxonomy


class GroundingError(ValueError):
    pass


class ExtractedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    factor_key: str
    value: float | str | None
    unit: str | None
    raw_text_snippet: str = Field(min_length=1, max_length=2000)
    page_number: int | None
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    facts: list[ExtractedFact] = Field(max_length=100)


def output_schema(taxonomy: FactorTaxonomy) -> dict:
    schema = ExtractionResult.model_json_schema()
    schema["$defs"]["ExtractedFact"]["properties"]["factor_key"]["enum"] = list(taxonomy.by_key)
    return schema


def validate_result(content: str, chunk: DocumentChunk, taxonomy: FactorTaxonomy) -> ExtractionResult:
    result = ExtractionResult.model_validate_json(content)
    known = taxonomy.by_key
    for fact in result.facts:
        definition = known.get(fact.factor_key)
        if definition is None:
            raise GroundingError("Unknown taxonomy factor")
        snippet = normalize_text(fact.raw_text_snippet)
        if not snippet or snippet not in normalize_text(chunk.text):
            raise GroundingError("Evidence excerpt is not present in this chunk")
        if fact.page_number != chunk.page_number:
            raise GroundingError("Evidence page does not match the parsed page")
        if fact.unit != definition.unit:
            raise GroundingError("Unit differs from the approved factor definition")
        if fact.value is None:
            if fact.confidence != 0:
                raise GroundingError("An unknown value must have confidence zero")
        elif definition.value_type == "number":
            if not isinstance(fact.value, float):
                raise GroundingError("Numeric factor requires a numeric value")
            numbers = {Decimal(match.replace(",", "")) for match in
                       re.findall(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?(?!\w|\.\d)", snippet)}
            if Decimal(str(fact.value)) not in numbers:
                raise GroundingError("Numeric value is absent from its cited excerpt")
        elif not isinstance(fact.value, str) or not normalize_text(fact.value) or normalize_text(fact.value) not in snippet:
            raise GroundingError("Text value must quote the cited evidence")
    return result
