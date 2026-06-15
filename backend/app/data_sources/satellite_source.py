"""
backend/app/data_sources/satellite_source.py — Abstract Satellite Source
=========================================================================

Defines the SatelliteSource interface for future satellite observation inputs.
This is a STUB — no satellite pipeline is implemented here.

Purpose:
  Establish the contract now so that when Sentinel-2 / MODIS / Landsat
  integration is implemented, the rest of the codebase (FieldState, EnKF,
  assimilation service) already knows the shape of satellite data.

NOT implemented here:
  - Sentinel-2 API calls or image download
  - NDVI / LAI retrieval or processing
  - Atmospheric correction
  - EnKF observation operators
  - Machine learning-based retrieval algorithms

When to implement:
  Implement a concrete SatelliteSource subclass when the satellite ingestion
  pipeline (Phase 4 / Objective 4) is added.  The interface below defines
  exactly what that implementation must return.

Example future implementation:
    class Sentinel2Source(SatelliteSource):
        def get_observations(self, field_id, start_date, end_date, variables):
            # Call Copernicus Data Space Ecosystem API
            # Return list of ObservationRecord
            ...
"""

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SatelliteObservation:
    """A single satellite-derived observation for one field on one date.

    All fields are optional because satellite retrievals may be cloudy,
    saturated, or out of bounds on any given date.

    Attributes:
        date:        Acquisition date of the observation.
        variable:    WOFOST-compatible variable name the observation targets
                     (e.g. 'LAI', 'TAGP', 'SM').
        value:       Retrieved value in WOFOST units (m²/m² for LAI, etc.)
        uncertainty: 1-sigma standard deviation of the retrieved value.
                     Required for EnKF observation noise covariance (R matrix).
        source:      Source product identifier (e.g. 'Sentinel-2 L2A 10m').
        cloud_cover: Cloud fraction over the field polygon [0–1].
                     Observations with cloud_cover > 0.2 should be discarded.
        field_id:    UUID of the Field this observation belongs to.
    """
    date: datetime.date
    variable: str                              # 'LAI', 'TAGP', 'SM', ...
    value: Optional[float] = None
    uncertainty: Optional[float] = None        # 1-sigma [same unit as value]
    source: str = "unknown"                    # product / sensor identifier
    cloud_cover: Optional[float] = None        # [0–1]
    field_id: Optional[object] = None         # uuid.UUID when populated


class SatelliteSource(ABC):
    """Abstract base class for satellite remote sensing observation sources.

    All concrete satellite backends must subclass this and implement
    get_observations().  The EnKF assimilation module will consume a
    SatelliteSource, not a concrete Sentinel2 or MODIS class.

    This enables:
      - Swapping between Sentinel-2 and MODIS without changing the assimilation code
      - Using synthetic/test observations in unit tests
      - Plugging in ML-based retrieval algorithms as alternative sources

    Current implementations:
        None — this is a stub for future implementation.

    Future implementations:
        Sentinel2Source       — Copernicus Data Space Ecosystem API
        ModisSource           — NASA LAADS DAAC MODIS LAI product
        SyntheticSatSource    — Deterministic synthetic observations for testing
    """

    @abstractmethod
    def get_observations(
        self,
        latitude: float,
        longitude: float,
        start_date: datetime.date,
        end_date: datetime.date,
        *,
        variables: Optional[list[str]] = None,
        boundary_geojson: Optional[dict] = None,
    ) -> list[SatelliteObservation]:
        """Retrieve satellite observations for a location and time window.

        Args:
            latitude:         Field centroid latitude [decimal degrees WGS84].
            longitude:        Field centroid longitude [decimal degrees WGS84].
            start_date:       Start of the observation window (inclusive).
            end_date:         End of the observation window (inclusive).
            variables:        List of WOFOST-variable names to retrieve
                              (e.g. ['LAI', 'SM']).  None = all available.
            boundary_geojson: Optional GeoJSON polygon of the field boundary.
                              When provided, the observation is spatially
                              averaged within the polygon rather than at the
                              centroid point.  This is the boundary_geojson
                              column from the Field model.

        Returns:
            List of SatelliteObservation records, one per (date, variable)
            combination that has valid retrievals.  May be empty if the
            window is fully cloudy or outside product coverage.
        """
        ...

    def get_source_name(self) -> str:
        """Human-readable source name (e.g. 'Sentinel-2 L2A 10m')."""
        return self.__class__.__name__
