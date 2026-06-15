"""
scenario/generators/__init__.py
================================

Deterministic Scenario Generators
===================================

Each generator takes a baseline context and returns a fully-populated
ScenarioDefinition object.  No simulation is executed here — generators
only construct the parameter grid that the execution service will iterate.

Available generators:
  SowingDateGenerator   — calendar grid around a baseline sowing date
  IrrigationGenerator   — fixed schedule tiers (rainfed / 2 / 4 / 6 events)
  VarietyGenerator      — one candidate per available variety for a crop

NOT implemented here:
  - Simulation execution
  - API routes
  - Comparison / ranking
  - Uncertainty or probabilistic sampling
  - Machine learning
"""

from backend.app.scenario.generators.sowing_date_generator import SowingDateGenerator
from backend.app.scenario.generators.irrigation_generator import IrrigationGenerator
from backend.app.scenario.generators.variety_generator import VarietyGenerator

__all__ = [
    "SowingDateGenerator",
    "IrrigationGenerator",
    "VarietyGenerator",
]
