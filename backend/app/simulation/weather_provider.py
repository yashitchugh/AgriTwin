"""
weather_provider.py — Weather Data Provider Factory
=====================================================

Provides weather data for PCSE/WOFOST simulations. Currently implements a
SyntheticWeatherProvider for offline development and testing. Designed to be
swapped for NASAPowerWeatherDataProvider in production.

PCSE Weather API (verified against base/weather.py):
  - WeatherDataProvider: base class, stores WeatherDataContainers keyed by (date, member_id)
  - WeatherDataContainer: single-day record with slots:
      Required: LAT, LON, ELEV, DAY, IRRAD, TMIN, TMAX, VAP, RAIN, WIND, E0, ES0, ET0
      Optional: TEMP, SNOWDEPTH
  - Units (base/weather.py lines 80-83):
      IRRAD: J/m²/day | RAIN: cm/day | VAP: hPa | E0/ES0/ET0: cm/day | WIND: m/s
"""

import math
import logging
import datetime as dt
from typing import Optional

# Verified imports (base/__init__.py line 14)
from pcse.base import WeatherDataProvider, WeatherDataContainer
# Verified (util.py line 36) — computes Penman-Monteith reference ET
from pcse.util import reference_ET

logger = logging.getLogger(__name__)


class SyntheticWeatherProvider(WeatherDataProvider):
    """Deterministic synthetic weather for offline simulation testing.

    Generates physically plausible daily weather data using sinusoidal
    seasonal cycles tuned for a temperate European climate (Netherlands).
    All values are deterministic (based on day-of-year), ensuring
    reproducible simulation results.

    This provider will be replaced by NASAPowerWeatherDataProvider for
    production runs:
        from pcse.input import NASAPowerWeatherDataProvider  # NOT pcse.db
        wdp = NASAPowerWeatherDataProvider(latitude=52.0, longitude=5.5)

    Args:
        latitude:   Decimal degrees (-90 to 90)
        longitude:  Decimal degrees (-180 to 180)
        elevation:  Meters above sea level
        start_year: First year of generated weather
        end_year:   Last year of generated weather
    """

    def __init__(
        self,
        latitude: float = 52.0,
        longitude: float = 5.5,
        elevation: float = 10.0,
        start_year: int = 2020,
        end_year: int = 2021,
    ) -> None:
        # Verified: base/weather.py line 228 — initializes self.store = {}
        WeatherDataProvider.__init__(self)

        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.description = ["Synthetic weather data for AgriTwin testing"]

        # Angstrom coefficients for atmospheric transmissivity estimation.
        # These are standard defaults used by NASAPowerWeatherDataProvider
        # (verified: input/nasapower.py lines 75-76)
        self.angstA = 0.29
        self.angstB = 0.49

        # Generate daily records for the full period
        start_date = dt.date(start_year, 1, 1)
        end_date = dt.date(end_year, 12, 31)
        n_days = (end_date - start_date).days + 1

        logger.info(
            "Generating %d days of synthetic weather for (%.2f, %.2f)",
            n_days, latitude, longitude,
        )

        current = start_date
        while current <= end_date:
            wdc = self._generate_day(current)
            # Verified: base/weather.py line 341
            self._store_WeatherDataContainer(wdc, current)
            current += dt.timedelta(days=1)

        logger.info(
            "Synthetic weather ready: %s to %s", self.first_date, self.last_date
        )

    def _generate_day(self, day: dt.date) -> WeatherDataContainer:
        """Generate one day of physically plausible weather.

        Physics rationale for each variable:
          - Temperature: annual sinusoidal cycle (cool winters, warm summers)
          - Radiation: follows solar geometry, peaks near summer solstice
          - Rainfall: higher probability in autumn/winter (frontal systems)
          - Wind: moderate, slight seasonal variation
          - Vapour pressure: derived from dewpoint temperature (Tdew ≈ Tmin - 2°C)
          - E0/ES0/ET0: computed via PCSE's own Penman-Monteith implementation
        """
        doy = day.timetuple().tm_yday

        # ── Temperature ──────────────────────────────────────────────────
        # Sinusoidal: winter ~2°C, summer ~20°C, phase-shifted to peak ~DOY 200
        t_mean = 11.0 + 9.0 * math.sin(2 * math.pi * (doy - 100) / 365)
        # Deterministic daily perturbation (±2°C)
        noise = math.sin(doy * 7.3 + day.year * 0.1) * 2.0
        t_mean += noise
        tmin = t_mean - 4.0
        tmax = t_mean + 4.0
        temp = (tmin + tmax) / 2.0

        # ── Solar radiation ──────────────────────────────────────────────
        # Summer: ~20 MJ/m²/day, Winter: ~3 MJ/m²/day
        irrad_mj = 3.0 + 17.0 * max(0, math.sin(2 * math.pi * (doy - 80) / 365))
        irrad = irrad_mj * 1e6  # MJ → J/m²/day (PCSE unit)

        # ── Wind ─────────────────────────────────────────────────────────
        wind = 3.0 + 1.0 * math.sin(doy * 0.5)  # 2-4 m/s range

        # ── Vapour pressure ──────────────────────────────────────────────
        # Approximation: dewpoint ≈ Tmin - 2°C, then Magnus formula
        tdew = tmin - 2.0
        vap_kpa = 0.6108 * math.exp(17.27 * tdew / (tdew + 237.3))
        vap_hpa = vap_kpa * 10.0  # PCSE uses hPa

        # ── Rainfall ─────────────────────────────────────────────────────
        # Higher probability in autumn/winter, deterministic from DOY
        rain_prob = 0.3 + 0.2 * math.cos(2 * math.pi * (doy - 200) / 365)
        rain_trigger = abs(math.sin(doy * 3.7 + day.year * 1.3))
        rain_mm = (2.0 + 8.0 * rain_trigger) if rain_trigger < rain_prob else 0.0
        rain_cm = rain_mm / 10.0  # mm → cm (PCSE unit)

        # ── Reference ET (Penman-Monteith) ───────────────────────────────
        # Uses PCSE's own implementation (verified: util.py line 36)
        # Returns (E0, ES0, ET0) in mm/day
        try:
            e0_mm, es0_mm, et0_mm = reference_ET(
                day, self.latitude, self.elevation,
                tmin, tmax, irrad, vap_hpa, wind,
                self.angstA, self.angstB, ETMODEL="PM",
            )
        except (ValueError, ZeroDivisionError):
            e0_mm = es0_mm = et0_mm = 0.1

        # Convert mm/day → cm/day (PCSE unit for E0/ES0/ET0)
        e0 = e0_mm / 10.0
        es0 = es0_mm / 10.0
        et0 = et0_mm / 10.0

        # ── Assemble container ───────────────────────────────────────────
        # Verified slots: base/weather.py lines 73-78
        return WeatherDataContainer(
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


def create_weather_provider(
    latitude: float = 52.0,
    longitude: float = 5.5,
    elevation: float = 10.0,
    start_year: int = 2020,
    end_year: int = 2021,
    use_nasa: bool = False,
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
) -> WeatherDataProvider:
    """Factory function for weather data providers.

    Args:
        latitude:    Site latitude
        longitude:   Site longitude
        elevation:   Site elevation (used only for synthetic provider)
        start_year:  First year of weather data (synthetic mode only)
        end_year:    Last year of weather data (synthetic mode only)
        use_nasa:    If True, use WeatherService (NASA POWER with caching)
        start_date:  Start date for NASA weather fetch (required if use_nasa=True)
        end_date:    End date for NASA weather fetch (required if use_nasa=True)

    Returns:
        WeatherDataProvider instance ready for PCSE Engine
    """
    if use_nasa:
        # Use our custom WeatherService with bounded dates and JSON caching
        from backend.app.services.weather_service import WeatherService
        if start_date is None:
            start_date = dt.date(start_year, 1, 1)
        if end_date is None:
            end_date = dt.date(end_year, 12, 31)
        svc = WeatherService()
        logger.info("Using NASA POWER via WeatherService for (%.2f, %.2f)", latitude, longitude)
        return svc.get_weather_provider(latitude, longitude, start_date, end_date)
    else:
        return SyntheticWeatherProvider(
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            start_year=start_year,
            end_year=end_year,
        )

