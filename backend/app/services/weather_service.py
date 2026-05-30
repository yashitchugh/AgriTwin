"""
weather_service.py — NASA POWER Weather Ingestion Service
==========================================================

Fetches daily weather data from the NASA POWER API, converts all variables
to PCSE-compatible units, computes reference evapotranspiration (Penman-
Monteith), and returns a WeatherDataProvider ready for WOFOST.

This module implements the SAME processing pipeline as PCSE's built-in
NASAPowerWeatherDataProvider (input/nasapower.py), but with:
  - Bounded date ranges (avoids the 422 error from requesting future dates)
  - JSON file caching (human-readable, not pickle — safer across versions)
  - Explicit error handling and logging
  - Clean separation from the simulation module

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

UNIT CONVERSION REFERENCE (verified against input/nasapower.py lines 16-20):

  ┌──────────────────────┬───────────────┬──────────────┬──────────────────┐
  │ NASA POWER variable  │ NASA unit     │ PCSE field   │ PCSE unit        │
  ├──────────────────────┼───────────────┼──────────────┼──────────────────┤
  │ T2M_MAX              │ °C            │ TMAX         │ °C (no change)   │
  │ T2M_MIN              │ °C            │ TMIN         │ °C (no change)   │
  │ T2M                  │ °C            │ TEMP         │ °C (no change)   │
  │ ALLSKY_SFC_SW_DWN    │ MJ/m²/day     │ IRRAD        │ J/m²/day (×1e6)  │
  │ PRECTOTCORR          │ mm/day        │ RAIN         │ cm/day (÷10)     │
  │ WS2M                 │ m/s           │ WIND         │ m/s (no change)  │
  │ T2MDEW               │ °C (dewpoint) │ VAP          │ hPa (Magnus→×10) │
  │ TOA_SW_DWN           │ MJ/m²/day     │ (internal)   │ Angstrom A/B est │
  └──────────────────────┴───────────────┴──────────────┴──────────────────┘

  E0, ES0, ET0 are COMPUTED by pcse.util.reference_ET(), not from NASA.
  They are returned in mm/day and must be converted to cm/day (÷10).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import logging
import datetime as dt
from typing import Optional
from math import exp

import requests
import numpy as np
import pandas as pd

# Verified PCSE imports (base/__init__.py line 14, util.py line 36)
from pcse.base import WeatherDataProvider, WeatherDataContainer
from pcse.util import ea_from_tdew, reference_ET, check_angstromAB
from pcse.exceptions import PCSEError

logger = logging.getLogger(__name__)

# ─── NASA POWER API Configuration ───────────────────────────────────────

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# Parameters to request (verified: input/nasapower.py lines 71-72)
NASA_POWER_VARIABLES = [
    "TOA_SW_DWN",          # Top-of-atmosphere shortwave (for Angstrom A/B)
    "ALLSKY_SFC_SW_DWN",   # Surface shortwave radiation [MJ/m²/day]
    "T2M",                 # Mean air temperature at 2m [°C]
    "T2M_MIN",             # Minimum temperature [°C]
    "T2M_MAX",             # Maximum temperature [°C]
    "T2MDEW",              # Dewpoint temperature [°C]
    "WS2M",                # Wind speed at 2m [m/s]
    "PRECTOTCORR",         # Corrected precipitation [mm/day]
]

# Default Angstrom coefficients (input/nasapower.py lines 75-76)
DEFAULT_ANGSTROM_A = 0.29
DEFAULT_ANGSTROM_B = 0.49

# Fill value used by NASA POWER for missing data
NASA_FILL_VALUE = -999.0

# Default cache directory
DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".agritwin_cache", "weather"
)


# ─── Unit Conversion Functions ───────────────────────────────────────────
# Exactly matching PCSE's own lambdas (input/nasapower.py lines 17-19)

def _mj_to_j(x: float) -> float:
    """Convert MJ/m²/day → J/m²/day.

    NASA POWER delivers radiation in MJ. PCSE requires J.
    Forgetting this gives 1e6× less radiation — crop will not grow.
    """
    return x * 1e6


def _mm_to_cm(x: float) -> float:
    """Convert mm/day → cm/day.

    NASA POWER delivers rain in mm. PCSE requires cm.
    Forgetting this gives 10× rainfall — completely wrong water balance.
    """
    return x / 10.0


def _tdew_to_vap_hpa(tdew_celsius: float) -> float:
    """Convert dewpoint temperature [°C] → actual vapour pressure [hPa].

    Uses PCSE's ea_from_tdew() which returns kPa, then ×10 for hPa.
    Verified: util.py line 343 (ea_from_tdew) and input/nasapower.py line 19.

    The Magnus formula:
        ea = 0.6108 × exp(17.27 × Tdew / (Tdew + 237.3))  [kPa]
    """
    return ea_from_tdew(tdew_celsius) * 10.0


# ─── Weather Service ─────────────────────────────────────────────────────

class WeatherService:
    """Fetches and processes NASA POWER weather data for PCSE/WOFOST.

    Implements the same processing pipeline as PCSE's built-in
    NASAPowerWeatherDataProvider but with bounded date ranges and
    JSON-based caching.

    Usage:
        svc = WeatherService()
        wdp = svc.get_weather_provider(latitude=28.6, longitude=77.2,
                                        start_date=date(2020, 10, 1),
                                        end_date=date(2021, 7, 30))
        # wdp is a WeatherDataProvider — pass directly to WOFOST engine
    """

    def __init__(self, cache_dir: Optional[str] = None, cache_max_age_days: int = 90):
        """
        Args:
            cache_dir:          Directory to store JSON weather cache files.
                                Defaults to .agritwin_cache/weather/ in project root.
            cache_max_age_days: Re-fetch if cache is older than this many days.
        """
        self.cache_dir = os.path.abspath(cache_dir or DEFAULT_CACHE_DIR)
        self.cache_max_age_days = cache_max_age_days

    def get_weather_provider(
        self,
        latitude: float,
        longitude: float,
        start_date: dt.date,
        end_date: dt.date,
        force_update: bool = False,
        et_model: str = "PM",
    ) -> WeatherDataProvider:
        """Fetch NASA POWER data and return a PCSE-ready WeatherDataProvider.

        Args:
            latitude:     Site latitude (-90 to 90)
            longitude:    Site longitude (-180 to 180)
            start_date:   First day of weather data needed
            end_date:     Last day of weather data needed
            force_update: Bypass cache, always fetch fresh data
            et_model:     "PM" (Penman-Monteith) or "P" (Penman) for ref ET

        Returns:
            WeatherDataProvider with daily WeatherDataContainers

        Raises:
            ValueError:    Invalid coordinates or date range
            RuntimeError:  NASA POWER API failure
            PCSEError:     Reference ET calculation failure
        """
        # ── Validate inputs ──────────────────────────────────────────────
        if not (-90 <= latitude <= 90):
            raise ValueError(f"Latitude must be in [-90, 90], got {latitude}")
        if not (-180 <= longitude <= 180):
            raise ValueError(f"Longitude must be in [-180, 180], got {longitude}")
        if start_date >= end_date:
            raise ValueError(f"start_date ({start_date}) must be < end_date ({end_date})")

        # Clamp end_date to yesterday (NASA POWER has ~5 day delay)
        yesterday = dt.date.today() - dt.timedelta(days=1)
        if end_date > yesterday:
            logger.warning(
                "end_date %s is in the future; clamping to %s (NASA POWER has ~5 day delay)",
                end_date, yesterday,
            )
            end_date = yesterday

        logger.info(
            "Requesting weather for (%.4f, %.4f) from %s to %s",
            latitude, longitude, start_date, end_date,
        )

        # ── Try cache first ──────────────────────────────────────────────
        if not force_update:
            cached_wdp = self._load_from_cache(latitude, longitude, start_date, end_date, et_model)
            if cached_wdp is not None:
                return cached_wdp

        # ── Fetch from NASA POWER API ────────────────────────────────────
        raw_json = self._fetch_from_nasa(latitude, longitude, start_date, end_date)

        # ── Process into PCSE format ─────────────────────────────────────
        elevation = float(raw_json["geometry"]["coordinates"][2])
        df_power = self._parse_power_records(raw_json)
        angst_a, angst_b = self._estimate_angstrom_ab(df_power)
        df_pcse = self._convert_power_to_pcse(df_power, latitude, longitude, elevation)

        # ── Build WeatherDataProvider ────────────────────────────────────
        wdp = self._build_provider(
            df_pcse, latitude, longitude, elevation, angst_a, angst_b, et_model,
        )

        # ── Save to cache ────────────────────────────────────────────────
        self._save_to_cache(raw_json, latitude, longitude, start_date, end_date)

        logger.info(
            "Weather loaded: %s to %s (%d days), elevation=%.1fm",
            wdp.first_date, wdp.last_date,
            (wdp.last_date - wdp.first_date).days + 1,
            elevation,
        )
        return wdp

    # ─── NASA POWER API ──────────────────────────────────────────────────

    def _fetch_from_nasa(
        self,
        latitude: float,
        longitude: float,
        start_date: dt.date,
        end_date: dt.date,
    ) -> dict:
        """Query the NASA POWER daily point API.

        Endpoint: https://power.larc.nasa.gov/api/temporal/daily/point
        Community: AG (agroclimatology) — verified: input/nasapower.py line 222

        Returns:
            Parsed JSON response dict

        Raises:
            RuntimeError on HTTP errors or empty responses
        """
        payload = {
            "parameters": ",".join(NASA_POWER_VARIABLES),
            "latitude": latitude,
            "longitude": longitude,
            "start": start_date.strftime("%Y%m%d"),
            "end": end_date.strftime("%Y%m%d"),
            "community": "AG",
            "format": "JSON",
        }

        logger.info("Fetching from NASA POWER: %s to %s", start_date, end_date)

        try:
            response = requests.get(NASA_POWER_URL, params=payload, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f"NASA POWER request failed: {e}") from e

        if response.status_code != 200:
            raise RuntimeError(
                f"NASA POWER returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        data = response.json()

        # Validate response structure
        if "properties" not in data or "parameter" not in data.get("properties", {}):
            raise RuntimeError(
                "NASA POWER response missing expected 'properties.parameter' structure"
            )

        logger.info("NASA POWER fetch successful")
        return data

    # ─── Data Processing ─────────────────────────────────────────────────

    def _parse_power_records(self, powerdata: dict) -> pd.DataFrame:
        """Parse NASA POWER JSON into a clean DataFrame.

        Removes rows with any missing values (fill_value = -999.0).
        Verified logic from: input/nasapower.py lines 312-333.
        """
        fill_value = float(powerdata["header"]["fill_value"])

        df_power = {}
        for varname in NASA_POWER_VARIABLES:
            series = pd.Series(powerdata["properties"]["parameter"][varname])
            series[series == fill_value] = np.nan
            df_power[varname] = series

        df_power = pd.DataFrame(df_power)
        df_power["DAY"] = pd.to_datetime(df_power.index, format="%Y%m%d")

        # Drop rows with any NaN (missing data for any variable)
        n_before = len(df_power)
        df_power = df_power.dropna()
        n_dropped = n_before - len(df_power)
        if n_dropped > 0:
            logger.warning("Dropped %d days with missing NASA POWER data", n_dropped)

        return df_power

    def _estimate_angstrom_ab(self, df_power: pd.DataFrame) -> tuple[float, float]:
        """Estimate Angstrom A/B coefficients from TOA and surface radiation.

        The Angstrom equation relates global radiation to sunshine duration:
            Rs = Ra × (A + B × n/N)
        where Rs = surface radiation, Ra = TOA radiation, n/N = sunshine fraction.

        A ≈ 5th percentile of Rs/Ra (minimum atmospheric transmissivity)
        A+B ≈ 98th percentile of Rs/Ra (maximum transmissivity on clear days)

        Verified logic from: input/nasapower.py lines 161-205.
        """
        if len(df_power) < 200:
            logger.warning(
                "Only %d days available — using default Angstrom A=%.2f, B=%.2f",
                len(df_power), DEFAULT_ANGSTROM_A, DEFAULT_ANGSTROM_B,
            )
            return DEFAULT_ANGSTROM_A, DEFAULT_ANGSTROM_B

        relative_radiation = df_power["ALLSKY_SFC_SW_DWN"] / df_power["TOA_SW_DWN"]
        valid = relative_radiation.dropna()

        angst_a = float(np.percentile(valid.values, 5))
        angst_ab = float(np.percentile(valid.values, 98))
        angst_b = angst_ab - angst_a

        try:
            check_angstromAB(angst_a, angst_b)
        except PCSEError as e:
            logger.warning(
                "Angstrom A=%.3f, B=%.3f outside valid range (%s). Using defaults.",
                angst_a, angst_b, e,
            )
            return DEFAULT_ANGSTROM_A, DEFAULT_ANGSTROM_B

        logger.info("Estimated Angstrom A=%.3f, B=%.3f", angst_a, angst_b)
        return angst_a, angst_b

    def _convert_power_to_pcse(
        self,
        df_power: pd.DataFrame,
        latitude: float,
        longitude: float,
        elevation: float,
    ) -> pd.DataFrame:
        """Convert NASA POWER DataFrame to PCSE-compatible DataFrame.

        This is where ALL unit conversions happen. Each conversion is
        documented with its scientific rationale.

        Verified logic from: input/nasapower.py lines 335-350.
        """
        df_pcse = pd.DataFrame({
            # ── Temperature: no conversion needed ────────────────────────
            # NASA POWER and PCSE both use °C
            "TMAX": df_power["T2M_MAX"],
            "TMIN": df_power["T2M_MIN"],
            "TEMP": df_power["T2M"],

            # ── Radiation: MJ/m²/day → J/m²/day ─────────────────────────
            # CRITICAL: multiply by 1,000,000 (1e6)
            # Forgetting this gives 1e6× less radiation → crop won't grow
            "IRRAD": df_power["ALLSKY_SFC_SW_DWN"].apply(_mj_to_j),

            # ── Rainfall: mm/day → cm/day ────────────────────────────────
            # CRITICAL: divide by 10
            # Forgetting this gives 10× rainfall → wrong water balance
            "RAIN": df_power["PRECTOTCORR"].apply(_mm_to_cm),

            # ── Wind: no conversion needed ───────────────────────────────
            # Both NASA POWER and PCSE use m/s at 2m height
            "WIND": df_power["WS2M"],

            # ── Vapour pressure: dewpoint °C → hPa ──────────────────────
            # Via Magnus formula: ea = 0.6108 × exp(17.27×Td/(Td+237.3)) [kPa]
            # Then ×10 to get hPa (PCSE unit for VAP)
            "VAP": df_power["T2MDEW"].apply(_tdew_to_vap_hpa),

            # ── Date: pandas Timestamp → Python date ─────────────────────
            "DAY": df_power["DAY"].apply(lambda d: d.date()),

            # ── Site coordinates (constant for all rows) ─────────────────
            "LAT": latitude,
            "LON": longitude,
            "ELEV": elevation,
        })

        return df_pcse

    def _build_provider(
        self,
        df_pcse: pd.DataFrame,
        latitude: float,
        longitude: float,
        elevation: float,
        angst_a: float,
        angst_b: float,
        et_model: str,
    ) -> WeatherDataProvider:
        """Build a WeatherDataProvider from the processed DataFrame.

        For each day:
          1. Compute reference ET (E0, ES0, ET0) using Penman-Monteith
          2. Convert ET from mm/day → cm/day
          3. Create a WeatherDataContainer with all required fields
          4. Store in the provider's internal dict

        Verified logic from: input/nasapower.py lines 288-310.
        """
        wdp = WeatherDataProvider()
        wdp.latitude = latitude
        wdp.longitude = longitude
        wdp.elevation = elevation
        wdp.angstA = angst_a
        wdp.angstB = angst_b
        wdp.ETmodel = et_model
        wdp.description = [f"NASA POWER weather for ({latitude:.4f}, {longitude:.4f})"]

        records = df_pcse.to_dict(orient="records")
        n_errors = 0

        for rec in records:
            # Compute reference evapotranspiration using PCSE's Penman-Monteith
            # Verified: util.py line 36
            # Returns (E0, ES0, ET0) in mm/day
            try:
                e0_mm, es0_mm, et0_mm = reference_ET(
                    rec["DAY"], rec["LAT"], rec["ELEV"],
                    rec["TMIN"], rec["TMAX"], rec["IRRAD"],
                    rec["VAP"], rec["WIND"],
                    angst_a, angst_b, et_model,
                )
            except ValueError as e:
                logger.warning("ET calculation failed for %s: %s", rec["DAY"], e)
                n_errors += 1
                continue

            # Convert ET from mm/day → cm/day (PCSE unit)
            rec["E0"] = e0_mm / 10.0
            rec["ES0"] = es0_mm / 10.0
            rec["ET0"] = et0_mm / 10.0

            # Build and store container
            # Verified slots: base/weather.py lines 73-78
            wdc = WeatherDataContainer(**rec)
            wdp._store_WeatherDataContainer(wdc, wdc.DAY)

        if n_errors > 0:
            logger.warning("Skipped %d days due to ET calculation errors", n_errors)

        return wdp

    # ─── Caching ─────────────────────────────────────────────────────────

    def _get_cache_path(
        self, latitude: float, longitude: float,
        start_date: dt.date, end_date: dt.date,
    ) -> str:
        """Generate cache file path.

        Format: weather_LAT{lat}_LON{lon}_{start}_{end}.json
        Lat/lon are truncated to 0.1° (matches NASA POWER grid resolution).
        """
        lat_key = int(latitude * 10)
        lon_key = int(longitude * 10)
        fname = f"weather_LAT{lat_key:05d}_LON{lon_key:05d}_{start_date}_{end_date}.json"
        return os.path.join(self.cache_dir, fname)

    def _save_to_cache(
        self, raw_json: dict,
        latitude: float, longitude: float,
        start_date: dt.date, end_date: dt.date,
    ) -> None:
        """Save raw NASA POWER JSON response to cache file."""
        cache_path = self._get_cache_path(latitude, longitude, start_date, end_date)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        try:
            with open(cache_path, "w") as f:
                json.dump(raw_json, f)
            logger.info("Weather cached to %s", cache_path)
        except (IOError, OSError) as e:
            logger.warning("Failed to write cache: %s", e)

    def _load_from_cache(
        self,
        latitude: float, longitude: float,
        start_date: dt.date, end_date: dt.date,
        et_model: str,
    ) -> Optional[WeatherDataProvider]:
        """Try to load weather from cache. Returns None if cache miss or stale."""
        cache_path = self._get_cache_path(latitude, longitude, start_date, end_date)

        if not os.path.exists(cache_path):
            logger.debug("No cache file at %s", cache_path)
            return None

        # Check age
        file_age_days = (dt.date.today() - dt.date.fromtimestamp(
            os.path.getmtime(cache_path)
        )).days

        if file_age_days > self.cache_max_age_days:
            logger.info("Cache file is %d days old (max=%d), re-fetching",
                        file_age_days, self.cache_max_age_days)
            return None

        try:
            with open(cache_path, "r") as f:
                raw_json = json.load(f)
            logger.info("Loaded weather from cache (%d days old)", file_age_days)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Cache file corrupt: %s", e)
            return None

        # Re-process from cached JSON
        elevation = float(raw_json["geometry"]["coordinates"][2])
        df_power = self._parse_power_records(raw_json)
        angst_a, angst_b = self._estimate_angstrom_ab(df_power)
        df_pcse = self._convert_power_to_pcse(df_power, latitude, longitude, elevation)

        return self._build_provider(
            df_pcse, latitude, longitude, elevation, angst_a, angst_b, et_model,
        )
