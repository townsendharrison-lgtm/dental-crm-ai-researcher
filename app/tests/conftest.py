import pytest

from app.clients.usage_guard import reset_usage_guard_for_tests
from app.config import Settings


@pytest.fixture(autouse=True)
def _reset_usage_guard():
    reset_usage_guard_for_tests()
    yield
    reset_usage_guard_for_tests()


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql://fixture:fixture@localhost/fixture",
        supabase_url="https://fixture.supabase.invalid",
        supabase_service_role_key="fixture-secret-key", supabase_storage_bucket="fixture-bucket",
        openai_api_key="fixture-not-a-real-key", tavily_api_key="fixture-not-a-real-key",
        queue_name="school_ai_test", db_schema="school_ai_test", external_backoff_seconds=0,
        # Offline tests mock chat completions, not embeddings — use legacy per-chunk path.
        document_extract_mode="chunk",
    )
