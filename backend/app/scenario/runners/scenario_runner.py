"""
scenario/runners/scenario_runner.py — ScenarioRunner
=====================================================

Executes a ScenarioDefinition by generating modified SimulateRequests
and passing them to the existing SimulationService.

Design rationale:
    - Reuse over recreation: The existing SimulationService already handles
      weather fetching, soil fetching, PCSE engine execution, and database
      persistence (SimulationRun + DailyOutputs).  We must never duplicate
      that logic here.
    - Isolation: Each scenario candidate is a complete, independent WOFOST
      simulation.  This runner simply acts as an orchestrator that loops
      over the candidate grid.
    - Translation: It clones the `base_request` (the control simulation),
      overrides the specific parameter dictated by the ScenarioDefinition,
      runs the simulation, and wraps the result in a ScenarioRun object.

Water diagnostic calculations:
    The runner computes `water_stress_days` directly from the
    DailyOutput rows returned by the SimulationService.
    It computes `total_irrigation_mm` from the candidate parameter value
    (for IRRIGATION scenarios).

NOT implemented here:
    - Parallel execution (Celery/multiprocessing).  Currently runs synchronously.
    - Ranking or cross-run comparisons (ScenarioComparison generation).
    - API routes or background task management.
"""

import datetime
import logging
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.app.api.schemas.simulate import SimulateRequest
from backend.app.scenario.models.scenario_definition import ScenarioDefinition, GeneratorType
from backend.app.scenario.models.scenario_run import ScenarioRun
from backend.app.services.simulation_service import run_simulation_from_request

logger = logging.getLogger(__name__)


