"""
models/farm.py — Farm ORM Model
================================

A Farm is the top-level organisational unit representing a physical farm
(e.g. "Rampur Research Farm", "Block C — IRRI India").  It acts as the
ownership boundary; all Fields and their Simulation Runs are nested under
a single Farm record.

Hierarchy:
    Farm (1) ──< Field (N) ──< SimulationRun (N) ──< DailyOutput (N)

UUID primary key:
    UUID primary keys are used for all aggregate roots (Farm, Field,
    SimulationRun) to:
      - Allow client-side ID generation (useful for optimistic inserts)
      - Avoid sequential-ID enumeration attacks in the API
      - Survive database merges without ID collisions

PostgreSQL note:
    On PostgreSQL the `Uuid` type maps to a native `UUID` column.
    On SQLite it maps to CHAR(36).  No model change is needed when
    migrating between the two.
"""

import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin


class Farm(TimestampMixin, Base):
    """Top-level grouping of fields belonging to a single farm entity.

    One farm owns one or more Fields.  Deleting a Farm cascades the delete
    to all its Fields (and transitively to their SimulationRuns and
    DailyOutputs).

    Table: farms
    """

    __tablename__ = "farms"

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        # Uuid: SQLAlchemy 2.0 cross-dialect UUID type.
        # native_uuid=True → use native UUID on PostgreSQL, CHAR(36) on SQLite.
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Universally unique identifier for this farm. Generated client- or server-side.",
    )

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        doc="Human-readable farm name (e.g. 'Rampur Research Station'). Must be non-empty.",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional free-text description of the farm (location, ownership, notes).",
    )

    # ── Contact / ownership (future) ──────────────────────────────────────
    owner_name: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        doc=(
            "Name of the farm owner or responsible agronomist. "
            "Placeholder for future user authentication integration."
        ),
    )

    country_code: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
        doc=(
            "ISO 3166-1 alpha-2 or alpha-3 country code (e.g. 'IN', 'NL', 'KE'). "
            "Used for regional aggregations and regulatory reporting."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    fields: Mapped[list["Field"]] = relationship(  # type: ignore[name-defined]
        "Field",
        back_populates="farm",
        # CASCADE: deleting a Farm deletes all its Fields.
        # The cascade continues to SimulationRun and DailyOutput via Field's cascade.
        cascade="all, delete-orphan",
        doc="All fields (plots) belonging to this farm.",
    )

    def __repr__(self) -> str:
        return f"<Farm id={self.id!s:.8} name={self.name!r}>"
