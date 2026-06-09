"""
db/session.py — SQLAlchemy 2.0 Engine and Session Factory
==========================================================

This module provides:
  1. engine         — the single SQLAlchemy Engine instance.
  2. SessionLocal   — a sessionmaker() factory that produces Sessions.
  3. get_db()       — FastAPI dependency that yields a database session
                       and guarantees cleanup (commit on success, rollback
                       on exception, always close).
  4. create_tables() — convenience function for development / test setup
                       that calls Base.metadata.create_all().

DATABASE_URL is read from the .env file via python-dotenv, with a safe
fallback to an in-project SQLite file if the variable is not set.

SQLite-specific connection arguments:
  `check_same_thread=False` is required because FastAPI runs request
  handlers in a thread pool; the same connection may be accessed from
  different threads across the lifetime of a request.  SQLAlchemy's
  connection pool (NullPool for SQLite) is thread-safe, but the raw
  SQLite driver is not — this flag disables the driver's own check.

PostgreSQL migration:
  Change DATABASE_URL to `postgresql+psycopg2://...` and remove the
  `connect_args` block. SQLAlchemy will automatically switch to a
  QueuePool (default for PostgreSQL) which is both thread-safe and
  connection-pooled.  No changes to models or queries are required.

Usage (FastAPI route):
    from backend.app.db.session import get_db
    from sqlalchemy.orm import Session

    @router.get("/farms")
    def list_farms(db: Session = Depends(get_db)):
        return db.query(Farm).all()
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

# ── Load .env file ────────────────────────────────────────────────────────────
# Resolve the .env file relative to the project root.
# session.py is at: backend/app/db/session.py
#   parents[0] = backend/app/db
#   parents[1] = backend/app
#   parents[2] = backend
#   parents[3] = <project_root>   ← AgriTwin/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

# ── Database URL ──────────────────────────────────────────────────────────────
# Default: SQLite file in the project root, zero-config for development.
# Override via DATABASE_URL environment variable or .env for production.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{_PROJECT_ROOT / 'agritwin.db'}",
)

# ── SQLite connection arguments ───────────────────────────────────────────────
# `check_same_thread=False` is required for SQLite + FastAPI (multi-threaded).
# Silently ignored when using PostgreSQL (psycopg2 is natively thread-safe).
_connect_args: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

# ── Engine ────────────────────────────────────────────────────────────────────
# `echo=False` in production — set to True or use LOG_LEVEL=DEBUG to log SQL.
# `future=True` is the SQLAlchemy 2.0 compatibility flag (required for 2.x API).
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=os.getenv("LOG_LEVEL", "INFO") == "DEBUG",
    # future=True is the default in SA 2.0 — kept explicit for documentation.
    future=True,
)

# ── SQLite pragma: enable WAL mode for better concurrent read performance ─────
# Write-Ahead Logging allows readers and one writer to operate concurrently
# without blocking each other — important for FastAPI's thread pool.
# This is a no-op on PostgreSQL (different concurrency model).
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")   # enforce FK constraints in SQLite
        cursor.close()

# ── Session factory ───────────────────────────────────────────────────────────
# autocommit=False: transactions must be committed explicitly (safe default).
# autoflush=False:  prevents unintentional queries during relationship access
#                   before a commit.  Flush explicitly before queries that need
#                   to see newly added objects.
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # keep ORM objects usable after session.commit()
)


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy Session.

    Guarantees the session is always closed, even on exception.
    Commits on clean exit; rolls back on any exception so partial writes
    are never silently persisted.

    Usage:
        @router.post("/farms")
        def create_farm(
            body: FarmCreate,
            db: Session = Depends(get_db),
        ):
            farm = Farm(**body.dict())
            db.add(farm)
            db.commit()
            return farm
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Dev / test helper ─────────────────────────────────────────────────────────

def create_tables() -> None:
    """Create all tables defined in Base.metadata.

    Idempotent — `CREATE TABLE IF NOT EXISTS` semantics.
    Intended for:
      - Local development startup (called from main.py on startup event)
      - pytest fixtures (`create_tables()` at the start of a test session)
      - CI pipelines that run against a fresh SQLite file

    In production with PostgreSQL, use Alembic migrations instead of this
    function. Alembic tracks schema history and supports incremental upgrades
    without dropping data.

    Import here (not at module level) to avoid circular imports — models
    import Base from base.py, and session.py also imports Base. Importing
    models here only when create_tables() is called breaks the cycle.
    """
    # Import all models so their tables are registered in Base.metadata
    # before create_all() is called.
    from backend.app.models import farm, field, simulation_run, daily_output  # noqa: F401
    from backend.app.db.base import Base

    Base.metadata.create_all(bind=engine)
