from pydantic import BaseModel
from datetime import date, datetime
from typing import List, Optional, Literal
from uuid import UUID

class WindowGenerationRequest(BaseModel):
    """Request body for generating ML windows."""
    simulation_id: UUID
    field_id: UUID
    window_size: int = 7  # Days in each window (7, 14, 30)
    stride: int = 1       # How many days to slide the window (1 = daily sliding)
    normalize: bool = True
    output_table_name: Optional[str] = "windowed_training_data"

class WindowGenerationResponse(BaseModel):
    """Response body for window generation."""
    simulation_id: UUID
    total_windows_generated: int
    features_used: List[str]
    normalization_scalers: dict  # Store min/max for each feature
    start_date: date
    end_date: date
    message: str

class WindowedTrainingDatum(BaseModel):
    """Single window record to be stored in DB."""
    simulation_id: UUID
    field_id: UUID
    window_start_date: date
    window_end_date: date
    target_date: date  # The date we're trying to predict (last day of window)
    
    # The feature vector (flattened list of normalized values)
    feature_vector: List[float]
    
    # The target variable (e.g., actual LAI at target_date, or residual)
    target_lai: float
    target_residual: float  # satellite - wofost at target_date
    
    # Context metadata
    weather_features: dict  # temp, rain, radiation for the window
    soil_features: dict     # mean + uncertainty