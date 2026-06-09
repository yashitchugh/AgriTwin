"""
models/field.py — Field ORM Model
===================================

A Field represents a single cultivated plot or parcel within a Farm.
It carries the GPS coordinates and physical attributes that remain constant
across growing seasons.  Each SimulationRun is anchored to a Field so that
multiple seasons of simulation data can be compared for the same location.

Why store lat/lon on both Field and SimulationRun?
    Field.latitude/longitude — the canonical, permanent location of the plot.
    SimulationRun.latitude/longitude — the exact coordinates used for the
    simulation (which may differ slightly if the user overrides them or if a
    centroid calculation is applied).  Storing both allows data-quality audits.

PostgreSQL note:
    For spatial queries (distance, bounding box), the `latitude`/`longitude`
    pair can be replaced by a PostGIS `GEOGRAPHY(POINT, 4326)` column.
    The schema is kept simple (Float pair) for SQLite compatibility.
    A PostGIS column can be added via Alembic migration when PostgreSQL is used.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.farm import Farm
    from backend.app.models.simulation_run import SimulationRun


class Field(TimestampMixin, Base):
    """A single cultivated field/plot belonging to a Farm.

    Fields are the unit of spatial indexing: weather and soil data are fetched
    at the field's GPS coordinates and cached keyed by those coordinates.

    Table: fields
    """

    __tablename__ = "fields"

    # ── Composite indexes ─────────────────────────────────────────────────
    # Spatial lookup: "give me all fields near lat 28.6, lon 77.2"
    # Both partial-scan and bounding-box queries benefit from this index.
    __table_args__ = (
        Index("ix_fields_lat_lon", "latitude", "longitude"),
    )

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="UUID primary key for this field.",
    )

    # ── Foreign key ───────────────────────────────────────────────────────
    farm_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="FK → farms.id.  Deleting the parent Farm cascades to this Field.",
    )

    # ── Identity ──────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        doc=(
            "Human-readable field name or code "
            "(e.g. 'Block A North', 'Plot 3', 'Kharif Field 2020')."
        ),
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional notes on the field (variety history, soil treatment, drainage, etc.).",
    )

    # ── Location ──────────────────────────────────────────────────────────
    latitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc=(
            "Field centroid latitude in decimal degrees (WGS84). "
            "Range: -90.0 to 90.0. "
            "Used as the coordinate for NASA POWER and SoilGrids API lookups."
        ),
    )

    longitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc=(
            "Field centroid longitude in decimal degrees (WGS84). "
            "Range: -180.0 to 180.0."
        ),
    )

    area_ha: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Field area in hectares [ha]. "
            "Not used by the simulation engine (WOFOST normalises to per-hectare units) "
            "but stored for reporting and upscaling to total production."
        ),
    )

    elevation_m: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Field elevation above mean sea level [m]. "
            "Used by WOFOST for Penman-Monteith reference ET calculation. "
            "If None, the simulation service falls back to 10 m (a neutral default)."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    farm: Mapped["Farm"] = relationship(
        "Farm",
        back_populates="fields",
        doc="Parent Farm that owns this field.",
    )

    simulation_runs: Mapped[list["SimulationRun"]] = relationship(
        "SimulationRun",
        back_populates="field",
        # CASCADE: deleting a Field deletes all its SimulationRuns
        # (and by extension all DailyOutputs via SimulationRun's cascade).
        cascade="all, delete-orphan",
        doc="All simulation runs ever executed for this field.",
    )

    def __repr__(self) -> str:
        return (
            f"<Field id={self.id!s:.8} name={self.name!r} "
            f"lat={self.latitude} lon={self.longitude}>"
        )
