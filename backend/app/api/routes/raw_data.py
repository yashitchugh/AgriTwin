"""
api/routes/raw_data.py — Raw Data Collection Endpoints
=======================================================

POST   /raw-data/collect/{field_id}    → Fetch 7-day weather window and store raw records

This endpoint triggers the 7-day sliding window data collection for ML cleaning.
It fetches weather from NASA POWER, validates it, and stores it in raw_weather_records
table for downstream Conv1D-LSTM processing.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter,Depends, HTTPException,Query
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.field import Field
from backend.app.services.raw_data_service import RawDataService

logger = logging.getLogger(__name__)
router = APIRouter()

#-----Response Schema-----
class ValidationResult(BaseModel):
    """Weather data validation result."""
    weather_valid:bool
    error_messages:list[str]=[]

class RawDataCollectionResponse(BaseModel):
    """Response from raw data collection endpoint."""
    field_id: uuid.UUID
    field_name: str
    latitude: float
    longitude: float
    records_stored: int
    validation: ValidationResult
    weather_days_fetched: int
    soil_data_fetched: bool

    class Config:
        json_schema_extra = {
            "example": {
                "field_id": "123e4567-e89b-12d3-a456-426614174000",
                "field_name": "North Field",
                "latitude": 28.6,
                "longitude": 77.2,
                "records_stored": 7,
                "validation": {
                    "weather_valid": True,
                    "error_messages": []
                },
                "weather_days_fetched": 7,
                "soil_data_fetched": True
            }
        }


#-------------------Endpoints--------------------------------------------------

@router.post(
    "collect/{field_id}",
    response_model = RawDataCollectionResponse,
    status_code=201,
    summary="Collect 7-day raw weather data for a field",
    description = (
        "Fetches the most recent 7-day weather window from NASA POWER API, "
        "validates the data for completeness and physical consistency, "
        "and stores raw records in the database for ML cleaning pipeline.\n\n"
        "**What this does:**\n"
        "1. Looks up the Field by ID to get latitude/longitude\n"
        "2. Fetches 7 days of weather data (ending yesterday by default)\n"
        "3. Validates: 7 days present, all variables complete, temp ordering correct\n"
        "4. Fetches static soil properties from SoilGrids\n"
        "5. Stores 7 RawWeatherRecord rows in database (if validation passes)\n\n"
        "**Use case:** Continuous data collection for ML-based weather cleaning"
    ),
    tags=["Raw Data Collection"],
)
def collect_raw_data(
    field_id:uuid.UUID,
    db:Session=Depends(get_db), 
    end_date:Optional[str]=Query(
        None,
        description="End date of 7-day window in YYYY-MM-DD format (defaults to yesterday)",
        examples="2024-12-31"
    ),
)-> RawDataCollectionResponse:
    """
    Collect 7-day raw weather data for the specified field.
    
    Args:
        field_id: UUID of the field to collect data for
        db: Database session (injected)
        end_date: Optional end date (defaults to yesterday)
        
    Returns:
        RawDataCollectionResponse with validation results and storage count
        
    Raises:
        HTTPException 404: Field not found
        HTTPException 500: Data collection or validation failed
    """

    #step1: Look up the field
    field = db.get(Field, field_id)
    if field is None:
        raise HTTPException(
            status_code=404,
            detail=f"Field {field_id} not found. Create the field first via POST /fields"
        )
    
    logger.info(
        "POST /raw-data/collect/%s → field=%r lat=%.4f lon=%.4f",
        field_id, field.name, field.latitude, field.longitude
    )
    
    #step2: Parse end_date if provided
    end_date_parsed = None
    if end_date:
        try:
            from datetime import date
            end_date_parsed=date.fromisoformat(end_date)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: {end_date}. Use YYYY-MM-DD format."
            ) from e 
        
     # Step 3: Initialize service and collect data
    try:
        service = RawDataService(timeout=60)
        result = service.fetch_and_store_window(
            field_id=field_id,
            latitude=field.latitude,
            longitude=field.longitude,
            db=db,
            end_date=end_date_parsed
        )
    except Exception as e:
        logger.exception("Raw data collection failed for field %s", field_id)
        raise HTTPException(
            status_code=500,
            detail=f"Data collection failed: {str(e)}"
        ) from e
    
    # Step 4: Build response
    validation_result = ValidationResult(
        weather_valid=result["validation"]["weather_valid"],
        error_messages=result["validation"]["error_messages"]
    )
    
    response = RawDataCollectionResponse(
        field_id=field_id,
        field_name=field.name,
        latitude=field.latitude,
        longitude=field.longitude,
        records_stored=result["records_stored"],
        validation=validation_result,
        weather_days_fetched=len(result["weather_data"]),
        soil_data_fetched=bool(result["soil_data"])
    )
    
    logger.info(
        "Raw data collection complete: field=%s, records_stored=%d, valid=%s",
        field_id, result["records_stored"], validation_result.weather_valid
    )
    
    return response
