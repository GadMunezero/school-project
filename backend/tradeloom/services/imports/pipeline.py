"""The import pipeline.

Stages, each of which persists its result so the workflow survives a page refresh::

    upload -> inspect -> map -> validate -> preview -> commit -> (revert)

Guarantees:

* **Nothing is silently discarded.** Every row is stored with a status and, when invalid, a list
  of ``{field, code, message}`` errors the UI attaches to the offending cell.
* **Duplicates are detected, not merged.** A row whose ``external_id`` already exists on the
  account is marked ``duplicate`` and skipped, and the count is reported.
* **Commit is one transaction.** Either every valid row lands or none do.
* **Revert is exact.** The import records the ids it created, so reverting removes precisely
  those rows and recomputes the account balance — it never deletes anything it did not create.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import (
    AssetType,
    AuditAction,
    ImportRowStatus,
    ImportStatus,
    OrderSide,
    TradeSource,
)
from tradeloom.core.errors import NotFoundError, UnprocessableStateError, ValidationError
from tradeloom.core.security import checksum_bytes
from tradeloom.core.timeutil import utcnow
from tradeloom.models.imports import Import, ImportRow, ImportTemplate
from tradeloom.models.trading import Order, Trade
from tradeloom.repositories.base import TenantRepository
from tradeloom.repositories.trading import AccountRepository, OrderRepository, TradeRepository
from tradeloom.schemas.trade import FillInput
from tradeloom.services.accounts import AccountService
from tradeloom.services.audit import AuditService
from tradeloom.services.catalog import InstrumentService
from tradeloom.services.imports import parsing
from tradeloom.services.trades import TradeService

REQUIRED_FIELDS = ("timestamp", "symbol", "side", "quantity", "price")


class ImportRepository(TenantRepository[Import]):
    model = Import
    supports_soft_delete = False


class ImportRowRepository(TenantRepository[ImportRow]):
    model = ImportRow
    supports_soft_delete = False


@dataclass(slots=True)
class RowError:
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(slots=True)
class NormalisedRow:
    row_number: int
    raw: dict[str, str]
    timestamp: datetime | None = None
    symbol: str | None = None
    side: OrderSide | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    commission: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    external_id: str | None = None
    notes: str | None = None
    errors: list[RowError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: ImportRowStatus = ImportRowStatus.PENDING

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_normalised_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "symbol": self.symbol,
            "side": self.side.value if self.side else None,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "price": str(self.price) if self.price is not None else None,
            "commission": str(self.commission),
            "fees": str(self.fees),
            "external_id": self.external_id,
            "notes": self.notes,
        }


class ImportPipeline:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.imports = ImportRepository(session, organization_id)
        self.rows = ImportRowRepository(session, organization_id)
        self.accounts = AccountRepository(session, organization_id)
        self.orders = OrderRepository(session, organization_id)
        self.trades = TradeRepository(session, organization_id)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Stage 1-2: upload and inspect
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        account_id: uuid.UUID,
        filename: str,
        data: bytes,
        file_object_id: uuid.UUID | None = None,
    ) -> tuple[Import, parsing.Inspection]:
        account = await self.accounts.get(account_id)
        if account is None:
            raise NotFoundError("Account not found.")

        try:
            inspection = parsing.inspect_csv(data)
        except parsing.CsvParseError as exc:
            raise ValidationError(str(exc)) from exc

        if inspection.total_rows == 0:
            raise ValidationError("The file contains a header but no data rows.")

        checksum = checksum_bytes(data)
        previous = await self.imports.list(
            Import.account_id == account_id,
            Import.file_checksum == checksum,
            Import.status == ImportStatus.COMPLETED,
        )

        template = await self._detect_template(inspection.headers)
        record = Import(
            organization_id=self.organization_id,
            account_id=account_id,
            created_by_user_id=self.actor_user_id,
            file_object_id=file_object_id,
            template_id=template.id if template else None,
            status=ImportStatus.MAPPING,
            filename=filename[:255],
            file_checksum=checksum,
            inspection=inspection.to_dict(),
            column_mapping=(
                dict(template.column_mapping) if template else inspection.suggested_mapping
            ),
            options=dict(template.options) if template else {"timezone": account.timezone},
            total_rows=inspection.total_rows,
        )
        if previous:
            record.error_summary = {
                "warning": "An identical file was already imported into this account.",
                "previous_import_id": str(previous[0].id),
            }
        await self.imports.add(record)
        if template is not None:
            inspection.detected_template = template.key
            record.inspection = inspection.to_dict()
        return record, inspection

    async def _detect_template(self, headers: list[str]) -> ImportTemplate | None:
        """Match the header set against known broker templates.

        A template matches when every one of its fingerprint headers is present, so extra columns
        do not defeat detection.
        """
        result = await self.session.execute(
            select(ImportTemplate).where(
                (ImportTemplate.organization_id == self.organization_id)
                | (ImportTemplate.organization_id.is_(None))
            )
        )
        normalised = {header.strip().lower() for header in headers}
        best: ImportTemplate | None = None
        best_score = 0
        for template in result.scalars().all():
            fingerprint = [
                str(value).strip().lower()
                for value in (template.detection_headers or {}).get("required", [])
            ]
            if not fingerprint:
                continue
            if all(value in normalised for value in fingerprint) and len(fingerprint) > best_score:
                best, best_score = template, len(fingerprint)
        return best

    # ------------------------------------------------------------------
    # Stage 3-5: map, validate, preview
    # ------------------------------------------------------------------

    async def get(self, import_id: uuid.UUID) -> Import:
        record = await self.imports.get(import_id)
        if record is None:
            raise NotFoundError("Import not found.")
        return record

    async def set_mapping(
        self, import_id: uuid.UUID, mapping: dict[str, str], options: dict[str, Any]
    ) -> Import:
        record = await self.get(import_id)
        if record.status in (ImportStatus.COMPLETED, ImportStatus.REVERTED):
            raise UnprocessableStateError("This import has already been completed.")

        missing = [field for field in REQUIRED_FIELDS if not mapping.get(field)]
        if missing:
            raise ValidationError(
                "Map every required column before continuing: " + ", ".join(missing),
                details={"missing_fields": missing},
            )
        headers = set(record.inspection.get("headers", []))
        unknown = [column for column in mapping.values() if column and column not in headers]
        if unknown:
            raise ValidationError(
                "These columns are not in the file: " + ", ".join(unknown),
                details={"unknown_columns": unknown},
            )

        record.column_mapping = {k: v for k, v in mapping.items() if v}
        record.options = {**record.options, **options}
        record.status = ImportStatus.MAPPING
        await self.session.flush()
        return record

    async def validate(self, import_id: uuid.UUID, data: bytes) -> Import:
        """Parse and check every row, persisting each with its outcome."""
        record = await self.get(import_id)
        if not record.column_mapping:
            raise UnprocessableStateError("Map the file's columns before validating.")

        record.status = ImportStatus.VALIDATING
        await self.session.flush()

        delimiter = record.inspection.get("delimiter", ",")
        source_timezone = record.options.get("timezone", "UTC")
        raw_rows = parsing.iter_rows(data, delimiter)

        normalised = [
            self._normalise_row(index + 1, raw, record.column_mapping, source_timezone)
            for index, raw in enumerate(raw_rows)
        ]

        await self._flag_duplicates(record, normalised)

        # Replace any previous validation pass for this import.
        await self.session.execute(
            delete(ImportRow).where(
                ImportRow.import_id == record.id,
                ImportRow.organization_id == self.organization_id,
            )
        )

        counts = {"valid": 0, "invalid": 0, "duplicate": 0}
        for row in normalised:
            if row.status is ImportRowStatus.DUPLICATE:
                counts["duplicate"] += 1
            elif row.is_valid:
                row.status = ImportRowStatus.VALID
                counts["valid"] += 1
            else:
                row.status = ImportRowStatus.INVALID
                counts["invalid"] += 1

            self.session.add(
                ImportRow(
                    organization_id=self.organization_id,
                    import_id=record.id,
                    row_number=row.row_number,
                    status=row.status,
                    raw_data=row.raw,
                    normalized_data=row.to_normalised_dict(),
                    errors={"items": [error.to_dict() for error in row.errors]},
                    warnings={"items": row.warnings},
                )
            )

        record.total_rows = len(normalised)
        record.valid_rows = counts["valid"]
        record.invalid_rows = counts["invalid"]
        record.duplicate_rows = counts["duplicate"]
        record.status = ImportStatus.PREVIEW
        await self.session.flush()
        return record

    def _normalise_row(
        self,
        row_number: int,
        raw: dict[str, str],
        mapping: dict[str, str],
        timezone: str,
    ) -> NormalisedRow:
        row = NormalisedRow(row_number=row_number, raw=raw)

        def value_for(field_name: str) -> str:
            column = mapping.get(field_name)
            return raw.get(column, "") if column else ""

        timestamp_text = value_for("timestamp")
        row.timestamp = parsing.parse_timestamp(timestamp_text, timezone)
        if row.timestamp is None:
            row.errors.append(
                RowError(
                    "timestamp",
                    "unparseable_timestamp",
                    (
                        f"'{timestamp_text}' is not a recognised date/time."
                        if timestamp_text
                        else "A date/time is required."
                    ),
                )
            )
        elif row.timestamp > utcnow():
            row.warnings.append("The timestamp is in the future.")

        symbol_text = value_for("symbol")
        row.symbol = parsing.normalize_symbol(symbol_text) if symbol_text else None
        if not row.symbol:
            row.errors.append(RowError("symbol", "missing_symbol", "A symbol is required."))

        side_text = value_for("side")
        row.side = parsing.parse_side(side_text)
        if row.side is None:
            row.errors.append(
                RowError(
                    "side",
                    "unknown_side",
                    (
                        f"'{side_text}' is not a recognised buy/sell value."
                        if side_text
                        else "A buy/sell value is required."
                    ),
                )
            )

        quantity = parsing.parse_decimal(value_for("quantity"))
        if quantity is None:
            row.errors.append(RowError("quantity", "invalid_quantity", "A quantity is required."))
        elif quantity == 0:
            row.errors.append(
                RowError("quantity", "zero_quantity", "Quantity must be greater than zero.")
            )
        else:
            if quantity < 0:
                # A negative quantity conventionally encodes a sell; honour it, but say so.
                row.warnings.append("Negative quantity interpreted as a sell.")
                if row.side is OrderSide.BUY:
                    row.side = OrderSide.SELL
                quantity = abs(quantity)
            row.quantity = quantity

        price = parsing.parse_decimal(value_for("price"))
        if price is None:
            row.errors.append(RowError("price", "invalid_price", "A price is required."))
        elif price <= 0:
            row.errors.append(
                RowError("price", "non_positive_price", "Price must be greater than zero.")
            )
        else:
            row.price = price

        commission = parsing.parse_decimal(value_for("commission"), allow_negative=False)
        row.commission = abs(commission) if commission is not None else Decimal(0)
        fees = parsing.parse_decimal(value_for("fees"), allow_negative=False)
        row.fees = abs(fees) if fees is not None else Decimal(0)

        row.external_id = (value_for("external_id") or "").strip()[:120] or None
        row.notes = (value_for("notes") or "").strip()[:500] or None
        return row

    async def _flag_duplicates(self, record: Import, rows: list[NormalisedRow]) -> None:
        """Two kinds of duplicate: already in the database, and repeated within this file."""
        candidates = [row.external_id for row in rows if row.external_id]
        existing = await self.orders.by_external_ids(record.account_id, candidates)

        seen: set[str] = set()
        for row in rows:
            if not row.external_id:
                continue
            if row.external_id in existing:
                row.status = ImportRowStatus.DUPLICATE
                row.warnings.append("This execution has already been imported.")
            elif row.external_id in seen:
                row.status = ImportRowStatus.DUPLICATE
                row.warnings.append("This execution id appears more than once in the file.")
            seen.add(row.external_id)

    async def preview(self, import_id: uuid.UUID, limit: int = 50) -> dict[str, Any]:
        record = await self.get(import_id)
        rows = await self.rows.list(
            ImportRow.import_id == record.id,
            order_by=[ImportRow.row_number.asc()],
            limit=limit,
        )
        invalid = await self.rows.list(
            ImportRow.import_id == record.id,
            ImportRow.status == ImportRowStatus.INVALID,
            order_by=[ImportRow.row_number.asc()],
            limit=limit,
        )
        return {
            "import_id": str(record.id),
            "status": record.status.value,
            "totals": {
                "total": record.total_rows,
                "valid": record.valid_rows,
                "invalid": record.invalid_rows,
                "duplicate": record.duplicate_rows,
            },
            "rows": [_row_payload(row) for row in rows],
            "invalid_rows": [_row_payload(row) for row in invalid],
        }

    # ------------------------------------------------------------------
    # Stage 6: commit
    # ------------------------------------------------------------------

    async def commit(self, import_id: uuid.UUID) -> Import:
        """Turn valid rows into fills, then into positions and trades.

        Fills are grouped by symbol and replayed in timestamp order through the same
        :class:`~tradeloom.services.trades.TradeService` used by manual entry, so an imported
        partial exit produces exactly the same numbers a manually recorded one would.
        """
        record = await self.get(import_id)
        if record.status is ImportStatus.COMPLETED:
            raise UnprocessableStateError("This import has already been committed.")
        if record.status is not ImportStatus.PREVIEW:
            raise UnprocessableStateError("Validate the file before committing it.")
        if record.valid_rows == 0:
            raise UnprocessableStateError("There are no valid rows to import.")

        account = await self.accounts.get(record.account_id)
        if account is None:
            raise NotFoundError("Account not found.")

        record.status = ImportStatus.COMMITTING
        await self.session.flush()

        valid_rows = await self.rows.list(
            ImportRow.import_id == record.id,
            ImportRow.status == ImportRowStatus.VALID,
            order_by=[ImportRow.row_number.asc()],
        )

        by_symbol: dict[str, list[tuple[ImportRow, FillInput]]] = {}
        for row in valid_rows:
            payload = row.normalized_data
            fill = FillInput(
                side=OrderSide(payload["side"]),
                quantity=Decimal(payload["quantity"]),
                price=Decimal(payload["price"]),
                timestamp=datetime.fromisoformat(payload["timestamp"]),
                commission=Decimal(payload.get("commission") or 0),
                fees=Decimal(payload.get("fees") or 0),
                external_id=payload.get("external_id"),
                notes=payload.get("notes"),
            )
            by_symbol.setdefault(str(payload["symbol"]), []).append((row, fill))

        trade_service = TradeService(
            self.session, self.organization_id, actor_user_id=self.actor_user_id
        )
        instruments = InstrumentService(self.session, self.organization_id)

        created_trades = 0
        created_orders = 0
        for symbol, entries in sorted(by_symbol.items()):
            entries.sort(key=lambda pair: pair[1].timestamp)
            instrument = await instruments.resolve(symbol)
            asset_type = instrument.asset_type if instrument else AssetType.EQUITY

            trades = await trade_service.ingest_fills(
                account=account,
                symbol=symbol,
                asset_type=asset_type,
                fills=[fill for _, fill in entries],
                source=TradeSource.IMPORT,
                instrument=instrument,
                import_id=record.id,
            )
            created_trades += len(trades)
            created_orders += len(entries)

            for row, _ in entries:
                row.status = ImportRowStatus.IMPORTED

        await AccountService(
            self.session, self.organization_id, actor_user_id=self.actor_user_id
        ).recalculate(account.id)

        record.status = ImportStatus.COMPLETED
        record.committed_at = utcnow()
        record.imported_rows = len(valid_rows)
        record.created_order_count = created_orders
        record.created_trade_count = created_trades
        await self.session.flush()

        await self.audit.record(
            AuditAction.IMPORT_COMMITTED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="import",
            entity_id=record.id,
            summary=f"Imported {len(valid_rows)} rows into {account.name}",
        )
        return record

    # ------------------------------------------------------------------
    # Stage 7: revert
    # ------------------------------------------------------------------

    async def revert(self, import_id: uuid.UUID) -> Import:
        """Undo a committed import.

        Safe because the import owns its output: orders and trades carry ``import_id``, so the
        delete is scoped to exactly the rows this import created. Trades that were *continued* by
        the import (an open position that existed beforehand) are left alone and reported, since
        removing them would delete data the import did not create.
        """
        record = await self.get(import_id)
        if not record.can_revert:
            raise UnprocessableStateError(
                "Only a completed import that has not already been reverted can be undone."
            )

        orders = await self.orders.list(Order.import_id == record.id, include_deleted=True)
        trade_ids = {order.trade_id for order in orders if order.trade_id}

        trades = await self.trades.get_many(list(trade_ids)) if trade_ids else []
        kept: list[str] = []
        removed = 0
        for trade in trades:
            if trade.import_id != record.id:
                # Pre-existing trade that this import added fills to — leave it.
                kept.append(str(trade.id))
                continue
            await self.trades.hard_delete(trade.id)
            removed += 1

        for order in orders:
            await self.orders.hard_delete(order.id)

        await self.session.execute(
            delete(Trade).where(
                Trade.organization_id == self.organization_id, Trade.import_id == record.id
            )
        )

        await AccountService(
            self.session, self.organization_id, actor_user_id=self.actor_user_id
        ).recalculate(record.account_id)

        record.status = ImportStatus.REVERTED
        record.reverted_at = utcnow()
        record.error_summary = {
            **record.error_summary,
            "reverted": {
                "orders_removed": len(orders),
                "trades_removed": removed,
                "trades_kept": kept,
            },
        }
        await self.session.flush()

        await self.audit.record(
            AuditAction.IMPORT_REVERTED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="import",
            entity_id=record.id,
            summary=f"Reverted import {record.filename}",
        )
        return record


def _row_payload(row: ImportRow) -> dict[str, Any]:
    return {
        "row_number": row.row_number,
        "status": row.status.value,
        "raw": row.raw_data,
        "normalized": row.normalized_data,
        "errors": (row.errors or {}).get("items", []),
        "warnings": (row.warnings or {}).get("items", []),
    }


__all__ = [
    "REQUIRED_FIELDS",
    "ImportPipeline",
    "ImportRepository",
    "ImportRowRepository",
    "NormalisedRow",
    "RowError",
]
