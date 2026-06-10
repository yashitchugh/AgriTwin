"""
repositories/simulation_repository.py — SimulationRun CRUD
============================================================

Handles all database operations for SimulationRun records.

Design principles:
  1. Session is injected — repository never calls commit() or rollback().
     The session lifecycle belongs to get_db() or a service layer.
  2. flush() after add()/delete() forces SQLAlchemy to send the SQL to
     the DB engine within the current transaction without committing.
     This lets callers read back auto-generated values (timestamps, etc.)
     while still being able to rollback the full transaction.
  3. refresh() after flush() reloads server-generated columns from the DB
     (e.g. created_at, updated_at set by server_default=func.now()).
  4. All queries use the SQLAlchemy 2.0 select() API — NOT the legacy
     session.query() API.  select() is the future-proof path.

Methods:
    save_simulation(run)          → SimulationRun
    get_simulation(run_id)        → SimulationRun | None
    get_all_simulations(...)      → list[SimulationRun]
    delete_simulation(run_id)     → bool

FastAPI dependency:
    from backend.app.db.session import get_db
    from backend.app.repositories.simulation_repository import SimulationRepository

    def get_simulation_repo(db: Session = Depends(get_db)) -> SimulationRepository:
        return SimulationRepository(db)
"""

import uuid
import datetime
import logging
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from backend.app.models.simulation_run import SimulationRun

logger = logging.getLogger(__name__)


