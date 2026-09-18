"""Offline migration checks: real PostgreSQL SQL generation, without cloud access."""
from io import StringIO

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData

from app.config import ROOT
from app.db.models import Base


def test_upgrade_sql_is_additive_and_schema_scoped(settings):
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["settings"] = settings
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE school_ai_test.schools" in sql
    assert "CREATE TABLE school_ai_test.rubric_overrides" in sql
    assert "0002_domain_models" in sql
    assert "0003_page_fetch_cache" in sql
    assert "0004_rubric_status" in sql
    assert "0005_provider_usage" in sql
    assert "rubric_status" in sql
    assert "CREATE TABLE school_ai_test.page_fetch_cache" in sql
    assert "CREATE TABLE school_ai_test.provider_usage_events" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "DROP " not in sql
    assert "TRUNCATE TABLE" not in sql
    assert "DELETE FROM" not in sql
    assert "public." not in sql
    assert "REFERENCES school_ai_test.school_documents (school_id, id)" in sql
    assert "protect_override_rows BEFORE UPDATE OR DELETE" in sql
    assert "protect_override_truncate BEFORE TRUNCATE" in sql


def test_models_can_be_relocated_without_cross_schema_foreign_keys():
    isolated = MetaData(schema="school_ai_test_isolated")
    for table in Base.metadata.sorted_tables:
        table.to_metadata(isolated, schema=isolated.schema)
    assert len(isolated.tables) == 10
    for table in isolated.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.schema == isolated.schema
