"""
models/__init__.py — ORM Model Registry
========================================

Importing this package ensures all ORM models are registered in
Base.metadata before any call to Base.metadata.create_all() or
Alembic's autogenerate.

Usage:
    # In session.py / Alembic env.py — import models before create_all:
    from backend.app.models import farm, field, simulation_run, daily_output

    # Or import individual models:
    from backend.app.models.farm import Farm
    from backend.app.models.field import Field
    from backend.app.models.simulation_run import SimulationRun
    from backend.app.models.daily_output import DailyOutput
"""

from backend.app.models.farm import Farm  # noqa: F401
from backend.app.models.field import Field  # noqa: F401
from backend.app.models.simulation_run import SimulationRun  # noqa: F401
from backend.app.models.daily_output import DailyOutput  # noqa: F401

from backend.app.scenario.models.scenario_definition import ScenarioDefinition  # noqa: F401
from backend.app.scenario.models.scenario_run import ScenarioRun  # noqa: F401
from backend.app.scenario.models.scenario_comparison import ScenarioComparison  # noqa: F401

# ── Assimilation / Observation Framework ──────────────────────────────────────
# Import last so foreign keys to fields/simulation_runs resolve correctly.
from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus  # noqa: F401
from backend.app.assimilation.models.observation_batch import ObservationBatch, BatchProcessingStatus  # noqa: F401
from backend.app.assimilation.models.assimilation_state import AssimilationState  # noqa: F401

__all__ = [
    # Core
    "Farm", "Field", "SimulationRun", "DailyOutput",
    # Scenario Engine
    "ScenarioDefinition", "ScenarioRun", "ScenarioComparison",
    # Observation Framework
    "Observation", "ObservationSource", "ObservationStatus",
    "ObservationBatch", "BatchProcessingStatus", "AssimilationState",
]
