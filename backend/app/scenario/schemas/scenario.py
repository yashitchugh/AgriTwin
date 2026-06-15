"""
scenario/schemas/scenario.py — Pydantic v2 Schemas for the Scenario Engine
============================================================================

Defines request and response schemas for:
  - ScenarioDefinitionCreate  — POST /scenarios (create a new definition)
  - ScenarioDefinitionResponse — GET /scenarios / GET /scenarios/{id}
  - ScenarioRunResponse        — element of ScenarioDefinitionResponse.runs
  - RankedRunEntry             — element of ScenarioComparisonResponse.ranked_runs
  - ScenarioComparisonResponse — GET /scenarios/{id}/comparison

All schemas use Pydantic v2 idioms consistent with the rest of AgriTwin:
  - model_config = ConfigDict(from_attributes=True)   for ORM ↔ schema
  - Field(..., description=..., examples=[...])        for Swagger docs
  - Optional[T] with default=None                      for nullable columns
  - field_validator / model_validator                  for semantic validation

Validators included here:
  - parameter_values must have at least 2 elements (a single value is trivial)
  - parameter_values must not exceed 50 elements (runtime safety guard)
  - For SOWING_DATE, each value must be a valid ISO date string
  - For VARIETY, each value must be a non-empty string
  - name must not be blank (strip whitespace check)

NOT implemented here:
  - Service logic, generators, execution pipeline
  - API routes (FastAPI router)
  - EnKF, ML, satellite, IoT
"""

import datetime
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.scenario.models.scenario_definition import GeneratorType


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

