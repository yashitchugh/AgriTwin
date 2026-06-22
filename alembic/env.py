"""
alembic/env.py — Alembic Migration Environment
================================================

Connects Alembic to:
  - AgriTwin's SQLAlchemy Base (so autogenerate reads our ORM models)
  - DATABASE_URL from .env (so the same config drives app and migrations)

Usage:
    # Generate a new migration after model changes:
    alembic revision --autogenerate -m "add column X to farms"

    # Apply all pending migrations to the database:
    alembic upgrade head

    # Downgrade one revision:
    alembic downgrade -1

    # Check current DB revision:
    alembic current

    # Show pending revisions:
    alembic history --verbose

PostgreSQL (production):
    Set DATABASE_URL=postgresql+psycopg2://... in .env and run the same
    alembic upgrade head command.  Alembic will translate the operations to
    PostgreSQL SQL automatically.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Add project root to sys.path ──────────────────────────────────────────────
# Required so Alembic (which runs from the project root) can import our models.
# env.py is at: alembic/env.py → parents[1] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

# ── Load .env before importing anything that reads settings ───────────────────
from dotenv import load_dotenv
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

# ── Import all models so Base.metadata knows about all tables ─────────────────
# This is the critical step for autogenerate — Alembic inspects Base.metadata
# to discover which tables exist in the ORM and which exist in the DB.
from backend.app.db.base import Base          # noqa: F401 — DeclarativeBase
from backend.app.models import (              # noqa: F401 — register all tables
    Farm, Field, SimulationRun, DailyOutput,raw_weather_records
)

# ── Alembic Config object ─────────────────────────────────────────────────────
config = context.config

# ── Override sqlalchemy.url from .env (takes priority over alembic.ini) ───────
_db_url = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{_PROJECT_ROOT / 'agritwin.db'}",
)
config.set_main_option("sqlalchemy.url", _db_url)

# ── Interpret the config file for Python logging ──────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata — the single source of truth for autogenerate ─────────────
target_metadata = Base.metadata


# ── Offline migration (generates SQL script without a live DB connection) ─────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Generates a SQL script suitable for review or manual execution.
    Useful for environments where direct DB access is unavailable
    (e.g. generating a migration file for DBA review before applying).
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Render AS BIGINT instead of INTEGER for Mapped[int] PKs on PostgreSQL.
        render_as_batch=True,   # required for SQLite ALTER TABLE support
        compare_type=True,      # detect column type changes during autogenerate
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migration (connects to live DB and applies migrations directly) ─────
def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates a direct connection to the database and applies pending
    migrations in a transaction. Default mode for `alembic upgrade head`.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # NullPool: no connection reuse (safe for migrations)
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,   # required for SQLite ALTER TABLE support
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