class ScenarioRunner:
    """Executes a ScenarioDefinition and generates ScenarioRun records.

    Loops through all candidate values in the ScenarioDefinition, runs WOFOST
    via SimulationService, computes scenario-specific diagnostics, and
    persists ScenarioRun rows to the database.

    Usage:
        runner = ScenarioRunner(db)
        scenario_runs = runner.run(base_request, scenario_definition)
    """

    def __init__(self, db: Session) -> None:
        """Initialize the runner.

        Args:
            db: Active SQLAlchemy session. Required because ScenarioRuns
                must be persisted and linked to the ScenarioDefinition.
        """
        self.db = db

    def run(
        self,
        base_request: SimulateRequest,
        scenario_definition: ScenarioDefinition,
    ) -> list[ScenarioRun]:
        """Execute all candidate values in the scenario definition.

        1. Validates the base request.
        2. Loops over scenario_definition.parameter_values.
        3. Clones the base request and applies the candidate value.
        4. Calls SimulationService to run and persist the SimulationRun.
        5. Computes water_stress_days and total_irrigation_mm.
        6. Creates and persists a ScenarioRun record.

        Args:
            base_request:        The control parameters. This provides the location,
                                 crop, dates, and flags that are NOT being varied.
            scenario_definition: The definition containing the generator type,
                                 parameter name, and grid of candidate values.

        Returns:
            List of populated ScenarioRun ORM objects, after they have been
            flushed to the database.
        """
        logger.info(
            "ScenarioRunner starting: scenario_id=%s, base_crop=%s, candidates=%d",
            scenario_definition.id, base_request.crop,
            len(scenario_definition.parameter_values),
        )

        scenario_runs: list[ScenarioRun] = []

        for candidate_value in scenario_definition.parameter_values:
            logger.debug(
                "Running candidate: %s = %r",
                scenario_definition.parameter_name, candidate_value,
            )

            # 1. Clone base request and apply the candidate parameter
            candidate_request = self._build_candidate_request(
                base_request,
                scenario_definition.generator_type,
                scenario_definition.parameter_name,
                candidate_value,
            )

            # 2. Execute full simulation and persist SimulationRun + DailyOutputs
            # We pass db so that SimulationService writes the results to SQLite/PG.
            try:
                sim_response = run_simulation_from_request(
                    request=candidate_request,
                    db=self.db,
                )
            except Exception as e:
                logger.error(
                    "Candidate failed: %s=%r — %s: %s",
                    scenario_definition.parameter_name, candidate_value,
                    type(e).__name__, str(e),
                )
                # If a simulation fails (e.g. crop dies immediately from cold),
                # we still create a ScenarioRun to record the failure.
                run = self._create_failed_scenario_run(
                    scenario_definition.id,
                    candidate_value,
                )
                self.db.add(run)
                self.db.flush()
                scenario_runs.append(run)
                continue

            # 3. Create the ScenarioRun record mapping the candidate to the results
            run = self._create_scenario_run(
                scenario_id=scenario_definition.id,
                parameter_value=candidate_value,
                sim_response=sim_response,
                generator_type=scenario_definition.generator_type,
            )

            self.db.add(run)
            self.db.flush()
            scenario_runs.append(run)

        # Commit all ScenarioRuns (and their underlying SimulationRuns) together
        self.db.commit()

        logger.info(
            "ScenarioRunner finished: scenario_id=%s. %d runs completed.",
            scenario_definition.id, len(scenario_runs),
        )
        return scenario_runs

    def _build_candidate_request(
        self,
        base_request: SimulateRequest,
        generator_type: GeneratorType,
        parameter_name: str,
        candidate_value: Any,
    ) -> SimulateRequest:
        """Clone the base request and apply the candidate parameter override.

        Uses Pydantic's model_copy(update={...}) for a clean, validated clone.

        Args:
            base_request:    The original control request.
            generator_type:  Which parameter dimension is being varied.
            parameter_name:  The specific request field (e.g. "sowing_date").
            candidate_value: The value to apply.

        Returns:
            A new SimulateRequest instance with the override applied.
        """
        update_dict = {}

        if generator_type == GeneratorType.SOWING_DATE:
            # candidate_value is an ISO date string
            update_dict[parameter_name] = datetime.date.fromisoformat(candidate_value)

        elif generator_type == GeneratorType.VARIETY:
            # candidate_value is a string variety name
            update_dict[parameter_name] = candidate_value

        elif generator_type == GeneratorType.IRRIGATION:
            # candidate_value is a dict: {"events": [{"date": ..., "amount_mm": ...}]}
            # SimulateRequest.irrigation_events expects a list of IrrigationEvent models.
            from backend.app.api.schemas.simulate import IrrigationEvent
            events = [
                IrrigationEvent(date=e["date"], amount_mm=e["amount_mm"])
                for e in candidate_value["events"]
            ]
            update_dict[parameter_name] = events

        else:
            # Fallback for future generators
            update_dict[parameter_name] = candidate_value

        return base_request.model_copy(update=update_dict)

    def _create_scenario_run(
        self,
        scenario_id: uuid.UUID,
        parameter_value: Any,
        sim_response: Any,
        generator_type: GeneratorType,
    ) -> ScenarioRun:
        """Construct a successful ScenarioRun ORM object from a simulation response.

        Args:
            scenario_id:     Parent scenario UUID.
            parameter_value: The candidate value that was tested.
            sim_response:    SimulateResponse returned by SimulationService.
            generator_type:  Scenario type (to compute total_irrigation_mm).

        Returns:
            Unflushed ScenarioRun instance.
        """
        # 1. Extract agronomic scalars
        metrics = sim_response.metrics
        yield_kg = metrics.final_twso_kg_ha if metrics else None
        peak_lai = metrics.peak_lai if metrics else None
        hi = metrics.harvest_index if metrics else None
        tagp = metrics.final_tagp_kg_ha if metrics else None
        twso = metrics.final_twso_kg_ha if metrics else None

        # 2. Compute water stress days from the daily time series
        # We count days where RFTRA < 1.0.  (Floating point < 0.999 to be safe).
        stress_days = 0
        if sim_response.daily_states:
            stress_days = sum(
                1 for day in sim_response.daily_states
                if day.rftra is not None and day.rftra < 0.999
            )

        # 3. Compute total irrigation mm
        total_irr_mm = 0.0
        if generator_type == GeneratorType.IRRIGATION:
            # Sum the amounts from the events list in the candidate value
            events = parameter_value.get("events", [])
            total_irr_mm = sum(float(e.get("amount_mm", 0.0)) for e in events)
        else:
            # For SOWING_DATE / VARIETY, irrigation is fixed.
            # We don't pull it from base_request here; we assume 0 unless
            # they passed irrigation in the baseline.
            # TODO: Future enhancement: compute from base_request if needed.
            total_irr_mm = 0.0

        return ScenarioRun(
            id=uuid.uuid4(),
            scenario_id=scenario_id,
            parameter_value=parameter_value,
            simulation_id=sim_response.simulation_id,
            yield_kg_ha=yield_kg,
            peak_lai=peak_lai,
            harvest_index=hi,
            final_tagp=tagp,
            final_twso=twso,
            water_stress_days=stress_days,
            total_irrigation_mm=total_irr_mm,
        )

    def _create_failed_scenario_run(
        self,
        scenario_id: uuid.UUID,
        parameter_value: Any,
    ) -> ScenarioRun:
        """Construct a ScenarioRun for a candidate that crashed WOFOST.

        Returns an empty run (all scalars NULL) so the scenario comparison
        knows this value was tested but failed.
        """
        return ScenarioRun(
            id=uuid.uuid4(),
            scenario_id=scenario_id,
            parameter_value=parameter_value,
            simulation_id=None,
            yield_kg_ha=None,
            peak_lai=None,
            harvest_index=None,
            final_tagp=None,
            final_twso=None,
            water_stress_days=None,
            total_irrigation_mm=None,
        )
