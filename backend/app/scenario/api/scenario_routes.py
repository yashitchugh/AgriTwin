"""
scenario/api/scenario_routes.py — Scenario API Endpoints
=========================================================

Exposes the deterministic scenario sweeps via HTTP endpoints.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.api.schemas.simulate import SimulateRequest
from backend.app.scenario.schemas.scenario import ScenarioComparisonResponse
from backend.app.scenario.services.scenario_service import ScenarioService
from backend.app.scenario.models.scenario_comparison import ScenarioComparison

router = APIRouter()

def _get_comparison_response(db: Session, scenario_id) -> ScenarioComparisonResponse:
    comp = db.query(ScenarioComparison).filter(ScenarioComparison.scenario_id == scenario_id).first()
    if not comp:
        raise HTTPException(status_code=500, detail="Comparison failed to generate.")
    return ScenarioComparisonResponse.from_orm_row(comp)

@router.post(
    "/sowing-date",
    response_model=ScenarioComparisonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a sowing date scenario",
    description="Sweeps the sowing date across a set of offset days to find the optimal planting window."
)
def run_sowing_date_scenario(
    request: SimulateRequest,
    offsets: Optional[str] = Query(None, description="Comma-separated integer offsets e.g. -30,-15,0,15,30"),
    name: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    offset_list = None
    if offsets:
        try:
            offset_list = [int(o.strip()) for o in offsets.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="Offsets must be a comma-separated list of integers")
            
    service = ScenarioService(db)
    definition = service.run_sowing_date_scenario(
        base_request=request,
        offsets=offset_list,
        name=name,
        description=description,
    )
    
    return _get_comparison_response(db, definition.id)

@router.post(
    "/irrigation",
    response_model=ScenarioComparisonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run an irrigation tier scenario"
)
def run_irrigation_scenario(
    request: SimulateRequest,
    name: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    service = ScenarioService(db)
    definition = service.run_irrigation_scenario(
        base_request=request,
        name=name,
        description=description,
    )
    return _get_comparison_response(db, definition.id)

@router.post(
    "/variety",
    response_model=ScenarioComparisonResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a variety sweep scenario"
)
def run_variety_scenario(
    request: SimulateRequest,
    name: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    service = ScenarioService(db)
    definition = service.run_variety_scenario(
        base_request=request,
        name=name,
        description=description,
    )
    return _get_comparison_response(db, definition.id)

