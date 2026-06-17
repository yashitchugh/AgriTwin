"""
backend/app/assimilation/__init__.py
=====================================

Assimilation Package — Observation Framework for AgriTwin Digital Twin
=======================================================================

This package contains all code for ingesting, storing, and querying field
observations from heterogeneous sources.  It is the data foundation on which
the future Ensemble Kalman Filter (EnKF) assimilation engine will be built.

Package layout:
    models/
        observation.py         — ORM: a single field measurement (any source)
        observation_batch.py   — ORM: grouped upload (satellite scene, sensor dump)
    repositories/
        observation_repository.py — Data access layer (SA 2.0, repository pattern)
    schemas/
        observation.py         — Pydantic v2 request/response schemas
    api/
        observation_routes.py  — FastAPI router (POST/GET /observations)

Supported observation sources (ObservationSource enum):
    SATELLITE   — Sentinel-2, MODIS, Landsat (LAI, NDVI products)
    SENSOR      — Soil moisture sensors, lysimeters
    WEATHER     — On-farm weather stations (temperature, humidity, rainfall)
    MANUAL      — Field scout measurements (canopy height, phenology)
    MODEL       — Model-derived pseudo-observations (synthetic testing)

NOT implemented here:
    - EnKF analysis step (Module 2)
    - Ensemble spreading
    - Observation operator H matrix
    - State update via wofost.set_variable()
    - Sentinel-2 image download / GEE integration
"""
