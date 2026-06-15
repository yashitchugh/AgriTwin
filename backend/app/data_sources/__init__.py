"""
backend/app/data_sources/__init__.py
=====================================

Data Source Interfaces — pluggable observation input layer.

This package defines the abstract contracts that all external data sources
must implement.  The simulation engine and the Twin layer consume these
interfaces, never concrete provider classes directly.

Current concrete implementations:
  WeatherSource  → NasaPowerWeatherSource   (simulation/weather_provider.py)
  SoilSource     → SoilGridsSource          (services/soil_service.py)

Stub interfaces (no implementation yet):
  SatelliteSource → reserved for Sentinel-2 / MODIS LAI products
  SensorSource    → reserved for field IoT (soil moisture, rain gauge)

NOT implemented here:
  - Satellite pipelines, image download, NDVI/LAI retrieval
  - IoT message brokers, MQTT, sensor time series
  - EnKF, assimilation, state estimation
  - Machine learning, prediction models
  - Optimization, recommendation engines
"""

from backend.app.data_sources.weather_source import WeatherSource
from backend.app.data_sources.soil_source import SoilSource
from backend.app.data_sources.satellite_source import SatelliteSource
from backend.app.data_sources.sensor_source import SensorSource

__all__ = [
    "WeatherSource",
    "SoilSource",
    "SatelliteSource",
    "SensorSource",
]
