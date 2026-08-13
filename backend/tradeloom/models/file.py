"""Object-storage metadata.

PostgreSQL stores only the *record* of an upload; bytes live in S3-compatible storage. Access is
always through a short-lived signed URL minted after an ownership check — objects are never
publicly readable, and the signed URL itself is never logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, JSONDict, TZDateTime


class FileObject(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "file_objects"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="uq_file_objects_bucket_key"),
        Index("ix_file_objects_org_purpose", "organization_id", "purpose"),
        Index("ix_file_objects_checksum", "checksum_sha256"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Keys are namespaced by organization: ``org/<org_id>/<purpose>/<uuid><ext>``.
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Validated against an allow-list *and* against the file's magic bytes, not just the header.
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    #: "screenshot" | "import" | "avatar" | "export"
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, default="screenshot")
    #: False until the bytes are confirmed present in storage.
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Set for generated exports so the cleanup job can remove them.
    expires_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)


__all__ = ["FileObject"]
