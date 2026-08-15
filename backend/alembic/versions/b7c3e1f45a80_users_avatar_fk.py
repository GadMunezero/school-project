"""Create the users -> file_objects foreign key that the initial migration never emitted.

Revision ID: b7c3e1f45a80
Revises: aedd7a7dcecf
Create Date: 2026-08-15

The models declare ``users.avatar_file_id`` as a foreign key with ``use_alter=True``, which breaks
the users -> file_objects -> organizations -> users cycle so PostgreSQL can order the initial
CREATE TABLEs. The initial migration passed that constraint to ``op.create_table``, where
``use_alter`` means nothing: it is a metadata directive for ``create_all``, and Alembic simply did
not emit the constraint. PostgreSQL therefore had *no* foreign keys on ``users`` at all, and
deleting a file left a dangling avatar reference instead of nulling it.

It went unnoticed because the drift check runs against SQLite, which does not reflect the
difference. A restore drill against a real PostgreSQL dump is what surfaced it.

SQLite is skipped: it cannot add a constraint to an existing table, and there the schema comes
from ``create_all`` in tests anyway, which honours ``use_alter`` correctly.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import inspect

from alembic import op

revision: str = "b7c3e1f45a80"
down_revision: str | None = "aedd7a7dcecf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "fk_users_avatar_file_id_file_objects"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    # Idempotent: a database created by `create_all` rather than by migrations already has it.
    existing = {fk.get("name") for fk in inspect(bind).get_foreign_keys("users")}
    if CONSTRAINT in existing:
        return

    op.create_foreign_key(
        CONSTRAINT,
        "users",
        "file_objects",
        ["avatar_file_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    existing = {fk.get("name") for fk in inspect(bind).get_foreign_keys("users")}
    if CONSTRAINT in existing:
        op.drop_constraint(CONSTRAINT, "users", type_="foreignkey")
