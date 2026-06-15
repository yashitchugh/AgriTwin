"""
backend/app/data_sources/nasa_power_source.py — NasaPowerWeatherSource
=======================================================================

Concrete implementation of WeatherSource that wraps the existing
WeatherService (NASA POWER) and SyntheticWeatherProvider.

NO business logic changes — this is a pure adapter.  All scientific
calculations, caching, unit conversions, and error handling remain in:
  backend/app/services/weather_service.py     (NASA POWER with caching)
  backend/app/simulation/weather_provider.py  (SyntheticWeatherProvider)

Architecture:
  WeatherSource (ABC)
      └── NasaPowerWeatherSource   ← this class
              ├── uses_real_nasa=True  → delegates to WeatherService.get_weather_provider()
              └── uses_real_nasa=False → delegates to SyntheticWeatherProvider(...)

The simulation engine will eventually accept a WeatherSource parameter so
that test suites can inject SyntheticWeatherProvider without touching the
engine's internal logic.

Usage:
    source = NasaPowerWeatherSource(use_real=True)
    provider = source.get_weather(52.0, 5.5, date(2020,10,1), date(2021,7,31))
    wofost = Wofost72_WLP_FD(params, provider, agro)
"""

import datetime
import logging
from typing import Optional

from backend.app.data_sources.weather_source import WeatherSource

logger = logging.getLogger(__name__)


class NasaPowerWeatherSource(WeatherSource):
    """Weather source backed by NASA POWER API (with JSON caching).

    When use_real=True:
        Delegates to WeatherService.get_weather_provider() which fetches
        from https://power.larc.nasa.gov/api/ and caches to
        .agritwin_cache/weather/.

    When use_real=False:
        Returns a SyntheticWeatherProvider (deterministic, offline).
        Used in all unit/integration tests where real API calls are undesirable.

    Args:
        use_real:     If True, use NASA POWER API.  If False, use synthetic.
        cache_dir:    Optional override for the JSON cache directory.
                      Passed to WeatherService if use_real=True.
    """

    def __init__(
        self,
        use_real: bool = False,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._use_real = use_real
        self._cache_dir = cache_dir
        # Lazy-initialise WeatherService only when needed (avoids import cost
        # in test code that uses use_real=False).
        self._weather_service = None

    def _get_weather_service(self):
        """Lazy-load WeatherService to avoid import overhead in test paths."""
        if self._weather_service is None:
            from backend.app.services.weather_service import WeatherService
            self._weather_service = WeatherService(
                cache_dir=self._cache_dir
            ) if self._cache_dir else WeatherService()
        return self._weather_service

    def get_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: datetime.date,
        end_date: datetime.date,
        *,
        elevation: float = 10.0,
    ) -> object:
        """Return a PCSE WeatherDataProvider for the given location and period.

        Delegates entirely to existing services — no scientific logic here.

        Args:
            latitude:   Site latitude [-90, 90].
            longitude:  Site longitude [-180, 180].
            start_date: First date of required weather (inclusive).
            end_date:   Last date of required weather (inclusive).
            elevation:  Site elevation [m] (used only by SyntheticWeatherProvider).

        Returns:
            pcse.base.WeatherDataProvider ready for the WOFOST engine.
        """
        if self._use_real:
            logger.info(
                "NasaPowerWeatherSource: fetching NASA POWER for (%.4f, %.4f) "
                "%s → %s", latitude, longitude, start_date, end_date,
            )
            svc = self._get_weather_service()
            return svc.get_weather_provider(latitude, longitude, start_date, end_date)
        else:
            from backend.app.simulation.weather_provider import SyntheticWeatherProvider
            logger.info(
                "NasaPowerWeatherSource: using synthetic weather for (%.4f, %.4f)",
                latitude, longitude,
            )
            return SyntheticWeatherProvider(
                latitude=latitude,
                longitude=longitude,
                elevation=elevation,
                start_year=start_date.year,
                end_year=end_date.year,
            )

    def get_source_name(self) -> str:
        return "NASA POWER" if self._use_real else "Synthetic"
