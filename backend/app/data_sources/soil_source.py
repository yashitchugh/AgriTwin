"""
backend/app/data_sources/soil_source.py — Abstract Soil Source
================================================================

Defines the SoilSource interface that all soil data backends must implement.
The simulation service programs against this interface; concrete providers
(SoilGrids, pedotransfer functions, lab data CSVs) are interchangeable.

Current implementations:
  SoilGridsSource — wraps SoilService (SoilGrids v2.0 REST API with caching)
                    (see backend/app/data_sources/soilgrids_source.py)

Planned future implementations (NOT implemented here):
  PedotransferSource        — derive hydraulic parameters from texture/bulk density
  LocalSoilCSVSource        — read soil params from a pre-measured CSV file
  IsricWorldSoilSource      — ISRIC World Soil Information (alternative endpoint)
"""

from abc import ABC, abstractmethod
from typing import Optional


class SoilSource(ABC):
    """Abstract base class for soil parameter providers.

    All concrete soil backends must subclass this and implement get_soil().
    The simulation engine (via create_soil_params()) must accept a SoilSource
    instead of calling SoilService directly, enabling future backends without
    any engine changes.

    Returned soil dict keys (WOFOST-compatible, all required):
        SMFCF   — Volumetric water content at field capacity [cm³/cm³]
        SMW     — Volumetric water content at wilting point [cm³/cm³]
        SM0     — Volumetric water content at saturation [cm³/cm³]
        CRAIRC  — Critical air content [cm³/cm³]
        RDMSOL  — Maximum rootable soil depth [cm]
        K0      — Hydraulic conductivity at saturation [cm/day]
        SOPE    — Maximum percolation rate root zone [cm/day]
        KSUB    — Maximum percolation rate subsoil [cm/day]

    Physical constraint enforced by all implementations:
        SMW < SMFCF < SM0
    """

    @abstractmethod
    def get_soil(
        self,
        latitude: float,
        longitude: float,
        *,
        rdmsol: float = 120.0,
        force_update: bool = False,
    ) -> dict[str, float]:
        """Fetch soil hydraulic parameters for a location.

        Args:
            latitude:     Site latitude in decimal degrees (WGS84).
            longitude:    Site longitude in decimal degrees (WGS84).
            rdmsol:       Maximum rootable soil depth override [cm].
                          Most APIs do not provide this — a default of 120 cm
                          is used for most annual crops on deep soils.
            force_update: Bypass any cache and re-fetch from the source.

        Returns:
            Dict with WOFOST soil parameter keys (see class docstring).
            MUST satisfy SMW < SMFCF < SM0.  Implementations should validate
            and fix ordering before returning.

        Raises:
            SoilFetchError: If data cannot be fetched and no fallback is
                available.  Implementations should return default loam values
                rather than raising, to prevent simulation failure.
        """
        ...

    def get_source_name(self) -> str:
        """Human-readable name of this soil backend.

        Returns:
            Source name string, e.g. "SoilGrids v2.0" or "Default Loam".
        """
        return self.__class__.__name__
