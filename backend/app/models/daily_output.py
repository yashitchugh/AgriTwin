"""
models/daily_output.py — DailyOutput ORM Model
================================================

DailyOutput stores one day of WOFOST simulation state for a SimulationRun.
It is the highest-volume table in the database:

    Rows per run ≈ max_duration + 14 (pre-sowing buffer) ≈ 379 rows / season

For a farm with 100 fields × 3 seasons × 379 days = ~114 000 rows.
At ~200 bytes per row (uncompressed), that's ~22 MB — well within SQLite's
practical limits.  PostgreSQL can handle tens of millions of rows trivially.

Integer primary key:
    DailyOutput intentionally uses an auto-increment integer PK (not UUID)
    because:
      - Rows are never referenced externally (only queried via parent run).
      - Sequential integer PKs are faster to insert in bulk.
      - The table can hold billions of rows without PK collision.

Variable descriptions — WOFOST 7.2 documentation:
  Always populated in batch mode (in OUTPUT_VARS):
    DVS    Development Stage [-]: 0=emergence, 1=anthesis, 2=maturity
    LAI    Leaf Area Index [m²/m²]: canopy green leaf area per ground area
    SM     Volumetric soil moisture [cm³/cm³]: in root zone
    TAGP   Total Above-Ground Production [kg/ha]: leaves+stems+organs
    TWSO   Total Weight Storage Organs [kg/ha]: grain/seed yield accumulator
    RFTRA  Relative water stress factor [-]: TRA/TRAMX, 0=full stress, 1=none
    TRA    Actual Transpiration [cm/day]
    RD     Root Depth [cm]
    TWLV   Total Weight Leaves [kg/ha]
    TWST   Total Weight Stems [kg/ha]
    TWRT   Total Weight Roots [kg/ha]

  Live-state variables (NULL in batch mode; populated in step-by-step/EnKF mode):
    WLV    Actual leaf weight [kg/ha] at current timestep (pre-senescence)
    WST    Actual stem weight [kg/ha]
    WRT    Actual root weight [kg/ha]
    WSO    Actual storage organ weight [kg/ha]
    EVS    Actual Soil Evaporation [cm/day]

Digital Twin readiness:
    The full set of stored variables (both batch + live-state groups) forms the
    complete state vector required by the FieldState abstraction in
    backend/app/twin/field_state.py.  Future EnKF modules consume FieldState
    objects, not raw DailyOutput rows, decoupling the assimilation layer from
    the database schema.
"""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.models.simulation_run import SimulationRun


