"""
api/routes/simulations.py — Simulation History Endpoints
=========================================================

GET    /simulations                       → paginated list of SimulationSummary
GET    /simulations/{simulation_id}       → full detail + daily time series
DELETE /simulations/{simulation_id}       → cascade delete run + daily_outputs
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.repositories.simulation_repository import SimulationRepository
from backend.app.repositories.daily_output_repository import DailyOutputRepository
from backend.app.api.schemas.simulation import (
    SimulationSummary,
    SimulationDetailResponse,
    SimulationListResponse,
    DailyStateRecord,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "",
    response_model=SimulationListResponse,
    summary="List simulation runs",
    description=(
        "Paginated list of stored SimulationRun records, newest first. "
        "Filters: crop, variety, status, run_type, field_id. "
        "Daily time series NOT included — use GET /simulations/{id} for that."
    ),
    tags=["Simulations"],
)
def list_simulations(
    db: Session = Depends(get_db),
    crop: Optional[str] = Query(None),
    variety: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    run_type: Optional[str] = Query(None),
    field_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SimulationListResponse:
    repo = SimulationRepository(db)
    items = repo.get_all_simulations(
        crop=crop, variety=variety, status=status,
        run_type=run_type, field_id=field_id,
        limit=limit, offset=offset,
    )
    # Total count with same filters, no limit
    total_items = repo.get_all_simulations(
        crop=crop, variety=variety, status=status,
        run_type=run_type, field_id=field_id,
        limit=100_000, offset=0,
    )
    return SimulationListResponse(
        total=len(total_items),
        limit=limit,
        offset=offset,
        items=[SimulationSummary.from_orm_row(r) for r in items],
    )


@router.get(
    "/{simulation_id}",
    response_model=SimulationDetailResponse,
    summary="Get simulation detail",
    description=(
        "Full SimulationRun record including all JSON payloads and the complete "
        "daily time series (one record per simulated day)."
    ),
    tags=["Simulations"],
)
def get_simulation(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> SimulationDetailResponse:
    repo = SimulationRepository(db)
    run = repo.get_simulation(simulation_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"SimulationRun {simulation_id} not found.")

    daily_repo = DailyOutputRepository(db)
    daily_rows = daily_repo.get_daily_outputs(simulation_id)

    response = SimulationDetailResponse.from_orm_row(run)
    response.daily_states = [DailyStateRecord.from_orm_row(row) for row in daily_rows]
    return response


@router.delete(
    "/{simulation_id}",
    status_code=204,
    summary="Delete a simulation run",
    description=(
        "Permanently deletes a SimulationRun and all its DailyOutput rows. "
        "Irreversible. Cascade enforced at DB and ORM level."
    ),
    tags=["Simulations"],
)
def delete_simulation(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    repo = SimulationRepository(db)
    deleted = repo.delete_simulation(simulation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"SimulationRun {simulation_id} not found.")
