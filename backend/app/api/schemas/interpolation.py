from pydantic import BaseModel
from datetime import date
from typing import List, Optional, Literal

class InterpolationRequest(BaseModel):
    """Request body for interpolation."""
    observation_dates: List[date]          # Dates when satellite data is available
    observation_values: List[float]        # LAI or SM values at those dates
    target_dates: List[date]               # All daily dates you want to fill (e.g., whole season)
    method: Literal["linear", "cubic_spline", "savgol"] = "cubic_spline"
    
    # The "Cloud-Gap Trigger" we discussed!
    max_allowed_gap_days: int = 10         # If gap > 10 days, we flag it rather than interpolate

class InterpolationResponse(BaseModel):
    """Response body for interpolation."""
    interpolated_dates: List[date]
    interpolated_values: List[Optional[float]]
    quality_flags: List[dict]              # e.g., {"date": "2020-07-15", "is_interpolated": True, "gap_risk": "high"}
    method_used: str
    message: Optional[str] = None
