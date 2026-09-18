"""Real migrations and persistence in an empty, transaction-isolated schema.

No DROP/TRUNCATE/DELETE cleanup is used. The outer transaction is always rolled
back, removing only this test's uncommitted schema and synthetic data.
"""
from decimal import Decimal
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import pytest
import pytest_asyncio
from sqlalchemy import MetaData, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ROOT, Settings
from app.db.models import (
    Base, SCHEMA, School, SchoolDocument, SchoolRawFact, RubricFactor,
    RubricOverride, CrossSchoolStat, ScoringRun, Job,
)
from app.db.session import make_engine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def isolated_database():
    settings = Settings()
    if not settings.run_integration_tests:
        pytest.skip("Set RUN_INTEGRATION_TESTS=true for transaction-isolated hosted model tests")
    schema = "school_ai_test_" + uuid4().hex
    isolated_settings = settings.model_copy(update={"db_schema": schema})
    engine = make_engine(settings)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                assert await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": schema}) is None
                config = Config(str(ROOT / "alembic.ini"))
                config.attributes["settings"] = isolated_settings

                def migrate(sync_connection):
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(migrate)
                assert transaction.is_active
                assert await connection.scalar(text(f'SELECT version_num FROM "{schema}".alembic_version')) == "0004_rubric_status"
                assert await connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 1
                await connection.execution_options(schema_translate_map={SCHEMA: schema})
                yield connection, schema
            finally:
                await transaction.rollback()
                await connection.execution_options(schema_translate_map=None)
            # Verify rollback cleanup; never drop an existing schema or table.
            assert await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": schema}) is None
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def session(isolated_database):
    connection, _ = isolated_database
    savepoint = await connection.begin_nested()
    try:
        async with AsyncSession(bind=connection, expire_on_commit=False,
                                join_transaction_mode="create_savepoint") as session:
            yield session
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def seed_school(session):
    school = School(name="Synthetic Phase 1 School", official_url="https://fixture.invalid/admissions",
                    details={"synthetic": True})
    session.add(school)
    await session.flush()
    return school


async def seed_document(session, school):
    document = SchoolDocument(school_id=school.id, filename="fixture.pdf", media_type="application/pdf",
                              storage_bucket="synthetic-not-uploaded", storage_key="fixture.pdf",
                              content_hash="a" * 64, source_type="upload")
    session.add(document)
    await session.flush()
    return document


def factor(school, **changes):
    values = dict(school_id=school.id, factor_key="fixture.gpa", value=3.5, weight=Decimal("0.25"),
                  weight_source="stated", confidence=Decimal("0.9"),
                  reasoning="Synthetic fixture explicitly states this weight.",
                  source_urls=["https://fixture.invalid/admissions"])
    values.update(changes)
    return RubricFactor(**values)


async def test_full_chain_and_all_eight_tables_round_trip(session):
    school = await seed_school(session)
    document = await seed_document(session, school)
    raw = SchoolRawFact(school_id=school.id, factor_key="fixture.gpa", value=3.5, unit="gpa",
                        source_type="document", document_id=document.id, page_number=2,
                        raw_text_snippet="Synthetic GPA 3.5", confidence=Decimal("0.9"))
    rubric = factor(school)
    session.add_all([raw, rubric])
    await session.flush()
    override = RubricOverride(school_id=school.id, factor_key=rubric.factor_key,
                              old_value={"value": 3.5, "weight": 0.25},
                              new_value={"value": 3.6, "weight": 0.25},
                              editor="fixture-admin", reason="Synthetic audit test")
    stats = CrossSchoolStat(factor_key="fixture.gpa", unit="gpa", sample_size=1,
                            min_value=Decimal("3.5"), max_value=Decimal("3.5"),
                            mean_value=Decimal("3.5"), stddev_value=0, percentiles={"50": 3.5},
                            method="synthetic-single-value", provenance={"raw_fact_ids": [str(raw.id)]})
    scoring = ScoringRun(school_id=school.id, student_id="external-fixture-student",
                         score=Decimal("0.25"), per_factor_breakdown={"fixture.gpa": {"contribution": 0.25}},
                         rubric_snapshot={"fixture.gpa": {"value": 3.5, "weight": 0.25}},
                         reasoning="Synthetic persistence test; no scoring algorithm called.")
    job = Job(type="fixture", school_id=school.id, payload={"document_id": str(document.id)})
    session.add_all([override, stats, scoring, job])
    await session.commit()  # Releases the session savepoint, never the outer test transaction.
    session.expunge_all()
    loaded = (await session.execute(
        select(School, SchoolRawFact, RubricFactor, ScoringRun)
        .join(SchoolRawFact, SchoolRawFact.school_id == School.id)
        .join(RubricFactor, RubricFactor.school_id == School.id)
        .join(ScoringRun, ScoringRun.school_id == School.id)
        .where(School.id == school.id)
    )).one()
    assert loaded[0].details == {"synthetic": True}
    assert loaded[1].document_id == document.id and loaded[1].value == 3.5
    assert loaded[2].weight == Decimal("0.25")
    assert loaded[3].student_id == "external-fixture-student"
    assert loaded[3].per_factor_breakdown["fixture.gpa"]["contribution"] == 0.25
    assert (await session.get(SchoolDocument, document.id)).parsed_status == "pending"
    assert (await session.get(RubricOverride, override.id)).new_value["value"] == 3.6
    assert (await session.get(CrossSchoolStat, stats.id)).mean_value == Decimal("3.5")
    assert (await session.get(Job, job.id)).status == "pending"
    assert loaded[0].created_at.tzinfo is not None


