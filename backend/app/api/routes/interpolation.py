from fastapi import APIRouter, HTTPException, status
from backend.app.api.schemas.interpolation import InterpolationRequest, InterpolationResponse
from backend.app.services.temporal_interpolation_service import TemporalInterpolationService

router = APIRouter()

@router.post("/fill-gaps", response_model=InterpolationResponse, status_code=status.HTTP_200_OK)
async def fill_gaps(request: InterpolationRequest):
    """
    Fills the temporal gaps between satellite observations.
    
    - Uses Linear, Cubic Spline, or Savitzky-Golay.
    - Triggers a warning if any gap exceeds max_allowed_gap_days (default 10).
    """
    try:
        service = TemporalInterpolationService()
        result = service.interpolate(request)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Interpolation failed: {str(e)}")