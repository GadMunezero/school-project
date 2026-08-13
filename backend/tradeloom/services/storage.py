"""S3-compatible object storage.

Behind a narrow interface so tests (and a developer without MinIO running) can use an in-memory
backend without changing any calling code.

Two rules enforced here rather than at the route:

* **Object keys are namespaced by organization** (``org/<org_id>/<purpose>/<uuid><ext>``). Even if
  a bug leaked a key, it would be evidently cross-tenant, and the metadata check in
  :class:`~tradeloom.services.files.FileService` still refuses to sign it.
* **Content type is verified against the file's magic bytes**, not the browser-supplied header. A
  ``.png`` that is really an HTML document is rejected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from tradeloom.core.config import Settings, get_settings
from tradeloom.core.errors import StorageUnavailableError, ValidationError
from tradeloom.core.logging import get_logger

logger = get_logger(__name__)

#: Magic-byte prefixes for the formats we accept. CSV/JSON are text and validated separately.
_MAGIC_BYTES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}

_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "text/csv": ".csv",
    "application/json": ".json",
}


@dataclass(slots=True)
class StoredObject:
    bucket: str
    key: str
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    bucket: str

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def signed_url(self, key: str, expires_in: int) -> str: ...
    def exists(self, key: str) -> bool: ...
    def ensure_bucket(self) -> None: ...


@dataclass
class InMemoryStorage:
    """Test/dev backend. Signed URLs are opaque local references, not real URLs."""

    bucket: str = "tradeloom-test"
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        self.objects[key] = (data, content_type)
        return StoredObject(self.bucket, key, len(data), content_type)

    def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise StorageUnavailableError("Object not found in storage.")
        return self.objects[key][0]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def signed_url(self, key: str, expires_in: int) -> str:
        return f"memory://{self.bucket}/{key}?expires_in={expires_in}"

    def exists(self, key: str) -> bool:
        return key in self.objects

    def ensure_bucket(self) -> None:
        return None


class S3Storage:
    """boto3-backed storage for AWS S3, MinIO, R2 and compatible services."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bucket = settings.s3_bucket
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url or None,
                region_name=self.settings.s3_region,
                aws_access_key_id=self.settings.s3_access_key_id,
                aws_secret_access_key=self.settings.s3_secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    s3={
                        "addressing_style": "path" if self.settings.s3_force_path_style else "auto"
                    },
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    def ensure_bucket(self) -> None:
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                client.create_bucket(Bucket=self.bucket)
                logger.info("storage_bucket_created", bucket=self.bucket)
            except Exception as exc:  # pragma: no cover - depends on remote permissions
                raise StorageUnavailableError("Object storage is not reachable.") from exc

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        try:
            self._get_client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                # Private by default; access is only ever through a signed URL.
                ACL="private",
            )
        except Exception as exc:
            logger.warning("storage_put_failed", error=type(exc).__name__)
            raise StorageUnavailableError() from exc
        return StoredObject(self.bucket, key, len(data), content_type)

    def get(self, key: str) -> bytes:
        try:
            response = self._get_client().get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()  # type: ignore[no-any-return]
        except Exception as exc:
            raise StorageUnavailableError("The file could not be retrieved.") from exc

    def delete(self, key: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # pragma: no cover
            logger.warning("storage_delete_failed", error=type(exc).__name__)

    def exists(self, key: str) -> bool:
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def signed_url(self, key: str, expires_in: int) -> str:
        try:
            url = self._get_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise StorageUnavailableError() from exc

        # Rewrite the internal endpoint to the browser-reachable host (compose networking).
        public = self.settings.s3_public_base_url
        internal = self.settings.s3_endpoint_url
        if public and internal and url.startswith(internal):
            url = public.rstrip("/") + url[len(internal.rstrip("/")) :]
        return str(url)


_storage: ObjectStorage | None = None


def get_storage(settings: Settings | None = None) -> ObjectStorage:
    global _storage
    if _storage is None:
        resolved = settings or get_settings()
        _storage = InMemoryStorage() if resolved.is_test else S3Storage(resolved)
    return _storage


def set_storage(storage: ObjectStorage | None) -> None:
    global _storage
    _storage = storage


# --- validation -------------------------------------------------------------


def detect_content_type(data: bytes, declared: str) -> str:
    """Verify the declared type against the file's magic bytes.

    Text formats have no reliable signature, so they are accepted after a decode check. Anything
    claiming to be an image must actually start like one.
    """
    declared = (declared or "").split(";")[0].strip().lower()

    if declared in _MAGIC_BYTES:
        prefixes = _MAGIC_BYTES[declared]
        if not any(data.startswith(prefix) for prefix in prefixes):
            raise ValidationError(
                "The file contents do not match its type. Upload a genuine image file."
            )
        if declared == "image/webp" and b"WEBP" not in data[:16]:
            raise ValidationError("The file is not a valid WebP image.")
        return declared

    if declared in ("text/csv", "application/json", "text/plain"):
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("The file must be UTF-8 encoded text.") from exc
        return "text/csv" if declared == "text/plain" else declared

    raise ValidationError(f"Files of type '{declared or 'unknown'}' are not accepted.")


def validate_upload(data: bytes, declared_type: str, settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > resolved.upload_max_bytes:
        limit_mb = resolved.upload_max_bytes // (1024 * 1024)
        raise ValidationError(f"Files must be {limit_mb} MB or smaller.")

    content_type = detect_content_type(data, declared_type)
    if content_type not in resolved.upload_allowed_mime:
        raise ValidationError(f"Files of type '{content_type}' are not accepted.")
    return content_type


def build_object_key(organization_id: uuid.UUID, purpose: str, content_type: str) -> str:
    """Tenant-namespaced, unguessable key. The filename never comes from user input."""
    extension = _EXTENSIONS.get(content_type, ".bin")
    return f"org/{organization_id}/{purpose}/{uuid.uuid4().hex}{extension}"


__all__ = [
    "InMemoryStorage",
    "ObjectStorage",
    "S3Storage",
    "StoredObject",
    "build_object_key",
    "detect_content_type",
    "get_storage",
    "set_storage",
    "validate_upload",
]
