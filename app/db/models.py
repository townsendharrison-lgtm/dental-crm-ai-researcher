"""Phase 1 persistence. Extraction, inference and scoring belong to later phases."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint, Date, DateTime, FetchedValue, ForeignKey, ForeignKeyConstraint,
    Index, Integer, MetaData, Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import get_settings

SCHEMA = get_settings().db_schema


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Updated:
    # A database trigger updates this even when callers use plain SQL.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=FetchedValue(),
    )


class School(Identity, Created, Updated, Base):
    __tablename__ = "schools"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="schools_name_nonempty"),
        CheckConstraint("official_url ~ '^https?://[^[:space:]]+$'", name="schools_url"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="schools_metadata_object"),
        CheckConstraint("ingestion_status IN ('pending','processing','complete','failed')", name="schools_status"),
        CheckConstraint("rubric_status IN ('draft','approved')", name="schools_rubric_status"),
    )
    name: Mapped[str] = mapped_column(Text)
    official_url: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    ingestion_status: Mapped[str] = mapped_column(String(20), server_default="pending")
    ingestion_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rubric_status: Mapped[str] = mapped_column(String(20), server_default="draft")
    rubric_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rubric_approved_by: Mapped[str | None] = mapped_column(Text)


class SchoolDocument(Identity, Created, Updated, Base):
    __tablename__ = "school_documents"
    __table_args__ = (
        UniqueConstraint("school_id", "content_hash", name="documents_school_hash_unique"),
        UniqueConstraint("school_id", "id", name="documents_school_id_unique"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="documents_sha256"),
        CheckConstraint("btrim(storage_bucket) <> '' AND btrim(storage_key) <> ''", name="documents_storage_nonempty"),
        CheckConstraint("source_type IN ('upload','web')", name="documents_source_type"),
        CheckConstraint("source_url IS NULL OR source_url ~ '^https?://[^[:space:]]+$'", name="documents_url"),
        CheckConstraint("source_type <> 'web' OR source_url IS NOT NULL", name="documents_web_source"),
        CheckConstraint("parsed_status IN ('pending','processing','complete','failed')", name="documents_status"),
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="documents_size"),
    )
    school_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    filename: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    storage_bucket: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text)
    parsed_status: Mapped[str] = mapped_column(String(20), server_default="pending")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class SchoolRawFact(Identity, Created, Base):
    __tablename__ = "school_raw_facts"
    __table_args__ = (
        ForeignKeyConstraint(["school_id", "document_id"],
                             [f"{SCHEMA}.school_documents.school_id", f"{SCHEMA}.school_documents.id"],
                             name="facts_document_same_school", ondelete="RESTRICT"),
        CheckConstraint("btrim(factor_key) <> ''", name="facts_factor_nonempty"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="facts_confidence"),
        CheckConstraint("value IS DISTINCT FROM 'null'::jsonb", name="facts_no_json_null"),
        CheckConstraint("value IS NOT NULL OR confidence = 0", name="facts_unknown_confidence"),
        CheckConstraint("source_type IN ('document','web')", name="facts_source_type"),
        CheckConstraint("(source_type = 'document' AND document_id IS NOT NULL) OR (source_type = 'web' AND source_url IS NOT NULL)", name="facts_source_required"),
        CheckConstraint("source_url IS NULL OR source_url ~ '^https?://[^[:space:]]+$'", name="facts_url"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="facts_page"),
        Index("ix_facts_school_factor", "school_id", "factor_key"),
    )
    school_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    factor_key: Mapped[str] = mapped_column(Text)
    value: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    unit: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))
    source_url: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[UUID | None]
    raw_text_snippet: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class RubricFactor(Identity, Created, Updated, Base):
    __tablename__ = "rubric_factors"
    __table_args__ = (
        UniqueConstraint("school_id", "factor_key", name="rubric_school_factor_unique"),
        CheckConstraint("btrim(factor_key) <> ''", name="rubric_factor_nonempty"),
        CheckConstraint("weight IS NULL OR (weight >= 0 AND weight < 'Infinity'::numeric)", name="rubric_weight_nonnegative"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="rubric_confidence"),
        CheckConstraint("weight_source IN ('stated','cross_school_inferred','qualitative_inferred','manual_override')", name="rubric_weight_source"),
        CheckConstraint("btrim(reasoning) <> ''", name="rubric_reasoning_required"),
        CheckConstraint("value IS DISTINCT FROM 'null'::jsonb", name="rubric_no_json_null"),
        CheckConstraint("(value IS NOT NULL OR weight IS NOT NULL) OR confidence = 0", name="rubric_unknown_confidence"),
        CheckConstraint("array_position(source_urls, NULL) IS NULL AND array_position(source_urls, '') IS NULL AND array_to_string(source_urls, '') !~ '[[:space:]]'", name="rubric_sources_valid"),
        CheckConstraint("weight_source = 'manual_override' OR cardinality(source_urls) > 0", name="rubric_grounding_required"),
    )
    school_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    factor_key: Mapped[str] = mapped_column(Text)
    value: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    weight_source: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    reasoning: Mapped[str] = mapped_column(Text)
    source_urls: Mapped[list[str]] = mapped_column(ARRAY(Text))


class RubricOverride(Identity, Created, Base):
    __tablename__ = "rubric_overrides"
    __table_args__ = (
        ForeignKeyConstraint(["school_id", "factor_key"],
                             [f"{SCHEMA}.rubric_factors.school_id", f"{SCHEMA}.rubric_factors.factor_key"],
                             name="overrides_rubric_factor", ondelete="RESTRICT"),
        CheckConstraint("btrim(editor) <> '' AND btrim(reason) <> ''", name="overrides_attribution"),
        CheckConstraint("jsonb_typeof(old_value) = 'object' AND jsonb_typeof(new_value) = 'object'", name="overrides_snapshots_objects"),
        Index("ix_overrides_school_factor", "school_id", "factor_key"),
    )
    school_id: Mapped[UUID]
    factor_key: Mapped[str] = mapped_column(Text)
    old_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    new_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    editor: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class CrossSchoolStat(Identity, Updated, Base):
    __tablename__ = "cross_school_stats"
    __table_args__ = (
        UniqueConstraint("factor_key", "unit", name="stats_factor_unit_unique"),
        CheckConstraint("btrim(factor_key) <> ''", name="stats_factor_nonempty"),
        CheckConstraint("sample_size > 0", name="stats_sample_size"),
        CheckConstraint("stddev_value >= 0", name="stats_stddev"),
        CheckConstraint("min_value <= mean_value AND mean_value <= max_value", name="stats_value_order"),
        CheckConstraint("jsonb_typeof(percentiles) = 'object'", name="stats_percentiles_object"),
        CheckConstraint("btrim(method) <> '' AND jsonb_typeof(provenance) = 'object' AND provenance <> '{}'::jsonb", name="stats_provenance"),
    )
    factor_key: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, server_default="")
    sample_size: Mapped[int] = mapped_column(Integer)
    min_value: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    max_value: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    mean_value: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    stddev_value: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    percentiles: Mapped[dict[str, Any]] = mapped_column(JSONB)
    method: Mapped[str] = mapped_column(Text)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScoringRun(Identity, Created, Base):
    __tablename__ = "scoring_runs"
    __table_args__ = (
        CheckConstraint("btrim(student_id) <> ''", name="scoring_student_nonempty"),
        CheckConstraint("jsonb_typeof(per_factor_breakdown) = 'object'", name="scoring_breakdown_object"),
        CheckConstraint("jsonb_typeof(rubric_snapshot) = 'object'", name="scoring_snapshot_object"),
        CheckConstraint("btrim(reasoning) <> ''", name="scoring_reasoning_required"),
        Index("ix_scoring_school_created", "school_id", "created_at"),
        Index("ix_scoring_student_created", "student_id", "created_at"),
    )
    school_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    student_id: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    per_factor_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rubric_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reasoning: Mapped[str] = mapped_column(Text)


class Job(Identity, Created, Updated, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("btrim(type) <> ''", name="jobs_type_nonempty"),
        CheckConstraint("status IN ('pending','running','succeeded','failed','cancelled')", name="jobs_status"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="jobs_payload_object"),
        CheckConstraint("attempts >= 0", name="jobs_attempts"),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="jobs_failure_error"),
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_school", "school_id"),
    )
    type: Mapped[str] = mapped_column(Text)
    school_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderUsageEvent(Identity, Created, Base):
    """Append-only LLM/search metering. Never updated or deleted by the service."""
    __tablename__ = "provider_usage_events"
    __table_args__ = (
        CheckConstraint("service IN ('openai','tavily')", name="usage_service"),
        CheckConstraint("btrim(operation) <> ''", name="usage_operation_nonempty"),
        CheckConstraint("tokens IS NULL OR tokens >= 0", name="usage_tokens_nonneg"),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="usage_cost_nonneg"),
        Index("ix_usage_day_school_service", "usage_day", "school_id", "service"),
        Index("ix_usage_school_created", "school_id", "created_at"),
    )
    school_id: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.schools.id", ondelete="RESTRICT"))
    service: Mapped[str] = mapped_column(String(20))
    operation: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    latency_ms: Mapped[float | None] = mapped_column(Numeric(24, 4))
    usage_day: Mapped[date] = mapped_column(Date, server_default=func.current_date())


class PageFetchCache(Base):
    """TTL cache for allow-listed web pages. Rows are upserted, never deleted by the service."""
    __tablename__ = "page_fetch_cache"
    __table_args__ = (
        CheckConstraint("url_hash ~ '^[0-9a-f]{64}$'", name="page_cache_sha256"),
        CheckConstraint("url ~ '^https?://[^[:space:]]+$'", name="page_cache_url"),
        CheckConstraint("btrim(content_text) <> ''", name="page_cache_text"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="page_cache_content_hash"),
        CheckConstraint("fetch_method IN ('httpx','playwright')", name="page_cache_method"),
        CheckConstraint("byte_size >= 0", name="page_cache_size"),
    )
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    fetch_method: Mapped[str] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    byte_size: Mapped[int] = mapped_column(Integer, server_default="0")
