"""
repositories/__init__.py — Repository layer public API
=======================================================

The repository pattern separates database access logic from business logic.
Each repository class wraps a SQLAlchemy Session and exposes domain-level
CRUD methods.  All repositories receive a Session via dependency injection
— they never manage their own session lifecycle (no commit/rollback here).

Session lifecycle contract:
    The Session is owned by get_db() (db/session.py).
    Repositories call session.add(), session.flush(), session.delete(), etc.
    Commit and rollback happen in get_db() (or an explicit service layer).

FastAPI usage pattern:
    def get_simulation_repo(db: Session = Depends(get_db)):
        return SimulationRepository(db)

    @router.post("/simulate")
    def run(repo: SimulationRepository = Depends(get_simulation_repo)):
        ...

Imports:
    from backend.app.repositories import (
        SimulationRepository,
        DailyOutputRepository,
        FieldRepository,
    )
"""

from backend.app.repositories.simulation_repository import SimulationRepository  # noqa: F401
from backend.app.repositories.daily_output_repository import DailyOutputRepository  # noqa: F401
from backend.app.repositories.field_repository import FieldRepository  # noqa: F401

__all__ = [
    "SimulationRepository",
    "DailyOutputRepository",
    "FieldRepository",
]
