"""
scenario/services/comparison_engine.py — ComparisonEngine
==========================================================

Evaluates a list of ScenarioRuns and computes a ScenarioComparison.

Responsibilities:
    - Identify the winning run for yield, water use, and stress.
    - Compute cross-run delta metrics (percent yield diff, mm irrigation diff)
      relative to the baseline simulation.
    - Generate a ranked snapshot of all runs.
"""

import uuid
import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.scenario.models.scenario_definition import ScenarioDefinition, GeneratorType
from backend.app.scenario.models.scenario_run import ScenarioRun
from backend.app.scenario.models.scenario_comparison import ScenarioComparison

logger = logging.getLogger(__name__)


class ComparisonEngine:
    """Evaluates scenario runs and generates a ScenarioComparison object."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(
        self,
        scenario_definition: ScenarioDefinition,
        runs: list[ScenarioRun],
    ) -> ScenarioComparison:
        """Compute comparisons and persist the ScenarioComparison.

        Args:
            scenario_definition: The parent scenario.
            runs: List of all ScenarioRun records for this scenario.
                  Must have been flushed to the DB so they have IDs.

        Returns:
            The flushed ScenarioComparison ORM object.
        """
        logger.info(
            "ComparisonEngine: Evaluating %d runs for scenario %s",
            len(runs), scenario_definition.id,
        )

        # 1. Filter out failed runs (where yield_kg_ha is None)
        valid_runs = [r for r in runs if r.yield_kg_ha is not None]
        
        if not valid_runs:
            logger.warning("No valid runs to compare for scenario %s", scenario_definition.id)
            return self._create_empty_comparison(scenario_definition.id)

        # 2. Find baseline metrics (the offset=0 or Rainfed case)
        baseline_run = self._find_baseline_run(scenario_definition, valid_runs)
        
        # 3. Find winners
        best_yield_run = max(valid_runs, key=lambda r: r.yield_kg_ha or 0.0)
        lowest_stress_run = min(
            (r for r in valid_runs if r.water_stress_days is not None),
            key=lambda r: r.water_stress_days,
            default=None,
        )
        lowest_water_run = self._find_lowest_water_acceptable_yield(valid_runs, best_yield_run)

        # 4. Compute Deltas
        delta_yield = None
        if baseline_run and baseline_run.yield_kg_ha and best_yield_run.yield_kg_ha is not None:
            delta_yield = ((best_yield_run.yield_kg_ha - baseline_run.yield_kg_ha) / baseline_run.yield_kg_ha) * 100.0

        delta_irr = None
        if baseline_run and lowest_water_run and baseline_run.total_irrigation_mm is not None and lowest_water_run.total_irrigation_mm is not None:
            if scenario_definition.generator_type == GeneratorType.IRRIGATION:
                delta_irr = lowest_water_run.total_irrigation_mm - baseline_run.total_irrigation_mm

        delta_stress = None
        if baseline_run and lowest_stress_run and baseline_run.water_stress_days is not None and lowest_stress_run.water_stress_days is not None:
            delta_stress = lowest_stress_run.water_stress_days - baseline_run.water_stress_days

        # 5. Build full ranking
        ranked = sorted(valid_runs, key=lambda r: r.yield_kg_ha or 0.0, reverse=True)
        ranked_runs_json = []
        for i, r in enumerate(ranked):
            ranked_runs_json.append({
                "rank": i + 1,
                "scenario_run_id": str(r.id),
                "parameter_value": r.parameter_value,
                "yield_kg_ha": r.yield_kg_ha,
                "peak_lai": r.peak_lai,
                "harvest_index": r.harvest_index,
                "water_stress_days": r.water_stress_days,
                "total_irrigation_mm": r.total_irrigation_mm,
            })

        comparison = ScenarioComparison(
            id=uuid.uuid4(),
            scenario_id=scenario_definition.id,
            best_yield_simulation=best_yield_run.id,
            lowest_water_use_simulation=lowest_water_run.id if lowest_water_run else None,
            lowest_stress_simulation=lowest_stress_run.id if lowest_stress_run else None,
            delta_yield_percent=delta_yield,
            delta_irrigation_mm=delta_irr,
            delta_stress_days=delta_stress,
            ranked_runs=ranked_runs_json,
        )

        # Delete any existing comparison for this scenario (one-to-one constraint)
        self.db.query(ScenarioComparison).filter(ScenarioComparison.scenario_id == scenario_definition.id).delete()
        
        self.db.add(comparison)
        self.db.flush()
        
        return comparison

    def _find_baseline_run(self, definition: ScenarioDefinition, valid_runs: list[ScenarioRun]) -> Optional[ScenarioRun]:
        """Attempt to find the 'control' run among the scenario runs."""
        # For SOWING_DATE, baseline is usually the one that matches the baseline_simulation_id,
        # but since we create new SimulationRuns for all, we check if the parameter_value
        # matches the baseline simulation or just use the first valid run as a fallback.
        # A simple heuristic: Rainfed for IRRIGATION, offset 0 for SOWING_DATE.
        
        if definition.generator_type == GeneratorType.IRRIGATION:
            for r in valid_runs:
                # parameter_value is a dict like {'events': [], 'tier_label': 'Rainfed', 'total_mm': 0}
                if isinstance(r.parameter_value, dict) and r.parameter_value.get("total_mm", -1) == 0:
                    return r
                    
        # For SOWING_DATE and VARIETY, it's harder to automatically identify the baseline run 
        # solely from the candidate values without the base request.
        # Fallback: Just return the first run in the list as the baseline.
        return valid_runs[0] if valid_runs else None

    def _find_lowest_water_acceptable_yield(self, valid_runs: list[ScenarioRun], best_yield_run: ScenarioRun) -> Optional[ScenarioRun]:
        """Find the run with the least irrigation that still achieves >= 90% of best yield."""
        if best_yield_run.yield_kg_ha is None:
            return None
            
        acceptable_runs = [r for r in valid_runs if r.yield_kg_ha and r.yield_kg_ha >= 0.9 * best_yield_run.yield_kg_ha]
        
        # Only consider runs that have irrigation data
        acceptable_runs_with_water = [r for r in acceptable_runs if r.total_irrigation_mm is not None]
        
        if not acceptable_runs_with_water:
            return None
            
        return min(acceptable_runs_with_water, key=lambda r: r.total_irrigation_mm)

    def _create_empty_comparison(self, scenario_id: uuid.UUID) -> ScenarioComparison:
        comp = ScenarioComparison(
            id=uuid.uuid4(),
            scenario_id=scenario_id,
            best_yield_simulation=None,
            lowest_water_use_simulation=None,
            lowest_stress_simulation=None,
            delta_yield_percent=None,
            delta_irrigation_mm=None,
            delta_stress_days=None,
            ranked_runs=[],
        )
        self.db.query(ScenarioComparison).filter(ScenarioComparison.scenario_id == scenario_id).delete()
        self.db.add(comp)
        self.db.flush()
        return comp
