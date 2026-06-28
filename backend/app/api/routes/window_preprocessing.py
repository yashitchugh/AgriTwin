from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from backend.app.db.session import get_db
from backend.app.api.schemas.window_preprocessing import WindowGenerationRequest, WindowGenerationResponse
from backend.app.services.window_generator import WindowGenerator

router = APIRouter()

@router.post("/preprocess/generate-windows", response_model=WindowGenerationResponse)
async def generate_windows(
    request: WindowGenerationRequest,
    db: Session = Depends(get_db)
):
    """
    Generates sliding windows from corrected WOFOST outputs.
    
    - Includes WOFOST states, weather, soil properties (with uncertainty!).
    - Applies Min-Max normalization.
    - Prepares data for XGBoost model training in Phase 4.
    """
    try:
        generator = WindowGenerator(db)
        result = generator.generate_windows(request)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Window generation failed: {str(e)}"
        )