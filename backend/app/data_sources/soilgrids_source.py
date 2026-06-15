"""
backend/app/data_sources/soilgrids_source.py — SoilGridsSource
================================================================

Concrete implementation of SoilSource that wraps the existing SoilService
(SoilGrids v2.0 REST API with JSON file caching).

NO business logic changes — this is a pure adapter.  All scientific
calculations, depth-averaging, unit conversions, validation, and caching
remain in:
  backend/app/services/soil_service.py

Architecture:
  SoilSource (ABC)
      └── SoilGridsSource   ← this class
              └── delegates to SoilService.get_soil_params(lat, lon, rdmsol)

Usage:
    source = SoilGridsSource()
    soil_params = source.get_soil(28.6, 77.2)
    # Returns dict: {SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB}
"""

import logging
from typing import Optional

from backend.app.data_sources.soil_source import SoilSource

logger = logging.getLogger(__name__)


class SoilGridsSource(SoilSource):
    """Soil source backed by ISRIC SoilGrids v2.0 REST API.

    Delegates all fetching, depth-averaging, unit conversion, and
    validation to SoilService.  This class is responsible only for
    adapting the SoilService API to the SoilSource interface contract.

    Args:
        cache_dir: Optional path for the JSON soil cache.
                   Passed through to SoilService.__init__().
                   Defaults to .agritwin_cache/soil/ relative to the project root.
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir
        self._soil_service = None

    def _get_soil_service(self):
        """Lazy-load SoilService to decouple import from class instantiation."""
        if self._soil_service is None:
            from backend.app.services.soil_service import SoilService
            self._soil_service = (
                SoilService(cache_dir=self._cache_dir)
                if self._cache_dir
                else SoilService()
            )
        return self._soil_service

    def get_soil(
        self,
        latitude: float,
        longitude: float,
        *,
        rdmsol: float = 120.0,
        force_update: bool = False,
    ) -> dict[str, float]:
        """Fetch WOFOST soil parameters from SoilGrids v2.0.

        Delegates entirely to SoilService.get_soil_params().  On failure,
        SoilService already returns safe default loam values — this method
        never raises for valid coordinates.

        Args:
            latitude:     Site latitude [-90, 90].
            longitude:    Site longitude [-180, 180].
            rdmsol:       Maximum rootable soil depth [cm] (default 120 cm).
            force_update: Bypass cache and re-fetch from API.

        Returns:
            Dict with WOFOST soil parameter keys:
                {SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB}
            Guaranteed to satisfy SMW < SMFCF < SM0.
        """
        logger.info(
            "SoilGridsSource: fetching soil for (%.4f, %.4f)", latitude, longitude
        )
        svc = self._get_soil_service()
        return svc.get_soil_params(
            latitude=latitude,
            longitude=longitude,
            rdmsol=rdmsol,
            force_update=force_update,
        )

    def get_source_name(self) -> str:
        return "SoilGrids v2.0"
