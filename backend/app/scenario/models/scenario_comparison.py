"""
scenario/models/scenario_comparison.py — ScenarioComparison ORM Model
=======================================================================

A ScenarioComparison is the distilled, human-readable result of a
ScenarioDefinition.  After all ScenarioRuns complete, the comparison
service computes which run "won" on each agronomic dimension and stores
the cross-run delta statistics here.

Why a separate table rather than computing on-the-fly?
  - Computed comparisons can be expensive (N runs × M daily rows).
  - Cached results enable instant API responses for dashboards.
  - The comparison can be re-computed (e.g. after adding a new run)
    by updating this single row.
  - Stores the ID of the "winner" run for each dimension so the client
    can fetch that run's full daily time series if needed.

One ScenarioDefinition has at most ONE ScenarioComparison (one-to-one).
The comparison is replaced (not versioned) when re-computed.

Delta metrics are always:
    (best_run_value - baseline_value) / baseline_value × 100   for percent
    (best_run_value - baseline_value)                           for absolute

A positive delta_yield_percent means the best scenario produced MORE
yield than the baseline.  A negative delta_stress_days means fewer
stress days than baseline (better water management).

Table: scenario_comparisons

NOT implemented here:
  - Comparison computation logic
  - Re-computation trigger
  - API routes
"""

import datetime
import uuid
from typing import Any, TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.scenario.models.scenario_definition import ScenarioDefinition


class ScenarioComparison(TimestampMixin, Base):
    """Cached comparison results across all runs in a ScenarioDefinition.

    Populated by the comparison service after all ScenarioRuns complete.
    One row per ScenarioDefinition (enforced by unique constraint).

    Table: scenario_comparisons
    """

    __tablename__ = "scenario_comparisons"

    __table_args__ = (
        # One comparison per scenario definition — replace, don't append.
        UniqueConstraint("scenario_id", name="uq_scencomp_scenario"),
    )

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="UUID primary key for this comparison record.",
    )

    # ── Parent scenario ───────────────────────────────────────────────────
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("scenario_definitions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        doc=(
            "FK → scenario_definitions.id. "
            "CASCADE: deleting the ScenarioDefinition also deletes its comparison. "
            "UNIQUE: enforces the one-to-one constraint at the DB level."
        ),
    )

    # ── Winner run identifiers ────────────────────────────────────────────
    # Each field points to the UUID of the ScenarioRun that performed best
    # on that dimension.  The client can use these IDs to fetch the winning
    # run's full SimulationRun data and daily time series.

    best_yield_simulation: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        doc=(
            "UUID of the ScenarioRun (scenario_runs.id) that achieved the "
            "highest yield_kg_ha across all runs in this scenario. "
            "NULL if no run has completed with a non-NULL yield. "
            "Use this ID to fetch the winner's daily LAI, SM, TAGP time series "
            "via GET /simulations/{simulation_id}/daily."
        ),
    )

    lowest_water_use_simulation: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        doc=(
            "UUID of the ScenarioRun (scenario_runs.id) with the lowest "
            "total_irrigation_mm while still achieving acceptable yield "
            "(defined as yield >= 0.9 × best_yield_kg_ha by the comparison service). "
            "NULL for SOWING_DATE and VARIETY scenarios where all runs have "
            "total_irrigation_mm = 0 (no irrigation applied). "
            "Useful for identifying the minimum-irrigation schedule that does "
            "not significantly sacrifice yield."
        ),
    )

    lowest_stress_simulation: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        doc=(
            "UUID of the ScenarioRun (scenario_runs.id) with the fewest "
            "water_stress_days (days with RFTRA < 1.0). "
            "NULL if no run has a non-NULL water_stress_days value. "
            "The lowest-stress run may not be the highest-yield run if extra "
            "irrigation was applied beyond what the crop could use."
        ),
    )

    # ── Cross-run delta metrics ───────────────────────────────────────────
    # All deltas compare the BEST run against the BASELINE run.
    # Baseline = the SimulationRun referenced by ScenarioDefinition.base_simulation_id.

    delta_yield_percent: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Yield improvement of the best-yield run relative to the baseline [%]. "
            "Formula: (best_yield - baseline_yield) / baseline_yield × 100. "
            "Positive = better than baseline. Negative = worse. "
            "Example: +12.3 means the best sowing date yielded 12.3% more "
            "than the original simulation. "
            "NULL if baseline or best run yield is unavailable."
        ),
    )

    delta_irrigation_mm: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Difference in total irrigation between the lowest-water-use run "
            "and the baseline [mm]. "
            "Formula: lowest_water_use_irrigation_mm - baseline_irrigation_mm. "
            "Negative = the efficient schedule uses LESS water than baseline. "
            "Example: -60.0 means 60 mm less irrigation was needed while "
            "still achieving ≥90% of best yield. "
            "NULL for scenarios where irrigation is not varied (SOWING_DATE, VARIETY) "
            "or no irrigation data is available."
        ),
    )

    delta_stress_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc=(
            "Difference in water stress days between the lowest-stress run "
            "and the baseline [days]. "
            "Formula: lowest_stress_days - baseline_stress_days. "
            "Negative = fewer stress days than baseline (better). "
            "Example: -14 means the best irrigation schedule eliminated "
            "14 days of water stress compared to the baseline. "
            "NULL if water_stress_days data is unavailable."
        ),
    )

    # ── Full ranking snapshot (JSON) ──────────────────────────────────────
    ranked_runs: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Full ranked list of all scenario runs, ordered by yield_kg_ha DESC. "
            "Each element is a dict: "
            "  { scenario_run_id, parameter_value, yield_kg_ha, peak_lai, "
            "    harvest_index, water_stress_days, total_irrigation_mm, rank } "
            "Stored as a snapshot so dashboards can display the full table "
            "without re-querying and sorting all ScenarioRun rows. "
            "Regenerated each time the comparison is recomputed. "
            "NULL if comparison has not been run yet."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    scenario: Mapped["ScenarioDefinition"] = relationship(
        "ScenarioDefinition",
        doc="The ScenarioDefinition this comparison summarizes.",
        foreign_keys=[scenario_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ScenarioComparison id={self.id!s:.8} "
            f"scenario={self.scenario_id!s:.8} "
            f"delta_yield={self.delta_yield_percent}% "
            f"delta_stress={self.delta_stress_days}d>"
        )
