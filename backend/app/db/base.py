"""
db/base.py — SQLAlchemy 2.0 Declarative Base and Shared Mixins
===============================================================

This module defines:
  1. Base — the single DeclarativeBase that all ORM models inherit from.
  2. TimestampMixin — adds `created_at` and `updated_at` to any model that
     includes it.  Both columns are timezone-aware UTC datetimes.

Design notes:
  - All models use Base so that `Base.metadata.create_all(engine)` creates
    every table in one call (useful for SQLite dev and test fixtures).
  - Alembic autogenerate reads `Base.metadata` to produce migrations.
  - The `Uuid` type (sqlalchemy.types.Uuid) maps to:
      SQLite  → CHAR(36) stored as hyphenated UUID string
      PostgreSQL → native UUID column (efficient, indexed)
    This lets us develop on SQLite and deploy on PostgreSQL with no model
    changes — only the DATABASE_URL needs to change.
  - `JSON` type maps to:
      SQLite  → TEXT (serialized via Python's json module)
      PostgreSQL → JSONB (binary JSON, supports indexing and operators)
    Alembic's PostgreSQL dialect will emit JSONB for the migration.
"""

import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for all AgriTwin ORM models.

    Every table defined as a subclass of Base is registered in
    Base.metadata and can be created with `Base.metadata.create_all(engine)`.

    Type annotation map overrides (applied globally across all models):
      datetime.datetime → DateTime(timezone=True)  — always UTC-aware
    """

    # Override the default DateTime mapping to always store timezone-aware
    # datetimes. This is critical for correct behaviour when the server
    # timezone changes or when migrating to a hosted PostgreSQL instance.
    type_annotation_map = {
        datetime.datetime: DateTime(timezone=True),
    }


class TimestampMixin:
    """Adds `created_at` and `updated_at` columns to any model.

    Usage:
        class Farm(TimestampMixin, Base):
            ...

    Both columns are set by the database server (via `server_default` and
    `onupdate`) so they are correct even when records are inserted by raw SQL
    or direct DB tools, not just via the ORM.

    PostgreSQL note:
        `func.now()` maps to `NOW()` in PostgreSQL, which returns the current
        transaction timestamp (not wall clock). This ensures all rows written
        in one transaction share the same `created_at` — useful for bulk inserts.
    """

    created_at: Mapped[datetime.datetime] = mapped_column(
        # server_default: executed by the DB engine at INSERT time.
        # Avoids clock skew between app server and DB server.
        server_default=func.now(),
        doc="UTC timestamp when this record was first inserted.",
    )

    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(),
        # onupdate: executed by SQLAlchemy on every UPDATE statement.
        # NOTE: only fires on ORM UPDATE calls, not raw SQL.  For raw SQL,
        # a PostgreSQL trigger (see migrations) should be used in production.
        onupdate=func.now(),
        doc="UTC timestamp of the most recent UPDATE to this record.",
    )
