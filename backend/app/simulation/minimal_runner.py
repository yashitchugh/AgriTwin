"""
minimal_runner.py — Minimal WOFOST 7.2 Water-Limited Simulation Runner
=======================================================================

This script demonstrates the MINIMUM viable workflow for running a PCSE/WOFOST
crop simulation from scratch. It uses:

  - Wofost72_WLP_FD   (water-limited, freely-draining soil)
  - YAMLCropDataProvider (loads crop parameters from local YAML files)
  - Synthetic weather data (so it runs offline without NASA POWER API)
  - Inline AgroManagement YAML (no external file dependencies)

All imports, class names, and method calls have been VERIFIED against the
actual PCSE source code in external_repos/pcse/.

Key findings embedded in this script:
  - Wofost72_WLP_FD is an alias for Wofost72_WLP_CWB (models.py line 256)
  - pcse.fileinput is DEPRECATED — always use pcse.input
  - NASAPowerWeatherDataProvider is at pcse.input, NOT pcse.db
  - WOFOST72SiteDataProvider only requires WAV; RDMSOL goes in soildata
  - YAMLCropDataProvider.get_crops_varieties() is the correct method
    (get_cropnames() and get_varieties() do NOT exist)

Usage:
    cd /home/vini/Arena/AgriTwin
    source venv/bin/activate
    python backend/app/simulation/minimal_runner.py
"""

import os
import sys
import math
import datetime as dt

import yaml

# =============================================================================
# STEP 1: VERIFIED IMPORTS
# =============================================================================
# Every import below has been verified against the PCSE source code:
#   - pcse.models    → models.py line 256: Wofost72_WLP_FD = Wofost72_WLP_CWB
#   - pcse.base      → base/__init__.py line 11: ParameterProvider
#   - pcse.base      → base/__init__.py line 14: WeatherDataProvider, WeatherDataContainer
#   - pcse.input     → input/__init__.py lines 30-34: YAMLCropDataProvider, WOFOST72SiteDataProvider
#   - pcse.util      → util.py line 36: reference_ET
#
# DO NOT use pcse.fileinput (deprecated) or pcse.db (wrong module path).

from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider, WeatherDataProvider, WeatherDataContainer
from pcse.input import YAMLCropDataProvider, WOFOST72SiteDataProvider
from pcse.util import reference_ET


# =============================================================================
# STEP 2: SYNTHETIC WEATHER DATA PROVIDER
# =============================================================================
# NASA POWER API may be unavailable or rate-limited. To ensure this script
# always works offline, we generate physically plausible synthetic weather
# for a temperate European location (lat=52, lon=5.5, elev=10m).
#
# WeatherDataContainer requires these fields (verified from base/weather.py):
#   Compulsory: LAT, LON, ELEV, DAY, IRRAD, TMIN, TMAX, VAP, RAIN, WIND,
#               E0, ES0, ET0
#   Optional:   TEMP, SNOWDEPTH
#
# Units (from base/weather.py line 80-83):
#   IRRAD: J/m²/day,  TMIN/TMAX: °C,  VAP: hPa,  RAIN: cm/day,
#   E0/ES0/ET0: cm/day,  WIND: m/s

