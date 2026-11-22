"""Baseline: schema as of 2026-10-28.

The application still creates tables with `Base.metadata.create_all()` on
startup (and applies the legacy column/index sync), then stamps this revision
into `alembic_version`. That makes an existing database and a fresh one agree
on a starting point, so every later change can be a normal Alembic revision.

This revision is intentionally a no-op for fresh databases: by the time it is
stamped, create_all has already produced the schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-28
"""

from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Schema creation is owned by Base.metadata.create_all() at startup.
    pass


def downgrade() -> None:
    # Dropping the whole schema is not a supported operation.
    pass
