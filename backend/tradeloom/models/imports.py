"""CSV import pipeline persistence.

Every uploaded row is retained as an :class:`ImportRow` with its parsed values, validation errors
and final disposition. Nothing is ever discarded silently: an invalid row is stored with
``status = invalid`` and a structured list of field errors the UI can render inline.

A committed import records the ids it created, which is what makes :meth:`revert` safe — the
revert deletes exactly the rows this import produced and nothing else.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import ImportRowStatus, ImportStatus
from tradeloom.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, TZDateTime


class ImportTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A reusable broker column mapping.

    System templates (``organization_id IS NULL``) ship with the product; users can save their own
    mapping after a successful import.
    """

    __tablename__ = "import_templates"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_import_templates_org_key"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    broker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Canonical field -> source column name.
    column_mapping: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Parsing options: timestamp format, source timezone, decimal separator, direction synonyms.
    options: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Header fingerprints used to auto-detect this template from an uploaded file.
    detection_headers: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Import(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "imports"
    __table_args__ = (
        Index("ix_imports_org_created", "organization_id", "created_at"),
        Index("ix_imports_account", "account_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    file_object_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("file_objects.id", ondelete="SET NULL"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("import_templates.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[ImportStatus] = mapped_column(
        EnumType(ImportStatus, 20), nullable=False, default=ImportStatus.UPLOADED
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Digest of the uploaded bytes — an identical file re-uploaded to the same account is flagged.
    file_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: What the parser found: detected delimiter, headers, sample values, per-column type guesses.
    inspection: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    column_mapping: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    options: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: "orders" (fills that build trades) or "trades" (pre-aggregated round trips).
    row_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="orders")

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("job_records.id", ondelete="SET NULL"), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    #: Structured, user-safe error summary. Internal detail stays in the job record.
    error_summary: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    @property
    def can_revert(self) -> bool:
        return self.status == ImportStatus.COMPLETED and self.reverted_at is None


class ImportRow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("import_id", "row_number", name="uq_import_rows_import_row_number"),
        Index("ix_import_rows_import_status", "import_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("imports.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based index within the data rows (header excluded), so errors match what the user sees.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        EnumType(ImportRowStatus, 16), nullable=False, default=ImportRowStatus.PENDING
    )

    raw_data: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Normalised values (UTC timestamps, canonical symbol, Decimal-as-string amounts).
    normalized_data: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: ``[{"field": "entry_price", "code": "not_a_number", "message": "..."}]``
    errors: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    warnings: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    duplicate_of_order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    created_order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    created_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["Import", "ImportRow", "ImportTemplate"]
