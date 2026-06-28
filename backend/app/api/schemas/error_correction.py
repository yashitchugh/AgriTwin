from pydantic import BaseModel
from datetime import date, datetime
from typing import List, Optional, Literal
from uuid import UUID

class ErrorCorrectionRequest(BaseModel):
    """Request body for 7-day window error correction."""
    simulation_id: UUID
    field_id: UUID
    window_start_date: date
    window_end_date: date  # Should be 7 days after start
    residual_threshold: float = 0.5  # LAI threshold for flagging anomalies
    
class ErrorCorrectionResponse(BaseModel):
    """Response body for error correction."""
    simulation_id: UUID
    window_start: date
    window_end: date
    total_days_processed: int
    anomalies_detected: int
    anomalies_corrected: int
    correction_summary: List[dict]  # Details per day
    message: str

class DailyCorrectionRecord(BaseModel):
    """Individual day correction record."""
    date: date
    variable: str  # "LAI", "SM", etc.
    wofost_value: float
    satellite_value: float  # From interpolation
    residual: float
    was_anomaly: bool
    correction_applied: float
    corrected_value: float
    blending_weight: float  # The scalar Kalman Gain used