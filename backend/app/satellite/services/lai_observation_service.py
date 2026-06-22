# backend/app/satellite/services/lai_observation_service.py

import datetime
import logging
import math
import uuid
from typing import Optional

from sqlalchemy import select

from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus
from backend.app.assimilation.models.observation_batch import ObservationBatch, BatchProcessingStatus
from backend.app.assimilation.repositories.observation_repository import ObservationRepository
from backend.app.models.field import Field
from backend.app.satellite.providers.sentinel2_provider import Sentinel2Provider
from backend.app.satellite.processors.lai_estimator import LAIEstimator
from backend.app.satellite.processors.vegetation_indices import compute_ndvi, compute_osavi, compute_seli
from backend.app.satellite.schemas.satellite_scene import SatelliteScene

logger = logging.getLogger(__name__)

class LAIObservationService:
    """Orchestrates the Leaf Area Index (LAI) satellite observation pipeline.
    
    Responsible for fetching Sentinel-2 scenes, filtering cloudy scenes,
    computing vegetation indices, estimating LAI, and persisting observations in a batch.
    """

    def __init__(
        self,
        obs_repo: ObservationRepository,
        provider: Sentinel2Provider,
        estimator: LAIEstimator,
    ) -> None:
        self.obs_repo = obs_repo
        self.provider = provider
        self.estimator = estimator

    def ingest_lai_observations(
        self,
        field_id: uuid.UUID,
        start_date: datetime.date,
        end_date: datetime.date,
        *,
        index_name: str = "NDVI",
        max_cloud_cover: float = 0.2,
        uncertainty: float = 0.3,
    ) -> list[SatelliteScene]:
        """Fetch and process Sentinel-2 scenes, creating or updating LAI observations.
        
        Args:
            field_id: UUID of the target Field.
            start_date: Start date of the ingestion window.
            end_date: End date of the ingestion window.
            index_name: Vegetation index to use for estimation ('NDVI', 'OSAVI', 'SeLI').
            max_cloud_cover: Max allowed cloud cover fraction [0.0 - 1.0].
            uncertainty: Custom uncertainty standard deviation for the observations.
            
        Returns:
            List of processed SatelliteScene objects (with computed VIs).
        """
        if field_id is None:
            raise ValueError("Field ID must be specified.")

        # 1. Fetch and validate Field
        field = self.obs_repo.db.get(Field, field_id)
        if field is None:
            raise ValueError(f"Field {field_id} not found.")

        if not field.boundary_geojson or not isinstance(field.boundary_geojson, dict):
            raise ValueError("Field boundary geometry is empty or invalid.")

        # 2. Retrieve scenes from Sentinel-2 provider
        scenes = self.provider.get_scenes(
            boundary_geojson=field.boundary_geojson,
            start_date=start_date,
            end_date=end_date,
        )

        if not scenes:
            logger.info("No scenes found for field=%s between %s and %s", field_id, start_date, end_date)
            return []

        # Find temporal boundaries for the batch
        timestamps = [
            datetime.datetime.combine(s.acquisition_date, datetime.time(12, 0, 0), tzinfo=datetime.timezone.utc)
            for s in scenes
        ]
        start_time = min(timestamps)
        end_time = max(timestamps)

        # 3. Create the ObservationBatch record
        batch = ObservationBatch(
            id=uuid.uuid4(),
            field_id=field_id,
            source="SATELLITE",
            provider_name="Sentinel2",
            start_time=start_time,
            end_time=end_time,
            number_of_observations=0,
            processing_status=BatchProcessingStatus.PENDING,
        )
        self.obs_repo.save_batch(batch)

        saved_count = 0
        processed_scenes: list[SatelliteScene] = []

        for scene in scenes:
            # Skip scenes with excessive cloud cover
            if scene.cloud_cover > max_cloud_cover:
                logger.debug("Skipping scene %s: cloud cover %.2f > threshold %.2f", 
                             scene.metadata.get("scene_id"), scene.cloud_cover, max_cloud_cover)
                continue

            # Compute indices
            if scene.red is not None and scene.nir is not None:
                scene.ndvi = compute_ndvi(scene.red, scene.nir)
                scene.osavi = compute_osavi(scene.red, scene.nir)
            if scene.red_edge is not None and scene.nir is not None:
                scene.seli = compute_seli(scene.red_edge, scene.nir)

            # Determine vegetation index value to use for estimation
            index_value = None
            name_upper = index_name.upper()
            if name_upper == "NDVI":
                index_value = scene.ndvi
            elif name_upper == "OSAVI":
                index_value = scene.osavi
            elif name_upper == "SELI":
                index_value = scene.seli
            else:
                raise ValueError(f"Unsupported vegetation index: '{index_name}'")

            if index_value is None or math.isnan(index_value):
                logger.warning("Vegetation index %s is NaN or missing for scene date %s. Skipping LAI estimation.",
                               index_name, scene.acquisition_date)
                continue

            # Estimate LAI
            estimated_lai = self.estimator.estimate_lai(index_value, index_name)
            scene.estimated_lai = estimated_lai
            if math.isnan(estimated_lai):
                logger.warning("Estimated LAI is NaN for scene date %s. Skipping observation persistence.",
                               scene.acquisition_date)
                continue

            # Create UTC timestamp for observation
            timestamp = datetime.datetime.combine(
                scene.acquisition_date, 
                datetime.time(12, 0, 0), 
                tzinfo=datetime.timezone.utc
            )

            # 4. Check for duplicates (field_id, timestamp, variable_name='LAI', source=SATELLITE)
            stmt = select(Observation).where(
                Observation.field_id == field_id,
                Observation.timestamp == timestamp,
                Observation.variable_name == "LAI",
                Observation.source == ObservationSource.SATELLITE
            )
            existing_obs = self.obs_repo.db.execute(stmt).scalars().first()

            quality_score = int((1.0 - scene.cloud_cover) * 100)

            if existing_obs is not None:
                # Update existing observation in-place (deduplication)
                existing_obs.value = estimated_lai
                existing_obs.uncertainty = uncertainty
                existing_obs.quality_score = quality_score
                existing_obs.cloud_cover = scene.cloud_cover
                existing_obs.provider_name = "Sentinel2"
                existing_obs.status = ObservationStatus.VALID
                existing_obs.batch_id = batch.id
                existing_obs.raw_payload = {
                    "scene_id": scene.metadata.get("scene_id"),
                    "index_name": index_name,
                    "index_value": index_value,
                    "ndvi": scene.ndvi,
                    "osavi": scene.osavi,
                    "seli": scene.seli,
                    "red": scene.red,
                    "nir": scene.nir,
                    "red_edge": scene.red_edge,
                    "updated_via_batch": str(batch.id),
                }
                logger.info("Updated existing LAI observation: ID=%s Date=%s Value=%.4f", 
                            existing_obs.id, scene.acquisition_date, estimated_lai)
            else:
                # Create a new observation
                obs = Observation(
                    id=uuid.uuid4(),
                    field_id=field_id,
                    timestamp=timestamp,
                    variable_name="LAI",
                    units="m2/m2",
                    value=estimated_lai,
                    uncertainty=uncertainty,
                    source=ObservationSource.SATELLITE,
                    provider_name="Sentinel2",
                    latitude=field.latitude,
                    longitude=field.longitude,
                    quality_score=quality_score,
                    cloud_cover=scene.cloud_cover,
                    status=ObservationStatus.VALID,
                    batch_id=batch.id,
                    raw_payload={
                        "scene_id": scene.metadata.get("scene_id"),
                        "index_name": index_name,
                        "index_value": index_value,
                        "ndvi": scene.ndvi,
                        "osavi": scene.osavi,
                        "seli": scene.seli,
                        "red": scene.red,
                        "nir": scene.nir,
                        "red_edge": scene.red_edge,
                    }
                )
                self.obs_repo.save_observation(obs)
                logger.info("Saved new LAI observation: ID=%s Date=%s Value=%.4f", 
                            obs.id, scene.acquisition_date, estimated_lai)

            saved_count += 1
            processed_scenes.append(scene)

        # 5. Complete batch record status update
        status = BatchProcessingStatus.SUCCESS if saved_count > 0 else BatchProcessingStatus.PARTIAL
        self.obs_repo.update_batch_status(
            batch.id,
            status=status,
            number_of_observations=saved_count
        )
        # Flush to DB to persist all changes in transaction
        self.obs_repo.db.flush()

        return processed_scenes
