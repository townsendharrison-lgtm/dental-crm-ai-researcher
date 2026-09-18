import hashlib
import json

import httpx
import pytest

from app.clients.storage_client import StorageClient, StorageError


def base(settings):
    return f"{settings.supabase_url}/storage/v1"


async def test_upload_download_and_cached_upload(settings, respx_mock):
    storage = StorageClient(settings)
    content = b"synthetic Phase 0 test document"
    key = "documents/" + hashlib.sha256(content).hexdigest()
    bucket = settings.supabase_storage_bucket
    info = respx_mock.get(f"{base(settings)}/object/info/{bucket}/{key}")
    info.side_effect = [httpx.Response(404, json={"code": "NoSuchKey"}), httpx.Response(200, json={"name": key})]
    upload = respx_mock.post(f"{base(settings)}/object/{bucket}/{key}").respond(200, json={"Key": f"{bucket}/{key}"})
    download = respx_mock.get(f"{base(settings)}/object/authenticated/{bucket}/{key}").respond(200, content=content)
    try:
        assert await storage.upload(content) == key
        assert await storage.download(key) == content
        assert await storage.upload(content) == key
        assert upload.call_count == 1
        assert info.call_count == 2
        assert download.call_count == 1
        request = upload.calls.last.request
        assert request.content == content
        assert request.headers["apikey"] == "fixture-secret-key"
        assert request.headers["Authorization"] == "Bearer fixture-secret-key"
        assert request.headers["x-upsert"] == "true"
        assert request.headers["Content-Type"] == "application/octet-stream"
    finally:
        await storage.close()


async def test_force_refresh_reuploads_without_info(settings, respx_mock):
    storage = StorageClient(settings)
    content = b"force"
    key = "documents/" + hashlib.sha256(content).hexdigest()
    upload = respx_mock.post(f"{base(settings)}/object/{settings.supabase_storage_bucket}/{key}").respond(200, json={})
    try:
        assert await storage.upload(content, force_refresh=True) == key
        assert upload.call_count == 1
    finally:
        await storage.close()


@pytest.mark.parametrize("status,code", [(403, "AccessDenied"), (404, "NoSuchBucket"), (400, "InvalidRequest")])
async def test_storage_errors_are_not_treated_as_missing_objects(settings, respx_mock, status, code):
    storage = StorageClient(settings)
    key = "documents/" + hashlib.sha256(b"test").hexdigest()
    route = respx_mock.get(f"{base(settings)}/object/info/{settings.supabase_storage_bucket}/{key}").respond(status, json={"code": code, "message": "secret-provider-message"})
    try:
        with pytest.raises(StorageError) as caught:
            await storage.upload(b"test")
        assert route.call_count == 1
        assert "secret-provider-message" not in str(caught.value)
    finally:
        await storage.close()


async def test_legacy_object_not_found_allows_upload(settings, respx_mock):
    storage = StorageClient(settings)
    key = "documents/" + hashlib.sha256(b"test").hexdigest()
    respx_mock.get(f"{base(settings)}/object/info/{settings.supabase_storage_bucket}/{key}").respond(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"})
    upload = respx_mock.post(f"{base(settings)}/object/{settings.supabase_storage_bucket}/{key}").respond(200, json={})
    try:
        assert await storage.upload(b"test") == key
        assert upload.call_count == 1
    finally:
        await storage.close()


async def test_storage_health_checks_private_bucket(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(200, json={"public": False})
    try:
        await storage.health()
    finally:
        await storage.close()


async def test_public_bucket_is_not_healthy(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(200, json={"public": True})
    try:
        with pytest.raises(RuntimeError, match="private"):
            await storage.health()
    finally:
        await storage.close()


async def test_temporary_failure_retries(settings, respx_mock):
    storage = StorageClient(settings)
    route = respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={"public": False})]
    try:
        await storage.health()
        assert route.call_count == 2
    finally:
        await storage.close()


async def test_delete_targets_only_requested_object(settings, respx_mock):
    storage = StorageClient(settings)
    route = respx_mock.delete(f"{base(settings)}/object/{settings.supabase_storage_bucket}").respond(200, json=[])
    try:
        await storage.delete("documents/fixture")
        assert json.loads(route.calls.last.request.content) == {"prefixes": ["documents/fixture"]}
    finally:
        await storage.close()


@pytest.mark.parametrize("key", ["../other", "/other", "documents/../other", "documents//other"])
async def test_object_paths_cannot_escape_bucket(settings, key):
    storage = StorageClient(settings)
    try:
        with pytest.raises(ValueError):
            await storage.download(key)
    finally:
        await storage.close()


async def test_missing_bucket_is_created_private_and_rechecked(settings, respx_mock):
    storage = StorageClient(settings)
    bucket = settings.supabase_storage_bucket
    health = respx_mock.get(f"{base(settings)}/bucket/{bucket}")
    health.side_effect = [httpx.Response(400, json={"code": "NoSuchBucket"}), httpx.Response(200, json={"public": False})]
    create = respx_mock.post(f"{base(settings)}/bucket").respond(200, json={"name": bucket})
    try:
        await storage.ensure_bucket()
        assert json.loads(create.calls.last.request.content) == {"id": bucket, "name": bucket, "public": False}
        assert health.call_count == 2
    finally:
        await storage.close()


async def test_existing_private_bucket_is_not_modified(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(200, json={"public": False})
    try:
        await storage.ensure_bucket()
        assert len(respx_mock.calls) == 1
    finally:
        await storage.close()


async def test_bootstrap_refuses_to_change_existing_public_bucket(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(200, json={"public": True})
    try:
        with pytest.raises(RuntimeError, match="private"):
            await storage.ensure_bucket()
        assert len(respx_mock.calls) == 1
    finally:
        await storage.close()


async def test_bootstrap_does_not_create_after_access_denied(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(403, json={"code": "AccessDenied"})
    try:
        with pytest.raises(StorageError):
            await storage.ensure_bucket()
        assert len(respx_mock.calls) == 1
    finally:
        await storage.close()


async def test_duplicate_bucket_creation_is_reconciled(settings, respx_mock):
    storage = StorageClient(settings)
    health = respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}")
    health.side_effect = [httpx.Response(400, json={"code": "NoSuchBucket"}), httpx.Response(200, json={"public": False})]
    create = respx_mock.post(f"{base(settings)}/bucket").respond(409, json={"code": "ResourceAlreadyExists"})
    try:
        await storage.ensure_bucket()
        assert health.call_count == 2
        assert create.call_count == 1
    finally:
        await storage.close()


async def test_create_bucket_failure_is_not_swallowed(settings, respx_mock):
    storage = StorageClient(settings)
    respx_mock.get(f"{base(settings)}/bucket/{settings.supabase_storage_bucket}").respond(400, json={"code": "NoSuchBucket"})
    respx_mock.post(f"{base(settings)}/bucket").respond(403, json={"code": "AccessDenied"})
    try:
        with pytest.raises(StorageError):
            await storage.ensure_bucket()
    finally:
        await storage.close()
