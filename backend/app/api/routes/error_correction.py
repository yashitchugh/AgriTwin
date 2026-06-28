from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from backend.app.db.session import get_db
from backend.app.api.schemas.error_correction import ErrorCorrectionRequest, ErrorCorrectionResponse
from backend.app.services.error_correction_service import ErrorCorrectionService

router = APIRouter()

@router.post("/error-correction/correct-window", response_model=ErrorCorrectionResponse)
async def correct_window(
    request: ErrorCorrectionRequest,
    db: Session = Depends(get_db)
):
    """
    Corrects anomalies in a 7-day window of WOFOST outputs.
    
    - Compares WOFOST LAI against interpolated satellite LAI.
    - If residual > threshold, applies a blending correction (scalar Kalman Gain).
    - Updates the DailyOutput table with corrected values.
    """
    try:
        service = ErrorCorrectionService(db)
        result = service.correct_window(request)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error correction failed: {str(e)}"
        )