class DailyOutput(Base):
    """One simulated day of WOFOST output for a SimulationRun.

    All WOFOST state variables are Optional (None before crop emergence or
    after crop death).  Only `date` and `simulation_run_id` are always present.

    Table: daily_outputs
    """

    __tablename__ = "daily_outputs"

    # ── Indexes ───────────────────────────────────────────────────────────
    __table_args__ = (
        # Primary access pattern: "give me the full time series for run X,
        # ordered by date."  This composite index covers both the filter and
        # the sort in a single B-tree scan.
        Index("ix_daily_run_date", "simulation_run_id", "date"),
    )

    # ── Primary key — integer, not UUID (see module docstring) ────────────
    id: Mapped[int] = mapped_column(
        # Integer maps to SQLite INTEGER ROWID — the only type SQLite supports
        # for autoincrement PKs.  On PostgreSQL, Alembic emits BIGSERIAL
        # automatically for Mapped[int] with autoincrement=True.
        Integer,
        primary_key=True,
        autoincrement=True,
        doc=(
            "Auto-increment integer primary key. "
            "Not exposed in the public API — DailyOutput rows are always "
            "accessed via their parent SimulationRun."
        ),
    )

    # ── Foreign key ───────────────────────────────────────────────────────
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
        # index=True is covered by ix_daily_run_date — no separate single-column index needed.
        doc="FK → simulation_runs.id. Cascade: deleting the run purges all its daily rows.",
    )

    # ── Simulation date ────────────────────────────────────────────────────
    date: Mapped[datetime.date] = mapped_column(
        Date,
        nullable=False,
        doc=(
            "Calendar date of this simulation timestep (ISO date). "
            "Starts at campaign_start (sowing_date - 14 days) and ends at "
            "harvest/maturity date.  One row per day, no gaps."
        ),
    )

    # ── Development ───────────────────────────────────────────────────────
    dvs: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Development Stage [-]. "
            "0.0 = crop emergence, 1.0 = anthesis (flowering), 2.0 = physiological maturity. "
            "NULL before sowing or if WOFOST did not report this variable."
        ),
    )

    # ── Canopy ────────────────────────────────────────────────────────────
    lai: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Leaf Area Index [m² green leaf / m² ground]. "
            "Key variable for satellite (Sentinel-2/MODIS) assimilation via LAI products. "
            "Rises from emergence, peaks mid-season, then declines as leaves senesce."
        ),
    )

    # ── Biomass pools ─────────────────────────────────────────────────────
    tagp: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Above-Ground Production [kg dry matter / ha]. "
            "Sum: TWLV + TWST + TWSO (excludes roots). "
            "Monotonically increasing until maturity."
        ),
    )

    twso: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Weight of Storage Organs [kg / ha]. "
            "Economic yield component (grain, seeds, tubers). "
            "Zero before anthesis (DVS < 1.0), accumulates rapidly during grain fill."
        ),
    )

    twlv: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Weight of Leaves [kg dry matter / ha]. "
            "Increases during vegetative growth, declines as leaves die and senesce. "
            "Drives LAI: TWLV × SLA = LAI."
        ),
    )

    twst: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Weight of Stems [kg dry matter / ha]. "
            "Peaks around anthesis, then may decline as stem reserves remobilise to grain."
        ),
    )

    twrt: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Weight of Roots [kg dry matter / ha]. "
            "Not included in TAGP (above-ground), but stored for water uptake diagnostics."
        ),
    )

    # ── Live-state organ weights (NULL in batch mode) ──────────────────────
    # These are daily instantaneous weights BEFORE senescence is applied.
    # Available only in step-by-step simulation mode via get_variable().
    # In current batch mode (run_till_terminate) all four are NULL.
    # Future EnKF modules will use these as the assimilation state vector.

    wlv: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual leaf weight [kg dry matter / ha] at current timestep, "
            "BEFORE daily senescence is subtracted. "
            "Distinct from TWLV (cumulative total): WLV represents today's "
            "living leaf mass; TWLV is the running total including senesced material. "
            "NULL in batch mode; populated in step-by-step (EnKF) mode only."
        ),
    )

    wst: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual stem weight [kg dry matter / ha] at current timestep. "
            "Pre-senescence daily value. "
            "NULL in batch mode; populated in step-by-step (EnKF) mode only."
        ),
    )

    wrt: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual root weight [kg dry matter / ha] at current timestep. "
            "Pre-senescence daily value. "
            "NULL in batch mode; populated in step-by-step (EnKF) mode only."
        ),
    )

    wso: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual storage organ weight [kg / ha] at current timestep. "
            "Pre-senescence daily value. Related to TWSO (cumulative). "
            "NULL in batch mode; populated in step-by-step (EnKF) mode only."
        ),
    )


    # ── Water balance ─────────────────────────────────────────────────────
    sm: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Volumetric soil moisture in the root zone [cm³ water / cm³ soil]. "
            "Ranges from SMW (wilting point) to SM0 (saturation). "
            "Present from day 1 of the waterbalance. "
            "Rises visibly on irrigation event dates."
        ),
    )

    rftra: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Reduction factor for transpiration due to water stress [-]. "
            "RFTRA = TRA / TRAMX (actual / potential transpiration). "
            "1.0 = no water stress. 0.0 = complete water stress (crop cannot transpire). "
            "Primary irrigation diagnostic variable."
        ),
    )

    tra: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual Transpiration [cm / day]. "
            "Reduced from potential transpiration (TRAMX) under water stress. "
            "TRA < TRAMX on days with RFTRA < 1.0."
        ),
    )

    evs: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Actual Soil Evaporation from the bare/partially covered soil surface "
            "[cm / day]. "
            "Decreases as LAI increases and canopy shades the soil."
        ),
    )

    # ── Rooting ───────────────────────────────────────────────────────────
    rd: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Root Depth [cm]. "
            "Deepens from RDI (initial root depth, ~10 cm) at sowing toward "
            "RDMCR (maximum crop root depth) at anthesis. "
            "Determines the soil volume accessible for water and nutrient uptake."
        ),
    )

    # ── Relationship ──────────────────────────────────────────────────────
    simulation_run: Mapped["SimulationRun"] = relationship(
        "SimulationRun",
        back_populates="daily_outputs",
        doc="Parent SimulationRun this daily record belongs to.",
    )

    def __repr__(self) -> str:
        return (
            f"<DailyOutput id={self.id} "
            f"run={self.simulation_run_id!s:.8} "
            f"date={self.date} dvs={self.dvs} twso={self.twso}>"
        )
