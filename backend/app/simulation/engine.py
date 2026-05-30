"""
engine.py — WOFOST Simulation Engine
======================================

Central orchestrator that assembles all providers and runs the WOFOST 7.2
water-limited simulation. This is the single entry point for running a
crop simulation — all other modules are provider factories.

PCSE Engine API (verified against source code):
  - Wofost72_WLP_FD is an alias for Wofost72_WLP_CWB (models.py line 256)
  - Engine constructor: (parameterprovider, weatherdataprovider, agromanagement)
  - ParameterProvider: ChainMap over cropdata, soildata, sitedata (base/parameter_providers.py)
  - run(days=N): advance N days, stops early if terminated (engine.py line 230)
  - run_till_terminate(): run until TERMINATE signal (engine.py line 238)
  - get_output(): list of daily dicts from OUTPUT_VARS (engine.py line 424)
  - get_summary_output(): summary at crop finish (engine.py line 433)
  - get_variable(key): live state access via kiosk (base/engine.py line 67)
  - set_variable(key, val): state injection for EnKF (engine.py line 389)
"""

import logging
import datetime as dt
from typing import Optional

# Verified imports — every path checked against PCSE source
from pcse.models import Wofost72_WLP_FD          # models.py line 256 (alias for WLP_CWB)
from pcse.base import ParameterProvider           # base/__init__.py line 11

# Local provider modules
from .weather_provider import create_weather_provider
from .crop_provider import create_crop_provider
from .soil_provider import create_soil_params
from .site_provider import create_site_params
from .agromanagement import build_agromanagement
from .output_parser import (
    parse_batch_output,
    parse_summary_output,
    compute_harvest_metrics,
    extract_daily_state,
)

logger = logging.getLogger(__name__)


class SimulationResult:
    """Container for simulation results.

    Attributes:
        daily_output:  Normalized list of daily state dicts (lowercase keys, ISO dates)
        summary:       Season-level summary dict (LAIMAX, DOE, DOA, etc.)
        metrics:       Computed harvest metrics (peak_lai, yield, harvest_index)
        raw_output:    Original PCSE get_output() list (uppercase keys, date objects)
        raw_summary:   Original PCSE get_summary_output() list
    """

    def __init__(self, raw_output: list, raw_summary: list):
        self.raw_output = raw_output
        self.raw_summary = raw_summary
        self.daily_output = parse_batch_output(raw_output)
        self.summary = parse_summary_output(raw_summary)
        self.metrics = compute_harvest_metrics(raw_output)

    @property
    def total_days(self) -> int:
        return len(self.daily_output)

    def __repr__(self) -> str:
        m = self.metrics
        return (
            f"SimulationResult("
            f"days={self.total_days}, "
            f"peak_lai={m.get('peak_lai', 0):.3f}, "
            f"yield={m.get('final_twso_kg_ha', 0):.0f} kg/ha, "
            f"HI={m.get('harvest_index', 0):.3f})"
        )


