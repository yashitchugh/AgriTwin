"""
core/exceptions.py — Custom Exception Hierarchy
================================================

Defines domain-specific exceptions for the AgriTwin backend.

Why custom exceptions?
  - They carry semantic meaning: a CalibrationError is different from a
    WeatherFetchError, even if both ultimately become HTTP 502 responses.
  - Route handlers can catch specific types and return appropriate HTTP codes,
    rather than catching bare Exception and guessing.
  - EnKF service (Phase 3) will raise AssimilationError instead of generic
    RuntimeError, making the error boundary clear.
  - Logging is more searchable: grep for "WeatherFetchError" finds all
    weather-related failures across the codebase.

Architecture convention:
  - Services RAISE these exceptions.
  - Routes CATCH them and convert to HTTPException.
  - Never put HTTPException inside service code (keeps services HTTP-agnostic).
"""


# ── Base ─────────────────────────────────────────────────────────────────────

class AgriTwinError(Exception):
    """Base class for all AgriTwin domain errors.

    All service-level exceptions inherit from this, making it possible
    to catch any AgriTwin error in a single except clause if needed.
    """


# ── Simulation errors ─────────────────────────────────────────────────────────

class SimulationError(AgriTwinError):
    """WOFOST / PCSE simulation engine error.

    Raised when the simulation engine itself fails — e.g. parameter
    combinations that cause numerical instability, or a PCSE internal crash.
    Maps to HTTP 500.
    """


class InvalidCropError(AgriTwinError):
    """Crop name or variety not found in PCSE parameter database.

    Raised by crop_provider when set_active_crop() fails because the
    crop_name or variety_name doesn't exist in the YAML database.
    Maps to HTTP 400 (bad request — user specified invalid crop config).
    """


class InvalidParameterError(AgriTwinError):
    """Invalid physical parameter combination.

    Raised when parameters violate physical constraints, e.g.:
      - SMW >= SMFCF >= SM0 (soil moisture ordering)
      - harvest_date <= sowing_date
      - latitude outside -90..90
    Maps to HTTP 422 (unprocessable entity).
    """


# ── Weather errors ────────────────────────────────────────────────────────────

class WeatherFetchError(AgriTwinError):
    """Failed to fetch weather data from NASA POWER API.

    Raised when the API is unreachable, returns an error, or the response
    cannot be parsed. The simulation service catches this and falls back
    to synthetic weather if configured.
    Maps to HTTP 502 (bad gateway — upstream dependency failed).
    """


class WeatherDataUnavailableError(AgriTwinError):
    """Weather data not available for the requested period.

    Raised when NASA POWER API returns no data for the specified
    location/date range (e.g. ocean coordinates, future dates).
    Maps to HTTP 422.
    """


# ── Soil errors ───────────────────────────────────────────────────────────────

class SoilFetchError(AgriTwinError):
    """Failed to fetch soil data from SoilGrids API.

    The simulation service catches this and falls back to default
    medium-loam parameters if configured.
    Maps to HTTP 502.
    """


# ── Future: EnKF errors (Phase 3) ────────────────────────────────────────────

class AssimilationError(AgriTwinError):
    """EnKF data assimilation failed.

    Will be raised by the assimilation service when the ensemble Kalman
    Filter update step fails — e.g. singular covariance matrix, observation
    outside physically possible range.
    Maps to HTTP 500.
    """
    # Placeholder — not used until EnKF is implemented.