class ScenarioDefinitionCreate(BaseModel):
    """Request body for POST /scenarios.

    Describes the what-if analysis to run: which parameter to vary,
    across which candidate values, relative to which baseline simulation.

    Minimal valid request (SOWING_DATE sweep):
        {
          "name": "Sowing date sweep — Delhi wheat 2020",
          "generator_type": "SOWING_DATE",
          "parameter_name": "sowing_date",
          "parameter_values": ["2020-10-01", "2020-10-15", "2020-11-01"],
          "base_simulation_id": "550e8400-e29b-41d4-a716-446655440000"
        }

    Minimal valid request (VARIETY sweep):
        {
          "name": "Variety comparison — Lucknow rice 2020",
          "generator_type": "VARIETY",
          "parameter_name": "variety",
          "parameter_values": ["Rice_IR64", "Rice_Basmati"],
          "base_simulation_id": "..."
        }
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description=(
            "Short, descriptive name for this scenario. "
            "Shown in list views and comparison tables. "
            "Must be non-empty after whitespace stripping."
        ),
        examples=["Sowing date sweep — Delhi wheat 2020"],
    )

    description: Optional[str] = Field(
        default=None,
        max_length=4000,
        description=(
            "Optional longer description of the scenario hypothesis, "
            "expected outcomes, or agronomic rationale. Free text."
        ),
        examples=["Testing whether earlier sowing avoids heat stress at grain fill."],
    )

    generator_type: GeneratorType = Field(
        ...,
        description=(
            "Which parameter dimension to vary. "
            "SOWING_DATE: vary the planting date across a calendar grid. "
            "IRRIGATION: vary the irrigation schedule applied. "
            "VARIETY: vary the crop variety, holding location/date constant."
        ),
        examples=["SOWING_DATE"],
    )

    parameter_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "The SimulateRequest field that is overridden per run. "
            "Canonical values: "
            "'sowing_date' for SOWING_DATE, "
            "'irrigation_events' for IRRIGATION, "
            "'variety' for VARIETY."
        ),
        examples=["sowing_date"],
    )

    parameter_values: list[Any] = Field(
        ...,
        min_length=2,
        description=(
            "List of candidate values to test, one per scenario run. "
            "Must contain 2–50 elements. "
            "Format per generator_type: "
            "  SOWING_DATE → ['2020-10-01', '2020-10-15', ...] "
            "  IRRIGATION  → [{'events': [{date, amount_mm}, ...]}, ...] "
            "  VARIETY     → ['apache', 'Winter_wheat_101', ...]"
        ),
        examples=[["2020-10-01", "2020-10-15", "2020-11-01"]],
    )

    base_simulation_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the baseline SimulationRun (simulation_runs.id) against "
            "which all candidate runs are compared. "
            "The baseline provides: crop, location, harvest_date, weather/soil flags, "
            "and all parameters NOT being varied. "
            "May be None for draft scenarios created before the baseline exists."
        ),
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        """Reject names that are only whitespace."""
        if not v.strip():
            raise ValueError("name must not be blank or whitespace-only")
        return v.strip()

    @field_validator("parameter_values")
    @classmethod
    def validate_parameter_values_length(cls, v: list[Any]) -> list[Any]:
        """Enforce 2 ≤ len ≤ 50 candidate values."""
        if len(v) < 2:
            raise ValueError(
                "parameter_values must contain at least 2 elements — "
                "a single value is just a baseline re-run."
            )
        if len(v) > 50:
            raise ValueError(
                f"parameter_values has {len(v)} elements; maximum is 50. "
                "Large sweeps should be batched into multiple scenarios."
            )
        return v

    @model_validator(mode="after")
    def validate_values_match_generator(self) -> "ScenarioDefinitionCreate":
        """Cross-field validation: parameter_values format must match generator_type."""
        gen = self.generator_type
        values = self.parameter_values

        if gen == GeneratorType.SOWING_DATE:
            for i, v in enumerate(values):
                if not isinstance(v, str):
                    raise ValueError(
                        f"SOWING_DATE parameter_values[{i}] must be a string "
                        f"(ISO date YYYY-MM-DD), got {type(v).__name__}."
                    )
                try:
                    datetime.date.fromisoformat(v)
                except ValueError:
                    raise ValueError(
                        f"SOWING_DATE parameter_values[{i}]={v!r} is not a valid "
                        "ISO date (expected YYYY-MM-DD)."
                    )

        elif gen == GeneratorType.VARIETY:
            for i, v in enumerate(values):
                if not isinstance(v, str) or not v.strip():
                    raise ValueError(
                        f"VARIETY parameter_values[{i}] must be a non-empty string "
                        f"(WOFOST variety name), got {v!r}."
                    )

        elif gen == GeneratorType.IRRIGATION:
            for i, v in enumerate(values):
                if not isinstance(v, dict):
                    raise ValueError(
                        f"IRRIGATION parameter_values[{i}] must be a dict "
                        f"with an 'events' key, got {type(v).__name__}."
                    )
                if "events" not in v:
                    raise ValueError(
                        f"IRRIGATION parameter_values[{i}] is missing required "
                        "key 'events'."
                    )
                if not isinstance(v["events"], list):
                    raise ValueError(
                        f"IRRIGATION parameter_values[{i}]['events'] must be a list."
                    )

        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Sowing date sweep — Delhi wheat 2020",
                "description": "Test 4 sowing windows to find optimal planting date.",
                "generator_type": "SOWING_DATE",
                "parameter_name": "sowing_date",
                "parameter_values": [
                    "2020-10-01", "2020-10-15", "2020-11-01", "2020-11-15"
                ],
                "base_simulation_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ScenarioRunResponse(BaseModel):
    """Summary of one executed run within a scenario.

    Returned as elements of ScenarioDefinitionResponse.runs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(
        description="UUID of this ScenarioRun record (scenario_runs.id).",
    )
    scenario_id: uuid.UUID = Field(
        description="UUID of the parent ScenarioDefinition.",
    )
    parameter_value: Any = Field(
        description=(
            "The candidate value tested in this run. "
            "Type matches generator_type: date string, irrigation dict, or variety string."
        ),
    )
    simulation_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the linked SimulationRun (simulation_runs.id). "
            "None if the simulation has not been executed yet."
        ),
    )

    # Agronomic results
    yield_kg_ha: Optional[float] = Field(
        default=None,
        description="Simulated yield [kg dry matter / ha]. None if not yet completed.",
    )
    peak_lai: Optional[float] = Field(
        default=None,
        description="Maximum Leaf Area Index [m²/m²]. None if not yet completed.",
    )
    harvest_index: Optional[float] = Field(
        default=None,
        description="Harvest Index = TWSO / TAGP [-]. None if not yet completed.",
    )
    final_tagp: Optional[float] = Field(
        default=None,
        description="Total above-ground biomass at season end [kg/ha].",
    )
    final_twso: Optional[float] = Field(
        default=None,
        description="Total weight of storage organs at season end [kg/ha].",
    )

    # Water-use diagnostics
    water_stress_days: Optional[int] = Field(
        default=None,
        description=(
            "Days with RFTRA < 1.0 (crop was water-stressed). "
            "None if run has not completed or DailyOutputs unavailable."
        ),
    )
    total_irrigation_mm: Optional[float] = Field(
        default=None,
        description=(
            "Total irrigation applied this season [mm]. "
            "0 for SOWING_DATE and VARIETY scenarios."
        ),
    )

    # Timestamps
    created_at: Optional[datetime.datetime] = Field(
        default=None,
        description="UTC datetime when this ScenarioRun record was created.",
    )

    @classmethod
    def from_orm_row(cls, row: object) -> "ScenarioRunResponse":
        """Construct from a ScenarioRun ORM instance."""
        return cls(
            id=row.id,
            scenario_id=row.scenario_id,
            parameter_value=row.parameter_value,
            simulation_id=row.simulation_id,
            yield_kg_ha=row.yield_kg_ha,
            peak_lai=row.peak_lai,
            harvest_index=row.harvest_index,
            final_tagp=row.final_tagp,
            final_twso=row.final_twso,
            water_stress_days=row.water_stress_days,
            total_irrigation_mm=row.total_irrigation_mm,
            created_at=row.created_at,
        )


