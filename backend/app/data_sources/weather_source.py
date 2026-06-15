"""
backend/app/data_sources/weather_source.py — Abstract Weather Source
======================================================================

Defines the WeatherSource interface that all weather data backends must
implement.  The simulation engine and any future digital twin component
should program against this interface, never against a concrete class.

Design principles:
  1. Protocol-first: concrete implementations inherit WeatherSource and
     override get_weather().  Python ABCs enforce this at class definition time.
  2. Return type is pcse.base.WeatherDataProvider — the standard PCSE
     provider contract, fully compatible with Wofost72_WLP_FD.
  3. Implementors are responsible for all caching, retrying, and unit
     conversion.  The caller (engine / service) gets a ready-to-use provider.

Current implementations:
  NasaPowerWeatherSource — wraps WeatherService + SyntheticWeatherProvider
                           (see backend/app/data_sources/nasa_power_source.py)

Planned future implementations (NOT implemented here):
  ERA5WeatherSource         — ECMWF ERA5 reanalysis via CDS API
  OpenMeteoWeatherSource    — Open-Meteo free forecast API
  LocalCSVWeatherSource     — historical weather from local CSV files
"""

import datetime
from abc import ABC, abstractmethod
from typing import Optional


class WeatherSource(ABC):
    """Abstract base class for weather data sources.

    All concrete weather providers must subclass this and implement
    get_weather().  This contract ensures that the simulation engine,
    future EnKF assimilation modules, and the scenario engine can all
    swap weather backends without changing their own code.

    Usage pattern:
        class MyWeatherSource(WeatherSource):
            def get_weather(self, latitude, longitude, start_date, end_date, ...):
                ...  # fetch and return a pcse WeatherDataProvider

        # In the simulation engine:
        source: WeatherSource = MyWeatherSource()
        provider = source.get_weather(lat, lon, start, end)
        wofost = Wofost72_WLP_FD(params, provider, agro)
    """

    @abstractmethod
    def get_weather(
        self,
        latitude: float,
        longitude: float,
        start_date: datetime.date,
        end_date: datetime.date,
        *,
        elevation: float = 10.0,
    ) -> object:
        """Fetch weather data for a location and time range.

        Args:
            latitude:   Site latitude in decimal degrees (WGS84, -90 to 90).
            longitude:  Site longitude in decimal degrees (WGS84, -180 to 180).
            start_date: First date to include (inclusive).
            end_date:   Last date to include (inclusive). Must be >= start_date.
            elevation:  Site elevation above sea level [m].
                        Used by synthetic and Penman-Monteith-based providers.
                        Optional for providers that supply their own elevation.

        Returns:
            A pcse.base.WeatherDataProvider instance ready to pass directly
            to Wofost72_WLP_FD(params, provider, agro).  The caller does not
            need to know which backend produced the provider.

        Raises:
            WeatherFetchError: If the data cannot be retrieved and no fallback
                is available.  Implementors should attempt a graceful fallback
                (synthetic weather) before raising.
        """
        ...

    def get_source_name(self) -> str:
        """Human-readable name of this weather backend.

        Used in logging, the weather_snapshot JSON column, and health-check
        API responses.  Subclasses should override with a descriptive string.

        Returns:
            Source name string, e.g. "NASA POWER" or "Synthetic".
        """
        return self.__class__.__name__
