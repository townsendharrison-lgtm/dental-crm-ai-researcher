"""Pydantic contracts for rubric inference writes and qualitative LLM output."""
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RubricWriteRejected(ValueError):
    pass


class QualitativeWeightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    weight_bucket: Literal["low", "medium", "high"]
    reasoning: str = Field(min_length=1, max_length=4000)
    source_urls: list[str] = Field(min_length=1, max_length=20)

    @field_validator("source_urls")
    @classmethod
    def nonempty_urls(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned or any(" " in item for item in cleaned):
            raise ValueError("source_urls must be nonempty without whitespace")
        return cleaned


# Bucket → relative weight before category normalization (mapped in code, not by the LLM).
QUALITATIVE_BUCKET_WEIGHTS = {
    "low": Decimal("0.05"),
    "medium": Decimal("0.15"),
    "high": Decimal("0.30"),
}


class RubricFactorDraft(BaseModel):
    """In-memory draft before persistence. Rejects ungrounded automated rows."""
    model_config = ConfigDict(extra="forbid", strict=True)
    factor_key: str = Field(min_length=1)
    value: Any | None = None
    weight: Decimal | None = None
    weight_source: Literal["stated", "cross_school_inferred", "qualitative_inferred", "manual_override"]
    confidence: Decimal = Field(ge=0, le=1)
    reasoning: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("source_urls")
    @classmethod
    def validate_urls(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and str(item).strip()]
        if any(" " in item for item in cleaned):
            raise ValueError("source_urls cannot contain whitespace")
        return cleaned

    @model_validator(mode="after")
    def enforce_grounding(self):
        if self.weight_source != "manual_override" and not self.source_urls:
            raise RubricWriteRejected("Automated rubric rows require at least one source_url")
        if self.value is None and self.weight is None and self.confidence != 0:
            raise RubricWriteRejected("Unknown value/weight requires confidence 0")
        return self


def qualitative_output_schema() -> dict:
    return QualitativeWeightResult.model_json_schema()
