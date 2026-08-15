"""Journal entries and screenshots.

A journal entry is either attached to a trade (post-trade review) or standalone (a daily/weekly
note). Screenshots reference a :class:`FileObject`; the binary never touches PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, JSONDict, TZDateTime


class JournalEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "journal_entries"
    __table_args__ = (
        Index("ix_journal_entries_org_date", "organization_id", "entry_date"),
        Index("ix_journal_entries_trade", "trade_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="CASCADE"), nullable=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )

    entry_date: Mapped[date] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: "trade_review" | "daily" | "weekly" | "note"
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False, default="note")
    mood: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: Self-rated adherence to the plan, 1-5.
    discipline_rating: Mapped[int | None] = mapped_column(nullable=True)
    lessons: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    pinned_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class Screenshot(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Links an uploaded image to a trade or journal entry, with chart context."""

    __tablename__ = "screenshots"
    __table_args__ = (
        Index("ix_screenshots_org_trade", "organization_id", "trade_id"),
        Index("ix_screenshots_journal", "journal_entry_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_object_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("file_objects.id", ondelete="CASCADE"), nullable=False
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="CASCADE"), nullable=True
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=True
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    caption: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: "before" | "entry" | "management" | "exit" | "review"
    phase: Mapped[str] = mapped_column(String(24), nullable=False, default="review")
    timeframe: Mapped[str | None] = mapped_column(String(8), nullable=True)
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)


__all__ = ["JournalEntry", "Screenshot"]
