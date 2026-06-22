"""
models/assimilation_run.py — AssimilationRun ORM Model
======================================================

An AssimilationRun records one complete EnKF execution.
"""

import datetime
import uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    DateTime, ForeignKey, Integer, JSON, String, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.simulation_run import SimulationRun
    from backend.app.assimilation.models.assimilation_state import AssimilationState


class AssimilationRun(TimestampMixin, Base):
    """One EnKF execution associated with a SimulationRun.

    Table: assimilation_runs
    """

    __tablename__ = "assimilation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="UUID primary key.",
    )

    simulation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="FK → simulation_runs.id.",
    )

    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Timestamp when the assimilation run started."
    )

    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp when the assimilation run completed."
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
        doc="State of the run (PENDING, RUNNING, COMPLETED, FAILED)."
    )

    ensemble_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Number of ensemble members."
    )

    total_cycles: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Total number of checked assimilation cycles."
    )

    executed_cycles: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of executed EnKF updates."
    )

    skipped_cycles: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of skipped cycles (no observations)."
    )

    observations_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of observations assimilated during the run."
    )

    config_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        doc="EnKF and execution configuration stored as JSON."
    )

    # Relationships
    simulation_run: Mapped["SimulationRun"] = relationship(
        "SimulationRun",
        back_populates="assimilation_runs",
    )

    assimilation_states: Mapped[list["AssimilationState"]] = relationship(
        "AssimilationState",
        back_populates="assimilation_run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<AssimilationRun id={self.id!s:.8} "
            f"simulation_id={self.simulation_id!s:.8} "
            f"status={self.status!r} "
            f"ensemble_size={self.ensemble_size}>"
        )
