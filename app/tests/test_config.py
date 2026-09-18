import pytest
from pathlib import Path
from pydantic import ValidationError

from app.config import Settings
from app.db.session import make_engine


@pytest.mark.parametrize("schema", ["public", "auth", "storage", "pgmq", "pg_catalog", 'bad";DROP TABLE schools;--'])
def test_service_schema_cannot_collide_with_crm_or_inject_sql(schema):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, db_schema=schema)


@pytest.mark.parametrize("queue", ["bad-name", 'q";--', "a" * 48])
def test_queue_identifiers_are_validated(queue):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, queue_name=queue)


def test_secrets_are_not_in_repr(settings):
    assert "fixture-secret-key" not in repr(settings)
    assert "fixture-not-a-real-key" not in repr(settings)


@pytest.mark.parametrize("url", ["sqlite:///file.db", "postgresql://fixture:password@host:6543/db", "not-a-url"])
def test_invalid_or_transaction_pooler_url_is_rejected(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


def test_dotenv_loads_without_other_apps(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=fixture-key\nQUEUE_NAME=fixture_queue\n", encoding="utf-8")
    settings = Settings(_env_file=path)
    assert settings.openai_api_key.get_secret_value() == "fixture-key"
    assert settings.queue_name == "fixture_queue"


async def test_database_engine_uses_asyncpg_and_hides_parameters(settings):
    engine = make_engine(settings)
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.sync_engine.hide_parameters is True
    await engine.dispose()


def test_resolve_database_ca_falls_back_to_bundled_cert(settings):
    from app.db.session import resolve_database_ca_file
    from app.config import ROOT

    settings = settings.model_copy(update={"database_ca_file": ""})
    resolved = resolve_database_ca_file(settings)
    assert resolved is not None
    assert Path(resolved).is_file()
    # Prefer the bundled Supabase CA when present.
    bundled = ROOT / "certs" / "prod-ca-2021.crt"
    if bundled.is_file():
        assert Path(resolved) == bundled


def test_gpt4o_is_the_configured_model(settings):
    assert settings.openai_model == "gpt-4o"
    assert settings.storage_configured is True


@pytest.mark.parametrize("url", ["http://project.supabase.co", "https://secret@project.supabase.co", "https://project.supabase.co/storage/v1"])
def test_supabase_requires_project_url_without_embedded_credentials(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, supabase_url=url)