class ScenarioDefinitionResponse(BaseModel):
    """Full response for GET /scenarios/{id} and elements of GET /scenarios.

    Includes the full list of ScenarioRun results when available.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="UUID of this ScenarioDefinition.")
    name: str = Field(description="Human-readable scenario name.")
    description: Optional[str] = Field(
        default=None,
        description="Optional longer description.",
    )
    generator_type: GeneratorType = Field(
        description="Parameter dimension being explored (SOWING_DATE, IRRIGATION, VARIETY).",
    )
    parameter_name: str = Field(
        description="The SimulateRequest field that is varied across runs.",
    )
    parameter_values: list[Any] = Field(
        description="All candidate values submitted when the scenario was created.",
    )
    base_simulation_id: Optional[uuid.UUID] = Field(
        default=None,
        description="UUID of the baseline SimulationRun used as the control.",
    )
    runs: list[ScenarioRunResponse] = Field(
        default_factory=list,
        description=(
            "Completed ScenarioRun records, one per candidate value. "
            "Empty until the execution service processes this scenario."
        ),
    )
    run_count: int = Field(
        default=0,
        description="Number of ScenarioRun records created so far.",
    )
    created_at: Optional[datetime.datetime] = Field(
        default=None,
        description="UTC datetime when this ScenarioDefinition was created.",
    )
    updated_at: Optional[datetime.datetime] = Field(
        default=None,
        description="UTC datetime of the most recent update to this record.",
    )

    @classmethod
    def from_orm_row(cls, row: object) -> "ScenarioDefinitionResponse":
        """Construct from a ScenarioDefinition ORM instance."""
        runs = [ScenarioRunResponse.from_orm_row(r) for r in (row.runs or [])]
        return cls(
            id=row.id,
            name=row.name,
            description=row.description,
            generator_type=row.generator_type,
            parameter_name=row.parameter_name,
            parameter_values=row.parameter_values,
            base_simulation_id=row.base_simulation_id,
            runs=runs,
            run_count=len(runs),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class RankedRunEntry(BaseModel):
    """One entry in the ranked_runs list inside ScenarioComparisonResponse.

    Ranked by yield_kg_ha DESC (rank=1 is highest yield).
    """

    model_config = ConfigDict(from_attributes=True)

    rank: int = Field(
        description=(
            "Position in the yield-descending ranking (1 = highest yield). "
            "Ties are broken by scenario_run_id lexicographic order."
        ),
    )
    scenario_run_id: uuid.UUID = Field(
        description="UUID of the ScenarioRun for this ranked entry.",
    )
    parameter_value: Any = Field(
        description="The candidate value that produced this result.",
    )
    yield_kg_ha: Optional[float] = Field(
        default=None,
        description="Simulated yield [kg/ha] for this run.",
    )
    peak_lai: Optional[float] = Field(
        default=None,
        description="Peak LAI [m²/m²] for this run.",
    )
    harvest_index: Optional[float] = Field(
        default=None,
        description="Harvest Index [-] for this run.",
    )
    water_stress_days: Optional[int] = Field(
        default=None,
        description="Days with RFTRA < 1.0 for this run.",
    )
    total_irrigation_mm: Optional[float] = Field(
        default=None,
        description="Total irrigation applied [mm] for this run.",
    )


class ScenarioComparisonResponse(BaseModel):
    """Full comparison results for GET /scenarios/{id}/comparison.

    Contains winner run identifiers for each dimension, cross-run delta
    statistics, and a full ranked list of all runs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="UUID of this ScenarioComparison record.")
    scenario_id: uuid.UUID = Field(
        description="UUID of the parent ScenarioDefinition.",
    )

    # Winner run IDs
    best_yield_simulation: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the ScenarioRun that achieved the highest yield_kg_ha. "
            "Use this to fetch the winning run's full simulation detail."
        ),
    )
    lowest_water_use_simulation: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the ScenarioRun with the lowest total_irrigation_mm "
            "while still achieving ≥90% of best yield. "
            "None for SOWING_DATE / VARIETY scenarios (no irrigation variation)."
        ),
    )
    lowest_stress_simulation: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the ScenarioRun with the fewest water_stress_days "
            "(days with RFTRA < 1.0)."
        ),
    )

    # Delta metrics
    delta_yield_percent: Optional[float] = Field(
        default=None,
        description=(
            "Yield improvement of the best run vs. the baseline [%]. "
            "= (best_yield - baseline_yield) / baseline_yield × 100. "
            "Positive = better than baseline. Negative = worse."
        ),
    )
    delta_irrigation_mm: Optional[float] = Field(
        default=None,
        description=(
            "Irrigation difference: lowest_water_use_mm - baseline_mm [mm]. "
            "Negative = saved water vs. baseline. "
            "None when irrigation is not the variable being tested."
        ),
    )
    delta_stress_days: Optional[int] = Field(
        default=None,
        description=(
            "Stress day difference: lowest_stress_days - baseline_stress_days [days]. "
            "Negative = fewer stress days than baseline (better water management)."
        ),
    )

    # Full ranking
    ranked_runs: Optional[list[RankedRunEntry]] = Field(
        default=None,
        description=(
            "All scenario runs ranked by yield_kg_ha DESC (rank=1 = highest yield). "
            "None if the comparison has not been computed yet."
        ),
    )

    # Timestamps
    created_at: Optional[datetime.datetime] = Field(
        default=None,
        description="UTC datetime when this comparison was first computed.",
    )
    updated_at: Optional[datetime.datetime] = Field(
        default=None,
        description="UTC datetime when this comparison was last recomputed.",
    )

    @classmethod
    def from_orm_row(cls, row: object) -> "ScenarioComparisonResponse":
        """Construct from a ScenarioComparison ORM instance."""
        ranked: Optional[list[RankedRunEntry]] = None
        if row.ranked_runs is not None:
            ranked = [RankedRunEntry(**entry) for entry in row.ranked_runs]
        return cls(
            id=row.id,
            scenario_id=row.scenario_id,
            best_yield_simulation=row.best_yield_simulation,
            lowest_water_use_simulation=row.lowest_water_use_simulation,
            lowest_stress_simulation=row.lowest_stress_simulation,
            delta_yield_percent=row.delta_yield_percent,
            delta_irrigation_mm=row.delta_irrigation_mm,
            delta_stress_days=row.delta_stress_days,
            ranked_runs=ranked,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
