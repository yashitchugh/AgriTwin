"""
scenario/services/scenario_service.py — ScenarioService
=========================================================

High-level orchestrator for deterministic scenario analysis.

Glues together:
  1. Scenario Generators (SowingDate, Irrigation, Variety)
  2. ScenarioRunner (simulates the grid)
  3. ComparisonEngine (ranks and summarizes the results)
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.api.schemas.simulate import SimulateRequest
from backend.app.scenario.generators import (
    SowingDateGenerator,
    IrrigationGenerator,
    VarietyGenerator,
)
from backend.app.scenario.runners.scenario_runner import ScenarioRunner
from backend.app.scenario.services.comparison_engine import ComparisonEngine
from backend.app.scenario.models.scenario_definition import ScenarioDefinition
from backend.app.scenario.models.scenario_comparison import ScenarioComparison

logger = logging.getLogger(__name__)


class ScenarioService:
    """Orchestrates deterministic scenario generation, execution, and comparison."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.runner = ScenarioRunner(db)
        self.comparison_engine = ComparisonEngine(db)

    def run_sowing_date_scenario(
        self,
        base_request: SimulateRequest,
        offsets: Optional[list[int]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ScenarioDefinition:
        """Run a sowing date sweep scenario.
        
        Args:
            base_request: The control simulation parameters.
            offsets: Optional list of days to offset the sowing date.
            name: Optional scenario name.
            description: Optional scenario description.
            
        Returns:
            The fully populated ScenarioDefinition with runs and comparison attached.
        """
        logger.info("Starting Sowing Date scenario for %s", base_request.crop)
        
        # 1. Generate the parameter grid
        generator = SowingDateGenerator(offsets=offsets)
        definition = generator.generate(
            baseline_sowing_date=base_request.sowing_date,
            name=name,
            description=description,
            baseline_simulation_id=None, # We don't have a baseline run yet
        )
        
        self.db.add(definition)
        self.db.flush()
        
        # 2. Run all candidates
        runs = self.runner.run(base_request, definition)
        
        # 3. Compute comparison
        comparison = self.comparison_engine.evaluate(definition, runs)
        
        self.db.commit()
        return definition

    def run_irrigation_scenario(
        self,
        base_request: SimulateRequest,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ScenarioDefinition:
        """Run an irrigation tier sweep scenario."""
        logger.info("Starting Irrigation scenario for %s", base_request.crop)
        
        generator = IrrigationGenerator()
        definition = generator.generate(
            sowing_date=base_request.sowing_date,
            name=name,
            description=description,
        )
        
        self.db.add(definition)
        self.db.flush()
        
        runs = self.runner.run(base_request, definition)
        comparison = self.comparison_engine.evaluate(definition, runs)
        
        self.db.commit()
        return definition

    def run_variety_scenario(
        self,
        base_request: SimulateRequest,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ScenarioDefinition:
        """Run a variety sweep scenario for all available varieties."""
        logger.info("Starting Variety scenario for %s", base_request.crop)
        
        generator = VarietyGenerator(exclude_baseline=False)
        definition = generator.generate(
            crop=base_request.crop,
            baseline_variety=base_request.variety,
            name=name,
            description=description,
        )
        
        self.db.add(definition)
        self.db.flush()
        
        runs = self.runner.run(base_request, definition)
        comparison = self.comparison_engine.evaluate(definition, runs)
        
        self.db.commit()
        return definition

