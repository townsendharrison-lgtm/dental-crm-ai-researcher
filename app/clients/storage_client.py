"""Native Supabase Storage API; no S3 credentials or boto3."""
import hashlib
from urllib.parse import quote

import httpx

from app.clients.operations import external_call
from app.config import Settings
from app.db.session import ConfigurationMissing


class StorageError(RuntimeError):
    def __init__(self, status_code: int, code: str = ""):
        # Raw provider messages may contain object paths or sensitive details.
        super().__init__(f"Supabase Storage request failed (HTTP {status_code})")
        self.status_code = status_code
        self.code = code


class StorageClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    @property
    def client(self):
        if not self.settings.storage_configured:
            raise ConfigurationMissing("Supabase Storage configuration is incomplete")
        if self._client is None:
            key = self.settings.supabase_service_role_key.get_secret_value()
            self._client = httpx.AsyncClient(
                base_url=f"{self.settings.supabase_url}/storage/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=self.settings.external_timeout_seconds, follow_redirects=False,
            )
        return self._client

    @property
    def bucket(self) -> str:
        return quote(self.settings.supabase_storage_bucket, safe="")

    @staticmethod
    def object_path(key: str) -> str:
        if not key or chr(92) in key or any(part in {"", ".", ".."} for part in key.split("/")):
            raise ValueError("Use a relative object key without traversal or empty segments")
        return quote(key, safe="/")

    @staticmethod
    def retryable(exc: Exception) -> bool:
        return isinstance(exc, httpx.TransportError) or (
            isinstance(exc, StorageError) and exc.status_code in {429, 500, 502, 503, 504}
        )

    async def _request(self, operation: str, method: str, path: str, **kwargs) -> httpx.Response:
        async def request():
            response = await self.client.request(method, path, **kwargs)
            if not response.is_success:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                code = str(body.get("code") or body.get("error") or "") if isinstance(body, dict) else ""
                # Legacy Storage errors can use HTTP 400 with statusCode=404.
                if isinstance(body, dict) and str(body.get("statusCode")) == "404" and body.get("message") == "Object not found":
                    code = "NoSuchKey"
                raise StorageError(response.status_code, code)
            return response
        return await external_call(
            "supabase_storage", operation, request,
            attempts=self.settings.external_max_attempts, backoff=self.settings.external_backoff_seconds,
            retryable=self.retryable,
        )

    async def health(self):
        response = await self._request("get_bucket", "GET", f"bucket/{self.bucket}")
        if response.json().get("public") is not False:
            raise RuntimeError("Configure a private Supabase Storage bucket")

    async def ensure_bucket(self):
        """Create only the configured missing private bucket; never change an existing policy."""
        try:
            await self.health()
            return
        except StorageError as exc:
            if exc.code != "NoSuchBucket":
                raise
        try:
            await self._request("create_bucket", "POST", "bucket", json={
                "id": self.settings.supabase_storage_bucket,
                "name": self.settings.supabase_storage_bucket,
                "public": False,
            })
        except StorageError as exc:
            # Another bootstrap (or a lost success response) may have created it.
            if exc.status_code != 409 and exc.code not in {"Duplicate", "BucketAlreadyExists", "ResourceAlreadyExists"}:
                raise
        await self.health()

    async def exists(self, key: str) -> bool:
        try:
            await self._request("object_info", "GET", f"object/info/{self.bucket}/{self.object_path(key)}")
            return True
        except StorageError as exc:
            if exc.code not in {"NoSuchKey", "ObjectNotFound"}:
                raise
            return False

    async def upload(self, content: bytes, *, prefix: str = "documents", force_refresh: bool = False) -> str:
        key = f"{prefix}/{hashlib.sha256(content).hexdigest()}"
        path = self.object_path(key)
        if not force_refresh and await self.exists(key):
            return key
        # A retry/race writes identical content to its hash key; upsert is idempotent.
        await self._request("upload", "POST", f"object/{self.bucket}/{path}", content=content,
                            headers={"Content-Type": "application/octet-stream", "x-upsert": "true"})
        return key

    async def download(self, key: str) -> bytes:
        response = await self._request("download", "GET", f"object/authenticated/{self.bucket}/{self.object_path(key)}")
        return response.content

    async def delete(self, key: str):
        self.object_path(key)
        await self._request("delete", "DELETE", f"object/{self.bucket}", json={"prefixes": [key]})

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
