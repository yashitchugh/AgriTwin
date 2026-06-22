# backend/app/satellite/providers/sentinel2_provider.py

import datetime
from abc import ABC, abstractmethod
from typing import Optional
from backend.app.satellite.schemas.satellite_scene import SatelliteScene

class Sentinel2Provider(ABC):
    """Abstract interface for Sentinel-2 satellite data provider."""

    @abstractmethod
    def get_scenes(
        self,
        boundary_geojson: dict,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> list[SatelliteScene]:
        """Retrieve Sentinel-2 scenes for a given field boundary and date range.
        
        Args:
            boundary_geojson: GeoJSON dictionary of the field boundary.
            start_date: Start date of the query window.
            end_date: End date of the query window.
            
        Returns:
            List of SatelliteScene objects.
        """
        pass

class StubSentinel2Provider(Sentinel2Provider):
    """Stub implementation of Sentinel2Provider that generates deterministic synthetic scenes."""

    def get_scenes(
        self,
        boundary_geojson: dict,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> list[SatelliteScene]:
        """Generate synthetic Sentinel-2 scenes every 5 days.
        
        Bands (red, nir, red_edge) are modeled to simulate realistic crop green-up.
        Cloud cover is cycled to include both clear and cloudy observations.
        """
        # Handle edge case: empty field geometry
        if not boundary_geojson or not isinstance(boundary_geojson, dict):
            raise ValueError("Field boundary geometry is empty or invalid.")
        
        if "coordinates" not in boundary_geojson and ("geometry" not in boundary_geojson or "coordinates" not in boundary_geojson.get("geometry", {})):
            raise ValueError("Field boundary geometry is empty or invalid.")

        if start_date > end_date:
            return []

        scenes: list[SatelliteScene] = []
        current_date = start_date
        total_days = max((end_date - start_date).days, 1)
        scene_count = 0

        while current_date <= end_date:
            # Deterministic progression factor t in [0.0, 1.0]
            t = (current_date - start_date).days / total_days
            
            # Simulate a realistic green-up curve: 
            # Red reflectance decreases (chlorophyll absorption)
            # NIR reflectance increases (leaf structure scattering)
            # Red-edge reflectance increases/stabilizes
            red = max(0.15 - 0.10 * t, 0.01)
            nir = min(0.20 + 0.50 * t, 0.90)
            red_edge = min(0.12 + 0.35 * t, 0.80)

            # Cycle cloud cover: 0.05 (clear), 0.15 (partly cloudy), 0.60 (heavy cloud - skip candidate)
            cloud_cycle = scene_count % 3
            if cloud_cycle == 0:
                cloud_cover = 0.05
            elif cloud_cycle == 1:
                cloud_cover = 0.15
            else:
                cloud_cover = 0.60

            # Unique scene ID for metadata
            scene_id = f"S2A_MSIL2A_{current_date.strftime('%Y%m%d')}_T43RGP"

            scene = SatelliteScene(
                acquisition_date=current_date,
                cloud_cover=cloud_cover,
                red=red,
                nir=nir,
                red_edge=red_edge,
                ndvi=None,   # To be computed by processing pipeline
                osavi=None,  # To be computed by processing pipeline
                seli=None,   # To be computed by processing pipeline
                metadata={
                    "scene_id": scene_id,
                    "sensor": "Sentinel-2A MSI",
                    "processing_baseline": "05.09",
                    "sun_elevation": 45.2,
                }
            )
            scenes.append(scene)
            
            # Sentinel-2 nominal revisit is 5 days
            current_date += datetime.timedelta(days=5)
            scene_count += 1

        return scenes