class SimulationRepository:
    """Data access layer for SimulationRun records.

    Instantiate with an active SQLAlchemy Session.
    Never holds state beyond the session — safe to create per-request.

    Args:
        db: An open SQLAlchemy Session (injected by FastAPI Depends(get_db)).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Write operations ──────────────────────────────────────────────────

    def save_simulation(self, run: SimulationRun) -> SimulationRun:
        """Persist a SimulationRun to the database.

        Accepts a fully constructed SimulationRun ORM instance.  If the run's
        `id` is already set (pre-generated UUID), the existing ID is kept.
        If `id` is None, SQLAlchemy generates a new UUID on flush.

        The session is NOT committed here.  The caller (service layer or
        FastAPI route's get_db dependency) owns the transaction.

        Args:
            run: A SimulationRun instance (not yet added to the session, or
                 already in 'transient' / 'detached' state).

        Returns:
            The same SimulationRun instance after flush and refresh.
            All server-generated columns (created_at, updated_at) are populated.

        Example:
            run = SimulationRun(
                id=uuid.uuid4(),
                crop="wheat",
                variety="apache",
                latitude=52.0,
                longitude=5.5,
                sowing_date=date(2020, 10, 15),
                use_real_weather=True,
                use_real_soil=True,
                run_type="baseline",
                model_name="Wofost72_WLP_FD",
                model_version="7.2",
                status="completed",
            )
            saved = repo.save_simulation(run)
            print(saved.created_at)  # populated after flush
        """
        self.db.add(run)

        # flush() sends INSERT to the DB within the current transaction.
        # Required before refresh() so the row exists in the DB.
        self.db.flush()

        # refresh() re-reads the row from the DB, populating server_default
        # columns (created_at, updated_at) that the app didn't set.
        self.db.refresh(run)

        logger.info(
            "Saved SimulationRun id=%s crop=%s/%s status=%s yield=%.1f kg/ha",
            run.id, run.crop, run.variety, run.status, run.yield_kg_ha or 0.0,
        )
        return run

    def delete_simulation(self, run_id: uuid.UUID) -> bool:
        """Delete a SimulationRun and all its DailyOutput records.

        DailyOutput deletion is handled by the DB-level CASCADE defined in
        DailyOutput.simulation_run_id (ondelete="CASCADE").  SQLAlchemy also
        respects the Python-level cascade="all, delete-orphan" on the
        SimulationRun.daily_outputs relationship for ORM-tracked objects.

        Args:
            run_id: UUID of the SimulationRun to delete.

        Returns:
            True if a record was found and deleted.
            False if no record with that ID exists (idempotent delete).
        """
        run = self.db.get(SimulationRun, run_id)
        if run is None:
            logger.warning("delete_simulation: id=%s not found", run_id)
            return False

        self.db.delete(run)
        self.db.flush()
        logger.info("Deleted SimulationRun id=%s (and its DailyOutputs)", run_id)
        return True

    # ── Read operations ───────────────────────────────────────────────────

    def get_simulation(self, run_id: uuid.UUID) -> Optional[SimulationRun]:
        """Fetch a single SimulationRun by its UUID primary key.

        Uses Session.get() which first checks the Session identity map
        (in-memory cache) before issuing a SELECT — efficient for repeated
        lookups within the same request.

        Args:
            run_id: UUID primary key of the SimulationRun.

        Returns:
            The SimulationRun instance, or None if not found.
        """
        run = self.db.get(SimulationRun, run_id)
        if run is None:
            logger.debug("get_simulation: id=%s not found", run_id)
        return run

    def get_all_simulations(
        self,
        *,
        field_id: Optional[uuid.UUID] = None,
        crop: Optional[str] = None,
        variety: Optional[str] = None,
        status: Optional[str] = None,
        run_type: Optional[str] = None,
        sowing_date_from: Optional[datetime.date] = None,
        sowing_date_to: Optional[datetime.date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SimulationRun]:
        """Fetch SimulationRun records with optional filtering and pagination.

        All filter arguments are optional and combinable.  This design avoids
        a proliferation of single-purpose query methods while staying readable.

        Args:
            field_id:         Filter by parent Field UUID.
            crop:             Filter by crop name (exact, case-sensitive, e.g. "wheat").
            variety:          Filter by variety name (exact, e.g. "apache").
            status:           Filter by status ("completed", "failed", "running", …).
            run_type:         Filter by run_type ("baseline", "irrigated", …).
            sowing_date_from: Include only runs with sowing_date >= this date.
            sowing_date_to:   Include only runs with sowing_date <= this date.
            limit:            Maximum number of records to return (default 100).
            offset:           Number of records to skip for pagination (default 0).

        Returns:
            List of SimulationRun instances ordered by created_at descending
            (newest first). Empty list if no records match.

        Example — all failed wheat runs:
            repo.get_all_simulations(crop="wheat", status="failed")

        Example — paginated baseline runs for a field:
            repo.get_all_simulations(field_id=fid, run_type="baseline", limit=20, offset=40)
        """
        # Build the base query using SA 2.0 select() — not session.query()
        stmt = select(SimulationRun)

        # Apply optional filters — each guard prevents adding a WHERE clause
        # when the argument is None, keeping the query selective.
        filters = []
        if field_id is not None:
            filters.append(SimulationRun.field_id == field_id)
        if crop is not None:
            filters.append(SimulationRun.crop == crop)
        if variety is not None:
            filters.append(SimulationRun.variety == variety)
        if status is not None:
            filters.append(SimulationRun.status == status)
        if run_type is not None:
            filters.append(SimulationRun.run_type == run_type)
        if sowing_date_from is not None:
            filters.append(SimulationRun.sowing_date >= sowing_date_from)
        if sowing_date_to is not None:
            filters.append(SimulationRun.sowing_date <= sowing_date_to)

        if filters:
            stmt = stmt.where(and_(*filters))

        # Order newest-first; covered by ix_simrun_field_sowing for field_id queries.
        stmt = stmt.order_by(SimulationRun.created_at.desc())

        # Pagination — offset/limit are applied after ordering.
        stmt = stmt.offset(offset).limit(limit)

        results = self.db.execute(stmt).scalars().all()
        logger.debug(
            "get_all_simulations: returned %d records (offset=%d limit=%d)",
            len(results), offset, limit,
        )
        return list(results)