@pytest.mark.parametrize("changes", [
    {"confidence": Decimal("1.1")}, {"confidence": Decimal("-0.1")},
    {"confidence": None}, {"weight": Decimal("-0.1")}, {"weight": Decimal("NaN")},
    {"weight_source": "invented"}, {"source_urls": []}, {"source_urls": [""]},
    {"source_urls": [" "]}, {"source_urls": [None]}, {"source_urls": None},
    {"reasoning": " "}, {"value": None, "weight": None, "confidence": Decimal("0.8")},
])
async def test_invalid_rubric_is_rejected_by_postgres(session, changes):
    school = await seed_school(session)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(factor(school, **changes))
            await session.flush()


async def test_unknown_value_is_null_with_zero_confidence(session):
    school = await seed_school(session)
    unknown = factor(school, value=None, weight=None, confidence=0,
                     reasoning="Inspected source does not state this factor.")
    session.add(unknown)
    await session.flush()
    assert await session.scalar(select(RubricFactor.value.is_(None)).where(RubricFactor.id == unknown.id)) is True


async def test_document_deduplication_and_school_boundaries(session):
    school = await seed_school(session)
    document = await seed_document(session, school)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await seed_document(session, school)
    other = await seed_school(session)
    await seed_document(session, other)  # Same hash at another school is legitimate.
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(SchoolRawFact(school_id=other.id, factor_key="fixture.gpa", value=3.5,
                                     source_type="document", document_id=document.id, confidence=1))
            await session.flush()


@pytest.mark.parametrize("changes", [
    {"source_type": "document"}, {"source_type": "web", "source_url": None},
    {"confidence": 2}, {"value": None, "confidence": 1}, {"page_number": 0},
])
async def test_raw_facts_require_grounding_and_valid_confidence(session, changes):
    school = await seed_school(session)
    values = dict(school_id=school.id, factor_key="fixture.gpa", value=3.5,
                  source_type="web", source_url="https://fixture.invalid/admissions", confidence=1)
    values.update(changes)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(SchoolRawFact(**values))
            await session.flush()


async def test_factor_is_unique_and_audit_history_cannot_be_rewritten(session):
    school = await seed_school(session)
    rubric = factor(school)
    session.add(rubric)
    await session.flush()
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(factor(school))
            await session.flush()
    audit = RubricOverride(school_id=school.id, factor_key=rubric.factor_key,
                           old_value={"value": 3.5}, new_value={"value": 3.6},
                           editor="fixture-admin", reason="Synthetic correction")
    session.add(audit)
    await session.flush()
    with pytest.raises(IntegrityError, match="append-only"):
        async with session.begin_nested():
            await session.execute(update(RubricOverride).where(RubricOverride.id == audit.id).values(reason="Rewritten"))
    assert (await session.get(RubricOverride, audit.id)).reason == "Synthetic correction"


async def test_updates_refresh_timestamp_in_database(session):
    school = await seed_school(session)
    original = school.updated_at
    await session.execute(update(School).where(School.id == school.id).values(name="Synthetic changed name"))
    await session.refresh(school)
    assert school.updated_at > original


async def test_migration_matches_models(isolated_database):
    connection, schema = isolated_database
    metadata = MetaData(schema=schema)
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata, schema=schema)

    def compare(sync_connection):
        context = MigrationContext.configure(sync_connection, opts={
            "include_schemas": True,
            "include_name": lambda name, kind, parents: name == schema if kind == "schema" else True,
            "version_table_schema": schema,
            "compare_server_default": True,
        })
        return compare_metadata(context, metadata)

    assert await connection.run_sync(compare) == []