class SyntheticWeatherProvider(WeatherDataProvider):
    """Generates physically plausible daily weather for testing.

    Uses sinusoidal seasonal variation with a small daily random-like
    perturbation (deterministic, based on day-of-year).

    Parameters are tuned for a temperate European climate (Netherlands region).
    """

    def __init__(self, latitude=52.0, longitude=5.5, elevation=10.0,
                 start_year=2020, end_year=2021):
        # Call the base class __init__ — this initializes self.store = {}
        # Verified: base/weather.py line 228
        WeatherDataProvider.__init__(self)

        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.description = ["Synthetic weather data for testing"]
        self.angstA = 0.29
        self.angstB = 0.49

        # Generate daily weather records for the full date range
        start_date = dt.date(start_year, 1, 1)
        end_date = dt.date(end_year, 12, 31)

        current_day = start_date
        while current_day <= end_date:
            wdc = self._generate_day(current_day)
            # _store_WeatherDataContainer is the correct method
            # Verified: base/weather.py line 341
            self._store_WeatherDataContainer(wdc, current_day)
            current_day += dt.timedelta(days=1)

    def _generate_day(self, day):
        """Generate a single day of physically plausible weather data."""
        doy = day.timetuple().tm_yday  # day-of-year (1-366)

        # -- Temperature: sinusoidal annual cycle --
        # Netherlands-like: winter ~2°C, summer ~20°C
        t_mean = 11.0 + 9.0 * math.sin(2 * math.pi * (doy - 100) / 365)
        # Small daily "noise" from deterministic hash
        noise = math.sin(doy * 7.3 + day.year * 0.1) * 2.0
        t_mean += noise

        tmin = t_mean - 4.0  # daily range ~8°C
        tmax = t_mean + 4.0
        temp = (tmin + tmax) / 2.0

        # -- Solar radiation: sinusoidal, peaks in June --
        # Summer: ~20 MJ/m²/day, Winter: ~3 MJ/m²/day → convert to J/m²/day
        irrad_mj = 3.0 + 17.0 * max(0, math.sin(2 * math.pi * (doy - 80) / 365))
        irrad = irrad_mj * 1e6  # Convert MJ → J (PCSE units)

        # -- Wind speed: 2-5 m/s --
        wind = 3.0 + 1.0 * math.sin(doy * 0.5)

        # -- Vapour pressure: derived from dewpoint ~= tmin - 2°C --
        # Using the Magnus formula: SVP = 0.6108 * exp(17.27*T / (T+237.3))
        tdew = tmin - 2.0
        vap_kpa = 0.6108 * math.exp(17.27 * tdew / (tdew + 237.3))
        vap_hpa = vap_kpa * 10.0  # PCSE uses hPa (verified: weather.py line 80)

        # -- Rainfall: sporadic, more in autumn/winter --
        # Simple pattern: some rain days scattered through the year
        rain_probability = 0.3 + 0.2 * math.cos(2 * math.pi * (doy - 200) / 365)
        rain_trigger = abs(math.sin(doy * 3.7 + day.year * 1.3))
        if rain_trigger < rain_probability:
            rain_mm = 2.0 + 8.0 * rain_trigger  # 2-10 mm
        else:
            rain_mm = 0.0
        rain_cm = rain_mm / 10.0  # PCSE uses cm/day (verified: weather.py line 61)

        # -- Reference evapotranspiration (E0, ES0, ET0) --
        # Use PCSE's own reference_ET function (verified: util.py line 36)
        # Signature: reference_ET(DAY, LAT, ELEV, TMIN, TMAX, IRRAD, VAP, WIND,
        #                         ANGSTA, ANGSTB, ETMODEL="PM")
        # Returns (E0, ES0, ET0) in mm/day
        try:
            e0_mm, es0_mm, et0_mm = reference_ET(
                day, self.latitude, self.elevation,
                tmin, tmax, irrad, vap_hpa, wind,
                self.angstA, self.angstB, ETMODEL="PM"
            )
        except (ValueError, ZeroDivisionError):
            # Fallback for edge cases (very short days, extreme values)
            e0_mm = es0_mm = et0_mm = 0.1

        # Convert ET values from mm/day to cm/day (PCSE units)
        e0 = e0_mm / 10.0
        es0 = es0_mm / 10.0
        et0 = et0_mm / 10.0

        # -- Build WeatherDataContainer --
        # All required fields verified from base/weather.py lines 73-78
        wdc = WeatherDataContainer(
            LAT=self.latitude,
            LON=self.longitude,
            ELEV=self.elevation,
            DAY=day,
            IRRAD=irrad,
            TMIN=tmin,
            TMAX=tmax,
            TEMP=temp,
            VAP=vap_hpa,
            RAIN=rain_cm,
            WIND=wind,
            E0=e0,
            ES0=es0,
            ET0=et0,
        )
        return wdc


# =============================================================================
# STEP 3: MAIN SIMULATION FUNCTION
# =============================================================================

