"""
repositories/daily_output_repository.py — DailyOutput Bulk I/O
===============================================================

Handles batch insert and retrieval of DailyOutput records — the highest-volume
table in AgriTwin (up to ~380 rows per simulation run).

Performance design:
  save_daily_outputs() uses session.add_all() with a pre-constructed list of
  ORM objects.  For very large datasets (>10 000 rows), the alternative is
  session.execute(insert(DailyOutput), list_of_dicts) which bypasses ORM
  overhead.  For typical AgriTwin simulations (≤ 730 rows), add_all() is
  appropriate and keeps objects in the session identity map for later access.

  get_daily_outputs() returns rows ordered by date ASC — matching the
  natural chronological order of a simulation time series.  An optional
  date-range filter is provided for partial retrieval (e.g. fetch only the
  grain-filling window for analysis).

Session lifecycle:
  Repositories never commit.  flush() is called after add_all() to send
  the INSERT to the DB engine within the current transaction, allowing the
  caller to read back auto-incremented IDs if needed.

Methods:
    save_daily_outputs(outputs)              → list[DailyOutput]
    get_daily_outputs(simulation_run_id, …)  → list[DailyOutput]

FastAPI dependency:
    def get_daily_output_repo(db: Session = Depends(get_db)):
        return DailyOutputRepository(db)
"""

import datetime
import logging
import uuid
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from backend.app.models.daily_output import DailyOutput

logger = logging.getLogger(__name__)


class DailyOutputRepository:
    """Data access layer for DailyOutput records.

    Always operate on a single SimulationRun's outputs — DailyOutput rows
    are never queried across multiple runs simultaneously.

    Args:
        db: An open SQLAlchemy Session (injected by FastAPI Depends(get_db)).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Write operations ──────────────────────────────────────────────────

    def save_daily_outputs(
        self, outputs: list[DailyOutput]
    ) -> list[DailyOutput]:
        """Bulk-insert a list of DailyOutput ORM objects for one SimulationRun.

        All objects must have simulation_run_id set before calling this method.
        The list is expected to be ordered by date ASC (as produced by the
        simulation engine's output parser), but ordering is not enforced here.

        Args:
            outputs: List of DailyOutput instances. All must share the same
                     simulation_run_id. Empty list is silently accepted (no-op).

        Returns:
            The same list of DailyOutput instances after flush.
            Auto-incremented `id` fields are populated after flush.

        Performance note:
            For simulations up to 730 days, add_all() is efficient.
            For bulk imports of historical data (thousands of runs), replace
            with session.execute(insert(DailyOutput), [dict, ...]) to bypass
            ORM object tracking overhead and reduce memory usage.

        Example:
            outputs = [
                DailyOutput(simulation_run_id=run_id, date=date(2020,10,15),
                            dvs=0.0, lai=0.0, sm=0.22),
                DailyOutput(simulation_run_id=run_id, date=date(2020,10,16),
                            dvs=0.01, lai=0.01, sm=0.21),
                ...
            ]
            saved = repo.save_daily_outputs(outputs)
        """
        if not outputs:
            logger.debug("save_daily_outputs: empty list — no-op")
            return []

        # Validate all rows share the same run — catches programming errors early.
        run_ids = {o.simulation_run_id for o in outputs}
        if len(run_ids) > 1:
            raise ValueError(
                f"save_daily_outputs received outputs for {len(run_ids)} different "
                f"simulation_run_ids.  Each call must contain outputs for exactly one run."
            )

        self.db.add_all(outputs)

        # flush() sends all INSERTs in one round-trip, within the current
        # transaction.  Auto-incremented IDs are populated after flush.
        self.db.flush()

        run_id = next(iter(run_ids))
        logger.info(
            "Saved %d DailyOutput rows for simulation_run_id=%s",
            len(outputs), run_id,
        )
        return outputs

    # ── Read operations ───────────────────────────────────────────────────

    def get_daily_outputs(
        self,
        simulation_run_id: uuid.UUID,
        *,
        date_from: Optional[datetime.date] = None,
        date_to: Optional[datetime.date] = None,
    ) -> list[DailyOutput]:
        """Fetch all DailyOutput rows for a SimulationRun, ordered by date ASC.

        The query uses the composite index ix_daily_run_date
        (simulation_run_id, date) for an efficient single B-tree scan without
        a separate sort step.

        Args:
            simulation_run_id: UUID of the parent SimulationRun.
            date_from:         Optional lower date bound (inclusive).
                               Useful for fetching only the grain-filling window.
            date_to:           Optional upper date bound (inclusive).

        Returns:
            Ordered list of DailyOutput instances (date ASC).
            Empty list if the run has no output rows or the run_id is unknown.

        Example — full time series:
            rows = repo.get_daily_outputs(run_id)

        Example — grain filling window only (DVS 1→2):
            rows = repo.get_daily_outputs(
                run_id,
                date_from=date(2021, 4, 1),
                date_to=date(2021, 6, 30),
            )
        """
        stmt = (
            select(DailyOutput)
            .where(DailyOutput.simulation_run_id == simulation_run_id)
        )

        if date_from is not None:
            stmt = stmt.where(DailyOutput.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(DailyOutput.date <= date_to)

        # ORDER BY date ASC — covered by the composite index, no extra sort.
        stmt = stmt.order_by(DailyOutput.date.asc())

        rows = self.db.execute(stmt).scalars().all()
        logger.debug(
            "get_daily_outputs: simulation_run_id=%s → %d rows",
            simulation_run_id, len(rows),
        )
        return list(rows)
