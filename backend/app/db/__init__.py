"""
db/__init__.py — Public re-exports for the database layer.

Usage:
    from backend.app.db import get_db, create_tables, engine
"""

from backend.app.db.session import engine, SessionLocal, get_db, create_tables  # noqa: F401
from backend.app.db.base import Base  # noqa: F401