def run_minimal_simulation():
    """Run a complete WOFOST 7.2 water-limited wheat simulation.

    Returns:
        list[dict]: Daily output records with day, LAI, SM, TAGP, TWSO, DVS
    """

    print("=" * 70)
    print("  AgriTwin — Minimal WOFOST Simulation Runner")
    print("=" * 70)

    # ── 3a. Weather Data Provider ────────────────────────────────────────
    # Using synthetic weather so the script works without internet.
    # For real use, replace with:
    #   from pcse.input import NASAPowerWeatherDataProvider
    #   wdp = NASAPowerWeatherDataProvider(latitude=52.0, longitude=5.5)
    print("\n[1/7] Creating synthetic weather provider...")
    wdp = SyntheticWeatherProvider(
        latitude=52.0,
        longitude=5.5,
        elevation=10.0,
        start_year=2020,
        end_year=2021
    )
    print(f"       Weather available: {wdp.first_date} to {wdp.last_date}")

    # ── 3b. Crop Parameters ─────────────────────────────────────────────
    # Load from local YAML files in external_repos/WOFOST_crop_parameters.
    # YAMLCropDataProvider extends MultiCropDataProvider (which extends dict).
    # After construction, you MUST call set_active_crop() to populate it.
    #
    # Verified: input/yaml_cropdataprovider.py line 105:
    #   __init__(self, model=Wofost72_PP, fpath=None, repository=None, force_reload=False)
    print("\n[2/7] Loading crop parameters from local YAML files...")
    crop_param_dir = os.path.join(
        os.path.dirname(__file__),  # backend/app/simulation/
        "..", "..", "..",           # → project root
        "external_repos", "WOFOST_crop_parameters"
    )
    crop_param_dir = os.path.abspath(crop_param_dir)

    if not os.path.isdir(crop_param_dir):
        print(f"ERROR: Crop parameter directory not found: {crop_param_dir}")
        print("       Falling back to default (remote GitHub repository)...")
        cropd = YAMLCropDataProvider()
    else:
        print(f"       Loading from: {crop_param_dir}")
        cropd = YAMLCropDataProvider(fpath=crop_param_dir)

    # List available crops and varieties
    # Verified: yaml_cropdataprovider.py line 272: get_crops_varieties()
    # NOTE: get_cropnames() and get_varieties() do NOT exist!
    crops_varieties = cropd.get_crops_varieties()
    wheat_varieties = list(crops_varieties.get('wheat', []))
    print(f"       Available wheat varieties: {wheat_varieties}")

    # Activate crop parameters for Winter_wheat_101
    # Verified: yaml_cropdataprovider.py line 247: set_active_crop(crop_name, variety_name)
    crop_name = "wheat"
    variety_name = "Winter_wheat_101"
    cropd.set_active_crop(crop_name, variety_name)
    print(f"       Active crop: {crop_name} / {variety_name}")

    # ── 3c. Soil Parameters ─────────────────────────────────────────────
    # PCSE has no dedicated soil data provider for real soils.
    # Soil data is passed as a plain dict with these 8 keys.
    # Verified keys from: input/soildataproviders.py DummySoilDataProvider._defaults
    #
    # CRITICAL: Must satisfy SMW < SMFCF < SM0 or WOFOST crashes!
    print("\n[3/7] Setting up soil parameters...")
    soildata = {
        "SMFCF": 0.30,   # Field capacity                  [cm³/cm³]
        "SMW":   0.10,    # Wilting point                    [cm³/cm³]
        "SM0":   0.45,    # Saturation (porosity)            [cm³/cm³]
        "CRAIRC": 0.06,   # Critical air content             [cm³/cm³]
        "RDMSOL": 120.0,  # Maximum rootable soil depth      [cm]
        "K0":    10.0,    # Hydraulic conductivity at sat.   [cm/day]
        "SOPE":  10.0,    # Max percolation rate root zone   [cm/day]
        "KSUB":  10.0,    # Max percolation rate subsoil     [cm/day]
    }
    # Validate physical ordering
    assert soildata["SMW"] < soildata["SMFCF"] < soildata["SM0"], \
        f"Soil moisture ordering violated: SMW={soildata['SMW']} < SMFCF={soildata['SMFCF']} < SM0={soildata['SM0']}"
    print(f"       SMW={soildata['SMW']}, SMFCF={soildata['SMFCF']}, SM0={soildata['SM0']} ✓")

    # ── 3d. Site Parameters ─────────────────────────────────────────────
    # WOFOST72SiteDataProvider creates a validated dict with:
    #   Required: WAV (initial available water in soil profile, cm)
    #   Defaults: IFUNRN=0, NOTINF=0, SSI=0, SSMAX=0, SMLIM=0.4
    #
    # Verified: input/sitedataproviders.py line 55-81
    # WARNING: RDMSOL does NOT go here — it belongs in soildata!
    print("\n[4/7] Setting up site parameters...")
    sitedata = WOFOST72SiteDataProvider(WAV=10.0)
    print(f"       Site data: {dict(sitedata)}")

    # ── 3e. Combine into ParameterProvider ──────────────────────────────
    # ParameterProvider acts as a ChainMap over crop, soil, and site params.
    # Lookups search in order: override → site → timer → soil → crop → derived
    #
    # Verified: base/parameter_providers.py line 46:
    #   __init__(self, sitedata=None, timerdata=None, soildata=None, cropdata=None)
    # NOTE: timerdata is set automatically by the Engine when CROP_START fires.
    print("\n[5/7] Assembling ParameterProvider...")
    params = ParameterProvider(
        cropdata=cropd,       # YAMLCropDataProvider (with active crop set)
        soildata=soildata,    # dict with 8 soil parameters
        sitedata=sitedata,    # WOFOST72SiteDataProvider (WAV required)
    )
    print(f"       ParameterProvider: {len(params)} parameters loaded")

    # ── 3f. AgroManagement ──────────────────────────────────────────────
    # AgroManagement defines the crop calendar: when to sow, when to harvest,
    # and any timed/state events (irrigation, etc.).
    #
    # CRITICAL RULES (verified from agromanagement_guide.md):
    #   - campaign_start_date MUST be <= crop_start_date
    #   - crop_name must be lowercase, matching PCSE database
    #   - crop_start_type: "sowing" or "emergence"
    #   - crop_end_type: "harvest", "maturity", or "earliest"
    #   - max_duration prevents infinite loops
    #
    # The Engine expects a Python LIST (not the full dict).
    # YAMLAgroManagementReader returns a list directly (it subclasses list).
    # When using yaml.safe_load, extract with: agro_dict['AgroManagement']
    print("\n[6/7] Building AgroManagement...")
    agro_yaml = """
AgroManagement:
- 2020-10-01:
    CropCalendar:
      crop_name: wheat
      variety_name: Winter_wheat_101
      crop_start_date: 2020-10-15
      crop_start_type: sowing
      crop_end_date: 2021-07-30
      crop_end_type: harvest
      max_duration: 300
    TimedEvents: null
    StateEvents: null
"""
    agro = yaml.safe_load(agro_yaml)['AgroManagement']
    print(f"       Campaign start: 2020-10-01")
    print(f"       Sowing date:    2020-10-15")
    print(f"       Harvest date:   2021-07-30 (or earlier by max_duration=300)")

    # ── 3g. Initialize and Run WOFOST ───────────────────────────────────
    # Engine constructor (verified: engine.py line 117):
    #   __init__(self, parameterprovider, weatherdataprovider, agromanagement,
    #            config=None, output_vars=None, summary_vars=None, terminal_vars=None)
    #
    # The Wofost72_WLP_FD class sets config="Wofost72_WLP_CWB.conf" which loads:
    #   SOIL = WaterbalanceFD
    #   CROP = Wofost72
    #   OUTPUT_VARS = ["DVS","LAI","TAGP","TWSO","TWLV","TWST",
    #                  "TWRT","TRA","RD","SM","WWLOW","RFTRA"]
    #   OUTPUT_INTERVAL = "daily"
    print("\n[7/7] Running WOFOST simulation...")
    wofost = Wofost72_WLP_FD(params, wdp, agro)

    # run_till_terminate() loops internally until a TERMINATE signal fires.
    # The TERMINATE signal comes when crop_end_date is reached or DVS >= 2.0.
    # Verified: engine.py line 238-242
    wofost.run_till_terminate()
    print("       Simulation completed!")

    # ── 3h. Extract Outputs ─────────────────────────────────────────────
    # get_output() returns self._saved_output — a list of dicts, one per day.
    # Each dict has keys from OUTPUT_VARS plus 'day' (datetime.date).
    # Verified: engine.py line 424-431
    output = wofost.get_output()

    # get_summary_output() returns summary at crop finish.
    # Includes LAIMAX, TWSO, DOE (date of emergence), DOA (anthesis), etc.
    # Verified: engine.py line 433-437
    summary = wofost.get_summary_output()

    return output, summary


