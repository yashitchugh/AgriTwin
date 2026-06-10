# tests/conftest.py
"""
Shared pytest fixtures for all AgriTwin tests.

StaticPool is critical for SQLite :memory: databases:
  Without it, each thread/connection gets an isolated empty database.
  FastAPI's TestClient dispatches sync route handlers in a threadpool,
  so requests get a different connection than the one that ran create_all().
  StaticPool forces all connections to share the same underlying connection,
  making the schema visible across all sessions.
"""

import sys
import os
import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Farm, Field, SimulationRun, DailyOutput  # noqa: F401


@pytest.fixture(scope="session")
def test_engine():
    """Session-scoped in-memory SQLite engine shared across all tests.

    StaticPool ensures all threads see the same tables (critical for
    FastAPI's threadpool dispatch of sync endpoints).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def test_db(test_engine):
    """Per-test Session. Rolls back after each test for isolation."""
    with Session(test_engine) as session:
        yield session
        session.rollback()


@pytest.fixture(scope="function")
def client(test_engine):
    """FastAPI TestClient with get_db overridden to use the in-memory test engine."""
    def override_get_db():
        with Session(test_engine) as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ── Shared test payloads ──────────────────────────────────────────────────────

SIMULATE_PAYLOAD = {
    "latitude": 52.0,
    "longitude": 5.5,
    "crop": "wheat",
    "variety": "apache",
    "sowing_date": "2020-10-15",
    "harvest_date": "2021-03-31",    # short season → fast WOFOST run (~0.3s)
    "max_duration": 180,
    "use_real_weather": False,        # synthetic — no internet needed
    "use_real_soil": False,           # default medium-loam
    "irrigation_events": [],
}

FIELD_PAYLOAD = {
    "name": "Test Field Alpha",
    "latitude": 26.8,
    "longitude": 80.9,
    "area_ha": 2.5,
    "description": "Pytest fixture field",
}