def run_simulation(
    crop_name: str = "wheat",
    variety_name: str = "Winter_wheat_101",
    sow_date: Optional[dt.date] = None,
    harvest_date: Optional[dt.date] = None,
    latitude: float = 52.0,
    longitude: float = 5.5,
    elevation: float = 10.0,
    wav: float = 10.0,
    soil_params: Optional[dict] = None,
    crop_param_dir: Optional[str] = None,
    use_nasa_weather: bool = False,
    max_duration: int = 300,
    step_by_step: bool = False,
) -> SimulationResult:
    """Run a complete WOFOST 7.2 water-limited simulation.

    This is the main entry point for running a crop simulation. It assembles
    all providers, initializes the WOFOST engine, runs the simulation, and
    returns parsed results.

    Args:
        crop_name:        Lowercase PCSE crop name (e.g. "wheat", "maize")
        variety_name:     PCSE variety key (e.g. "Winter_wheat_101")
        sow_date:         Sowing date (defaults to 2020-10-15 for winter wheat)
        harvest_date:     Harvest date (defaults to 2021-07-30)
        latitude:         Site latitude for weather data
        longitude:        Site longitude for weather data
        elevation:        Site elevation in meters (synthetic weather only)
        wav:              Initial available water in soil [cm]
        soil_params:      Optional dict overriding default soil parameters.
                          Keys: SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB
        crop_param_dir:   Path to WOFOST_crop_parameters (defaults to external_repos/)
        use_nasa_weather: If True, fetch from NASA POWER API (requires internet)
        max_duration:     Maximum crop duration in days
        step_by_step:     If True, run day-by-day (needed for future EnKF).
                          If False, use run_till_terminate() for speed.

    Returns:
        SimulationResult with daily outputs, summary, and harvest metrics

    Raises:
        KeyError: Invalid crop_name or variety_name
        ValueError: Invalid soil parameters or date ordering
        RuntimeError: PCSE engine failure
    """
    # Default dates for winter wheat in Netherlands
    if sow_date is None:
        sow_date = dt.date(2020, 10, 15)
    if harvest_date is None:
        harvest_date = dt.date(2021, 7, 30)

    logger.info(
        "Starting simulation: %s/%s at (%.2f, %.2f), %s to %s",
        crop_name, variety_name, latitude, longitude, sow_date, harvest_date,
    )

    # ── 1. Weather ───────────────────────────────────────────────────────
    wdp = create_weather_provider(
        latitude=latitude,
        longitude=longitude,
        elevation=elevation,
        start_year=sow_date.year,
        end_year=harvest_date.year,
        use_nasa=use_nasa_weather,
    )
    logger.info("Weather: %s to %s", wdp.first_date, wdp.last_date)

    # ── 2. Crop parameters ───────────────────────────────────────────────
    cropd = create_crop_provider(
        crop_name=crop_name,
        variety_name=variety_name,
        crop_param_dir=crop_param_dir,
    )

    # ── 3. Soil parameters ───────────────────────────────────────────────
    if soil_params is not None:
        soildata = create_soil_params(**{k.lower(): v for k, v in soil_params.items()})
    else:
        soildata = create_soil_params()

    # ── 4. Site parameters ───────────────────────────────────────────────
    sitedata = create_site_params(wav=wav)

    # ── 5. Assemble ParameterProvider ────────────────────────────────────
    # Verified: base/parameter_providers.py line 46
    # ChainMap lookup order: override → site → timer → soil → crop → derived
    params = ParameterProvider(
        cropdata=cropd,
        soildata=soildata,
        sitedata=sitedata,
    )
    logger.info("ParameterProvider assembled: %d parameters", len(params))

    # ── 6. AgroManagement ────────────────────────────────────────────────
    agro = build_agromanagement(
        crop_name=crop_name,
        variety_name=variety_name,
        sow_date=sow_date,
        harvest_date=harvest_date,
        max_duration=max_duration,
    )

    # ── 7. Initialize WOFOST engine ──────────────────────────────────────
    # Verified: engine.py line 117 — constructor(params, wdp, agro)
    # Wofost72_WLP_FD uses config="Wofost72_WLP_CWB.conf" with:
    #   SOIL=WaterbalanceFD, CROP=Wofost72, OUTPUT_INTERVAL=daily
    wofost = Wofost72_WLP_FD(params, wdp, agro)

    # ── 8. Run simulation ────────────────────────────────────────────────
    if step_by_step:
        # Day-by-day execution: required for future EnKF assimilation
        # where we need to inject corrected states after each step.
        # Verified: engine.py line 230
        logger.info("Running step-by-step (EnKF-compatible mode)")
        while not wofost.flag_terminate:
            wofost.run(days=1)
    else:
        # Batch execution: faster, for non-assimilated baseline runs
        # Verified: engine.py line 238
        logger.info("Running batch (run_till_terminate)")
        wofost.run_till_terminate()

    # ── 9. Extract results ───────────────────────────────────────────────
    raw_output = wofost.get_output()
    raw_summary = wofost.get_summary_output()

    result = SimulationResult(raw_output, raw_summary)
    logger.info("Simulation complete: %s", result)

    return result
