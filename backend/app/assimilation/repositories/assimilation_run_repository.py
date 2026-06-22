"""
assimilation/repositories/assimilation_run_repository.py
========================================================

Repository layer for AssimilationRun operations.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.assimilation_run import AssimilationRun


class AssimilationRunRepository:
    """Repository for CRUD operations on AssimilationRun."""

    def __init__(self, session: Session):
        self.session = session

    def create_run(self, run: AssimilationRun) -> AssimilationRun:
        """Persist a new assimilation run to the database."""
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def get_run(self, run_id: uuid.UUID) -> Optional[AssimilationRun]:
        """Retrieve an assimilation run by its ID."""
        return self.session.get(AssimilationRun, run_id)

    def get_by_simulation(self, simulation_id: uuid.UUID) -> List[AssimilationRun]:
        """Retrieve all assimilation runs linked to a specific SimulationRun."""
        stmt = (
            select(AssimilationRun)
            .where(AssimilationRun.simulation_id == simulation_id)
            .order_by(AssimilationRun.started_at.desc())
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def update_run(self, run: AssimilationRun) -> AssimilationRun:
        """Update and commit changes to an existing run."""
        self.session.commit()
        self.session.refresh(run)
        return run
