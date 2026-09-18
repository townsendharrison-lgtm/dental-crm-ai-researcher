import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bootstrap import bootstrap
from app.smoke import check_storage


async def test_storage_only_bootstrap_does_not_need_database(settings, monkeypatch):
    database = SimpleNamespace(execute=AsyncMock(), close=AsyncMock())
    storage = SimpleNamespace(ensure_bucket=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr("app.bootstrap.get_settings", lambda: settings)
    monkeypatch.setattr("app.bootstrap.Database", lambda _: database)
    monkeypatch.setattr("app.bootstrap.StorageClient", lambda _: storage)
    await bootstrap(storage_only=True)
    storage.ensure_bucket.assert_awaited_once()
    database.execute.assert_not_awaited()
    database.close.assert_awaited_once()
    storage.close.assert_awaited_once()


async def test_queue_only_bootstrap_does_not_provision_storage(settings, monkeypatch):
    database = SimpleNamespace(execute=AsyncMock(), close=AsyncMock())
    storage = SimpleNamespace(ensure_bucket=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr("app.bootstrap.get_settings", lambda: settings)
    monkeypatch.setattr("app.bootstrap.Database", lambda _: database)
    monkeypatch.setattr("app.bootstrap.StorageClient", lambda _: storage)
    await bootstrap(queue_only=True)
    storage.ensure_bucket.assert_not_awaited()
    assert database.execute.await_count == 4
    operations = [call.args[0] for call in database.execute.await_args_list]
    assert operations == ["enable_pgmq", "create_queue", "create_document_queue", "create_research_queue"]


def fake_storage():
    content = {}
    async def upload(data, *, prefix):
        key = f"{prefix}/{hashlib.sha256(data).hexdigest()}"
        content[key] = data
        return key
    return SimpleNamespace(health=AsyncMock(), upload=AsyncMock(side_effect=upload),
                           download=AsyncMock(side_effect=lambda key: content[key]),
                           delete=AsyncMock(side_effect=lambda key: content.pop(key, None)),
                           exists=AsyncMock(side_effect=lambda key: key in content))


async def test_storage_smoke_checks_round_trip_cache_and_cleanup():
    storage = fake_storage()
    assert await check_storage(storage) == {
        "storage_round_trip": "passed", "storage_cache": "passed", "storage_cleanup": "passed",
    }
    assert storage.upload.await_count == 2
    storage.delete.assert_awaited_once()
    storage.exists.assert_awaited_once()


async def test_storage_smoke_cleans_up_after_corrupt_download():
    storage = fake_storage()
    storage.download.side_effect = lambda _: b"wrong"
    with pytest.raises(RuntimeError, match="round trip"):
        await check_storage(storage)
    storage.delete.assert_awaited_once()


async def test_storage_smoke_does_not_claim_cleanup_when_object_remains():
    storage = fake_storage()
    storage.exists.side_effect = lambda _: True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await check_storage(storage)
