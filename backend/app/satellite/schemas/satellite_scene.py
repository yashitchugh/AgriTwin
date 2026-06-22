# backend/app/satellite/schemas/satellite_scene.py

import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

class SatelliteScene(BaseModel):
    """Internal model representing a Sentinel-2 scene.
    
    Contains acquisition date, cloud cover, raw reflectances (optional), 
    computed indices (optional), and custom metadata.
    """
    acquisition_date: datetime.date = Field(
        ..., 
        description="Date when the satellite scene was acquired."
    )
    cloud_cover: float = Field(
        ..., 
        ge=0.0, 
        le=1.0, 
        description="Cloud cover fraction [0.0 - 1.0]."
    )
    red: Optional[float] = Field(
        None, 
        ge=0.0, 
        le=1.0, 
        description="Red band reflectance."
    )
    nir: Optional[float] = Field(
        None, 
        ge=0.0, 
        le=1.0, 
        description="Near-infrared (NIR) band reflectance."
    )
    red_edge: Optional[float] = Field(
        None, 
        ge=0.0, 
        le=1.0, 
        description="Red-edge band reflectance."
    )
    ndvi: Optional[float] = Field(
        None, 
        description="Normalized Difference Vegetation Index."
    )
    osavi: Optional[float] = Field(
        None, 
        description="Optimized Soil Adjusted Vegetation Index."
    )
    seli: Optional[float] = Field(
        None, 
        description="Sentinel-2 LAIgreen Index."
    )
    estimated_lai: Optional[float] = Field(
        None,
        description="Estimated Leaf Area Index."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, 
        description="Arbitrary metadata dictionary from the provider."
    )

class SatelliteLAIResponse(BaseModel):
    """API response schema for /satellite/lai."""
    acquisition_date: datetime.date = Field(
        ..., 
        description="Acquisition date of the scene."
    )
    cloud_cover: float = Field(
        ..., 
        description="Cloud cover fraction [0.0 - 1.0]."
    )
    ndvi: Optional[float] = Field(
        None, 
        description="Computed NDVI value."
    )
    osavi: Optional[float] = Field(
        None, 
        description="Computed OSAVI value."
    )
    seli: Optional[float] = Field(
        None, 
        description="Computed SeLI value."
    )
    estimated_lai: Optional[float] = Field(
        None, 
        description="Estimated Leaf Area Index."
    )
    quality_score: int = Field(
        ..., 
        description="Quality score based on cloud cover [0 - 100]."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, 
        description="Scene metadata details."
    )
