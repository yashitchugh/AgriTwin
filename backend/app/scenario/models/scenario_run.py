"""
scenario/models/scenario_run.py — ScenarioRun ORM Model
=========================================================

A ScenarioRun records the outcome of executing one candidate value
from a ScenarioDefinition.  Each ScenarioRun links to:

  1. Its parent ScenarioDefinition (which parameter was varied)
  2. The concrete SimulationRun that WOFOST executed (the actual results)

The agronomic summary columns (yield_kg_ha, peak_lai, etc.) are
DENORMALIZED copies of the linked SimulationRun's scalar results.
Denormalization serves two purposes:
  a. Fast scenario comparison queries without JOINs across multiple tables.
  b. Stable snapshot: even if the SimulationRun is updated or deleted,
     the scenario's recorded metrics remain intact for audit purposes.

Water-use diagnostics (water_stress_days, total_irrigation_mm) are scenario-
specific aggregates computed from the DailyOutput time series at run time
and cached here.  They are not stored in SimulationRun because they are
only meaningful in the context of a scenario comparison (e.g. "how much
less water does irrigation schedule A need vs. schedule B?").

Table: scenario_runs

NOT implemented here:
  - Computation logic for water_stress_days or total_irrigation_mm
  - Service layer that populates this record after WOFOST runs
  - API routes
"""

import datetime
import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.scenario.models.scenario_definition import ScenarioDefinition


class ScenarioRun(TimestampMixin, Base):
    """One executed simulation within a ScenarioDefinition.

    Created by the scenario execution service, one per candidate value in
    ScenarioDefinition.parameter_values.

    Table: scenario_runs
    """

    __tablename__ = "scenario_runs"

    __table_args__ = (
        # All runs for a scenario — used when loading comparison results
        Index("ix_scenrun_scenario_id", "scenario_id"),
        # All scenario runs backed by a given simulation run
        Index("ix_scenrun_simulation_id", "simulation_id"),
    )

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc=(
            "UUID primary key for this scenario run record. "
            "Distinct from simulation_id — one ScenarioRun wraps one SimulationRun."
        ),
    )

    # ── Parent scenario ───────────────────────────────────────────────────
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scenario_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc=(
            "FK → scenario_definitions.id — the parent ScenarioDefinition "
            "that spawned this run. "
            "CASCADE: deleting the ScenarioDefinition cascades to all its ScenarioRuns."
        ),
    )

    # ── The candidate value that was tested ───────────────────────────────
    parameter_value: Mapped[Any] = mapped_column(
        JSON,
        nullable=False,
        doc=(
            "The specific candidate value tested in this run. "
            "This is one element from ScenarioDefinition.parameter_values. "
            "Type depends on generator_type: "
            "  SOWING_DATE → ISO date string: '2020-10-15' "
            "  IRRIGATION  → schedule dict:   {'events': [{date, amount_mm}, ...]} "
            "  VARIETY     → variety string:  'apache' "
            "Stored as JSON so that complex types (irrigation schedules) "
            "are preserved without a separate events table."
        ),
    )

    # ── Link to the actual WOFOST simulation ──────────────────────────────
    simulation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "FK → simulation_runs.id — the SimulationRun created by WOFOST "
            "for this candidate value. "
            "NULL if the simulation has not been executed yet (e.g. queued state). "
            "SET NULL on delete so that deleting the underlying SimulationRun "
            "invalidates this run's reference without cascading to the scenario."
        ),
    )

    # ── Denormalized agronomic results ────────────────────────────────────
    # Copied from SimulationRun at write time for fast comparison queries.
    # These are intentionally redundant with simulation_run scalar columns.

    yield_kg_ha: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Simulated grain/storage-organ yield [kg dry matter / ha]. "
            "Denormalized copy of SimulationRun.yield_kg_ha. "
            "NULL if the run has not completed or the crop did not reach maturity. "
            "Primary ranking metric in yield-maximization scenario comparisons."
        ),
    )

    peak_lai: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Maximum Leaf Area Index reached during the season [m²/m²]. "
            "Denormalized copy of SimulationRun.peak_lai. "
            "Proxy for canopy development vigor; correlates with radiation interception "
            "and can be validated against satellite LAI products."
        ),
    )

    harvest_index: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Harvest Index = TWSO / TAGP [-]. "
            "Denormalized copy of SimulationRun.harvest_index. "
            "Fraction of total biomass that is economically harvestable. "
            "Useful for comparing varieties and irrigation strategies: "
            "water stress typically reduces HI by diverting assimilates from grain fill."
        ),
    )

    final_tagp: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Above-Ground Production at season end [kg dry matter / ha]. "
            "Denormalized copy of SimulationRun.final_tagp. "
            "Sum of leaf + stem + storage organ biomass. "
            "Higher TAGP with same or lower yield indicates more vegetative growth "
            "and lower partitioning efficiency (low HI scenario)."
        ),
    )

    final_twso: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total Weight of Storage Organs at season end [kg / ha]. "
            "Denormalized copy of SimulationRun.final_twso. "
            "Numerically equal to yield_kg_ha. "
            "Stored using WOFOST variable naming for traceability to engine output."
        ),
    )

    # ── Water-use diagnostics (scenario-specific aggregates) ───────────────
    # NOT copied from SimulationRun — these are computed from DailyOutput
    # during scenario execution and are meaningful only in a comparison context.

    water_stress_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc=(
            "Number of simulated days on which the crop experienced water stress, "
            "defined as RFTRA < 1.0 (actual transpiration < potential). "
            "Computed by counting DailyOutput rows where rftra < 1.0. "
            "Key irrigation-adequacy metric: a run with 0 stress days had "
            "sufficient water supply throughout the season. "
            "Lower is better when comparing irrigation schedules. "
            "NULL if the run has not completed or DailyOutputs are unavailable."
        ),
    )

    total_irrigation_mm: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Total volume of irrigation water applied across the season [mm]. "
            "Summed from ScenarioDefinition.parameter_value['events'][*]['amount_mm'] "
            "for IRRIGATION scenarios. "
            "0.0 for SOWING_DATE and VARIETY scenarios (no irrigation applied). "
            "Used with water_stress_days to compute water-use efficiency: "
            "higher yield per mm irrigation = more efficient schedule. "
            "NULL only if the run has not been populated by the execution service."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    scenario: Mapped["ScenarioDefinition"] = relationship(
        "ScenarioDefinition",
        back_populates="runs",
        doc="Parent ScenarioDefinition that spawned this run.",
    )

    def __repr__(self) -> str:
        return (
            f"<ScenarioRun id={self.id!s:.8} "
            f"scenario={self.scenario_id!s:.8} "
            f"value={self.parameter_value!r} "
            f"yield={self.yield_kg_ha} "
            f"stress_days={self.water_stress_days}>"
        )
