# backend/app/satellite/api/routes.py

import datetime
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.assimilation.repositories.observation_repository import ObservationRepository
from backend.app.satellite.providers.sentinel2_provider import StubSentinel2Provider
from backend.app.satellite.processors.lai_estimator import EmpiricalLAIEstimator
from backend.app.satellite.services.lai_observation_service import LAIObservationService
from backend.app.satellite.schemas.satellite_scene import SatelliteLAIResponse

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get(
    "/lai",
    response_model=list[SatelliteLAIResponse],
    summary="Retrieve and ingest Sentinel-2 LAI observations",
    description=(
        "Retrieves Sentinel-2 satellite scenes for a given field and date range, "
        "filters them for cloud cover, computes vegetation indices (NDVI/OSAVI/SeLI), "
        "estimates Leaf Area Index (LAI) using empirical equations, "
        "and persists the observations into the database. "
        "Updates existing observation records instead of creating duplicate timestamps."
    ),
    tags=["Satellite"],
)
def get_satellite_lai(
    field_id: uuid.UUID = Query(..., description="UUID of the target field"),
    start_date: datetime.date = Query(..., description="Start date of the observation window (YYYY-MM-DD)"),
    end_date: datetime.date = Query(..., description="End date of the observation window (YYYY-MM-DD)"),
    index_name: str = Query("NDVI", description="Vegetation index to use: 'NDVI', 'OSAVI', or 'SeLI'"),
    max_cloud_cover: float = Query(0.2, ge=0.0, le=1.0, description="Maximum allowable cloud cover fraction [0.0 - 1.0]"),
    uncertainty: float = Query(0.3, gt=0.0, description="Configurable 1-sigma uncertainty standard deviation"),
    db: Session = Depends(get_db),
) -> list[SatelliteLAIResponse]:
    """Retrieves and processes satellite scenes, persisting them as observations."""
    
    # Validate the vegetation index name
    valid_indices = {"NDVI", "OSAVI", "SELI"}
    if index_name.upper() not in valid_indices:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported vegetation index: '{index_name}'. Supported indices are: {', '.join(valid_indices)}"
        )

    # Instantiate repositories, providers, and estimators
    obs_repo = ObservationRepository(db)
    provider = StubSentinel2Provider()
    estimator = EmpiricalLAIEstimator()
    
    service = LAIObservationService(
        obs_repo=obs_repo,
        provider=provider,
        estimator=estimator,
    )
    
    try:
        processed_scenes = service.ingest_lai_observations(
            field_id=field_id,
            start_date=start_date,
            end_date=end_date,
            index_name=index_name,
            max_cloud_cover=max_cloud_cover,
            uncertainty=uncertainty,
        )
    except ValueError as e:
        # Map validation errors (e.g. field not found or empty geometry) to 400 Bad Request
        logger.warning("Satellite LAI ingestion failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Internal error during Satellite LAI ingestion: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal processing error in satellite pipeline.")

    # Convert processed scenes to the response schemas
    response: list[SatelliteLAIResponse] = []
    for scene in processed_scenes:
        quality_score = int((1.0 - scene.cloud_cover) * 100)
        response.append(
            SatelliteLAIResponse(
                acquisition_date=scene.acquisition_date,
                cloud_cover=scene.cloud_cover,
                ndvi=scene.ndvi,
                osavi=scene.osavi,
                seli=scene.seli,
                estimated_lai=scene.estimated_lai,
                quality_score=quality_score,
                metadata=scene.metadata,
            )
        )
        
    return response
