"""
assimilation/repositories/assimilation_state_repository.py
==========================================================

Repository layer for AssimilationState operations.
"""

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.assimilation.models.assimilation_state import AssimilationState


class AssimilationStateRepository:
    """Repository for CRUD operations on AssimilationState."""

    def __init__(self, session: Session):
        self.session = session

    def save_state(self, state: AssimilationState) -> AssimilationState:
        """Persist a new assimilation state to the database."""
        self.session.add(state)
        self.session.commit()
        self.session.refresh(state)
        return state

    def get_latest(self, field_id: uuid.UUID) -> Optional[AssimilationState]:
        """Get the most recent assimilation state for a given field."""
        stmt = (
            select(AssimilationState)
            .where(AssimilationState.field_id == field_id)
            .order_by(AssimilationState.assimilation_time.desc())
            .limit(1)
        )
        result = self.session.execute(stmt)
        return result.scalars().first()

    def get_history(self, field_id: uuid.UUID) -> List[AssimilationState]:
        """Get all assimilation states for a field, ordered by time."""
        stmt = (
            select(AssimilationState)
            .where(AssimilationState.field_id == field_id)
            .order_by(AssimilationState.assimilation_time.asc())
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())