# =============================================================================
# STEP 4: OUTPUT FORMATTING AND DISPLAY
# =============================================================================

def print_results(output, summary):
    """Print simulation results in a clean tabular format."""

    print("\n" + "=" * 70)
    print("  SIMULATION RESULTS")
    print("=" * 70)
    print(f"\n  Total simulation days: {len(output)}")

    if not output:
        print("  WARNING: No output generated! Check AgroManagement dates.")
        return

    # ── Helper to format one row safely (handles None for pre-sowing days) ──
    def fmt(val, width, decimals):
        """Format a numeric value, returning padded spaces if None."""
        if val is None:
            return " " * (width + 1)
        return f" {val:{width}.{decimals}f}"

    # ── Daily output table (first 10 + last 10 days) ────────────────────
    header = f"  {'Date':<12} {'DVS':>6} {'LAI':>7} {'SM':>7} {'TAGP':>9} {'TWSO':>9}"
    print(f"\n{header}")
    print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")

    def print_row(rec):
        """Print one daily output record."""
        day = rec.get('day', '?')
        line = f"  {str(day):<12}"
        line += fmt(rec.get('DVS'), 6, 3)
        line += fmt(rec.get('LAI'), 7, 3)
        line += fmt(rec.get('SM'), 7, 4)
        line += fmt(rec.get('TAGP'), 9, 1)
        line += fmt(rec.get('TWSO'), 9, 1)
        print(line)

    # Print first 10 days
    for rec in output[:10]:
        print_row(rec)

    if len(output) > 20:
        print(f"  {'... (skipping middle days) ...':^54}")

    # Print last 10 days
    start_idx = max(10, len(output) - 10)
    for rec in output[start_idx:]:
        print_row(rec)

    # ── Summary statistics ──────────────────────────────────────────────
    # Find key milestones from the output
    peak_lai = max((r.get('LAI', 0) or 0) for r in output)
    final_twso = output[-1].get('TWSO', 0) or 0
    final_tagp = output[-1].get('TAGP', 0) or 0
    final_dvs = output[-1].get('DVS', 0) or 0
    harvest_index = final_twso / final_tagp if final_tagp > 0 else 0

    print(f"\n  {'─' * 45}")
    print(f"  Peak LAI:           {peak_lai:.3f} m²/m²")
    print(f"  Final DVS:          {final_dvs:.3f}")
    print(f"  Final TAGP:         {final_tagp:.1f} kg/ha")
    print(f"  Final TWSO (yield): {final_twso:.1f} kg/ha")
    print(f"  Harvest Index:      {harvest_index:.3f}")

    # ── Summary output from PCSE ────────────────────────────────────────
    if summary:
        print(f"\n  PCSE Summary Output:")
        for key, value in summary[0].items():
            if value is not None:
                if isinstance(value, float):
                    print(f"    {key:<12}: {value:.2f}")
                else:
                    print(f"    {key:<12}: {value}")


# =============================================================================
# STEP 5: ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    try:
        output, summary = run_minimal_simulation()
        print_results(output, summary)
        print("\n✅ Simulation completed successfully!")
    except Exception as e:
        print(f"\n❌ Simulation failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
