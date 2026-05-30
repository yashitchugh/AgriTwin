"""
AgriTwin Simulation Module
===========================

Modular PCSE/WOFOST simulation architecture for the AgriTwin digital twin.

Public API:
    run_simulation()       — Run a complete WOFOST simulation
    SimulationResult       — Container for simulation results

Provider factories (for advanced use):
    create_weather_provider()  — Weather data (synthetic or NASA POWER)
    create_crop_provider()     — Crop parameters from YAML
    create_soil_params()       — Validated soil parameter dict
    create_site_params()       — Validated site parameters
    build_agromanagement()     — AgroManagement list builder

Output utilities:
    extract_daily_state()      — Live state extraction (for EnKF)
    parse_batch_output()       — Normalize get_output() results
    parse_summary_output()     — Normalize get_summary_output()
    compute_harvest_metrics()  — Compute yield, HI, peak LAI
"""

from .engine import run_simulation, SimulationResult
from .weather_provider import create_weather_provider
from .crop_provider import create_crop_provider
from .soil_provider import create_soil_params
from .site_provider import create_site_params
from .agromanagement import build_agromanagement
from .output_parser import (
    extract_daily_state,
    parse_batch_output,
    parse_summary_output,
    compute_harvest_metrics,
)

__all__ = [
    "run_simulation",
    "SimulationResult",
    "create_weather_provider",
    "create_crop_provider",
    "create_soil_params",
    "create_site_params",
    "build_agromanagement",
    "extract_daily_state",
    "parse_batch_output",
    "parse_summary_output",
    "compute_harvest_metrics",
]
