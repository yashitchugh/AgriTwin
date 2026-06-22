"""
assimilation/models/assimilation_state.py
=========================================

Persists the internal state of the Ensemble Kalman Filter (EnKF) for auditing,
uncertainty analysis, and debugging.
"""

import datetime
import uuid
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.field import Field
    from backend.app.models.simulation_run import SimulationRun
    from backend.app.models.assimilation_run import AssimilationRun


class AssimilationState(TimestampMixin, Base):
    """Stores the internal matrices and state vectors of an EnKF update step.
    
    Provides full reproducibility and traceability of the assimilation process.
    """

    __tablename__ = "assimilation_states"

    __table_args__ = (
        Index("ix_assimilation_field_time", "field_id", "assimilation_time"),
        Index("ix_assimilation_run_time", "simulation_run_id", "assimilation_time"),
        Index("ix_assimilation_assim_run_time", "assimilation_run_id", "assimilation_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    field_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fields.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    simulation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    assimilation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("assimilation_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    assimilation_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    ensemble_mean: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ensemble_covariance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    observation_vector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    innovation_vector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    kalman_gain: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_state_vector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    forecast_state_vector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    number_of_members: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    field: Mapped[Optional["Field"]] = relationship("Field", foreign_keys=[field_id])
    simulation_run: Mapped[Optional["SimulationRun"]] = relationship("SimulationRun", foreign_keys=[simulation_run_id])
    assimilation_run: Mapped[Optional["AssimilationRun"]] = relationship(
        "AssimilationRun",
        foreign_keys=[assimilation_run_id],
        back_populates="assimilation_states",
    )

    def __repr__(self) -> str:
        return (
            f"<AssimilationState id={self.id!s:.8} "
            f"time={self.assimilation_time.date()} "
            f"obs_count={self.observation_count} "
            f"members={self.number_of_members}>"
        )
