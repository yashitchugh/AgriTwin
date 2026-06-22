"""
assimilation/api/assimilation_routes.py — Assimilation API Endpoints
==================================================================

FastAPI router for the /assimilation namespace.
"""

import datetime
import logging
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.simulation_run import SimulationRun
from backend.app.models.field import Field
from backend.app.models.assimilation_run import AssimilationRun
from backend.app.assimilation.models.assimilation_state import AssimilationState
from backend.app.assimilation.services.assimilation_service import AssimilationService
from backend.app.assimilation.repositories.observation_repository import ObservationRepository
from backend.app.assimilation.repositories.assimilation_state_repository import AssimilationStateRepository
from backend.app.assimilation.ensemble.ensemble_manager import EnsembleManager
from backend.app.assimilation.schemas.assimilation_api import (
    AssimilationRunRequest,
    AssimilationRunStartResponse,
    AssimilationStatusResponse,
)
from backend.app.assimilation.services.assimilation_visualization_service import AssimilationVisualizationService
from backend.app.assimilation.schemas.assimilation_visualization import (
    CycleHistoryItem,
    TimeSeriesResponse,
    YieldEvolutionPoint,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/run",
    response_model=AssimilationRunStartResponse,
    summary="Run seasonal EnKF loop",
    description=(
        "Exposes the season EnKF loop. Validates the existence of the simulation run and "
        "field, creates a new RUNNING AssimilationRun record, constructs the EnsembleManager "
        "using parameters from the baseline simulation, runs the sequential forecast-assimilate "
        "loop, and updates the execution status and statistics upon completion."
    ),
    response_description="Created AssimilationRun details after execution.",
    tags=["Assimilation"],
)
def run_assimilation(
    request: AssimilationRunRequest,
    db: Session = Depends(get_db),
) -> AssimilationRunStartResponse:
    """Execute the seasonal forecast-assimilate loop for a simulation run.

    Runs synchronously because the underlying PCSE ensemble forecasting is CPU-bound.
    FastAPI will automatically dispatch this sync endpoint to a threadpool.
    """
    # 1. Fetch simulation run
    sim_run = db.query(SimulationRun).filter(SimulationRun.id == request.simulation_id).first()
    if not sim_run:
        raise HTTPException(
            status_code=404,
            detail=f"SimulationRun with ID {request.simulation_id} not found."
        )

    # Fetch field
    field = db.query(Field).filter(Field.id == request.field_id).first()
    if not field:
        raise HTTPException(
            status_code=404,
            detail=f"Field with ID {request.field_id} not found."
        )

    # 2. Create AssimilationRun row
    run_record = AssimilationRun(
        simulation_id=request.simulation_id,
        ensemble_size=request.ensemble_size,
        status="RUNNING",
        total_cycles=0,
        executed_cycles=0,
        skipped_cycles=0,
        observations_used=0,
        config_json={},
    )
    db.add(run_record)
    db.commit()
    db.refresh(run_record)

    try:
        # Determine elevation
        elevation = field.elevation_m if field.elevation_m is not None else 10.0

        # 3. Create EnsembleManager and generate ensemble
        manager = EnsembleManager(
            crop_name=sim_run.crop,
            variety_name=sim_run.variety,
            sow_date=sim_run.sowing_date,
            harvest_date=sim_run.harvest_date,
            latitude=sim_run.latitude,
            longitude=sim_run.longitude,
            elevation=elevation,
            use_nasa_weather=sim_run.use_real_weather,
            soil_params=sim_run.soil_snapshot,
        )
        manager.create_ensemble(n=request.ensemble_size)

        # 4. Instantiate AssimilationService
        obs_repo = ObservationRepository(db)
        state_repo = AssimilationStateRepository(db)
        service = AssimilationService(obs_repo=obs_repo, state_repo=state_repo)

        # 5. Call AssimilationService.run_season()
        result = service.run_season(
            manager=manager,
            harvest_date=sim_run.harvest_date,
            field_id=request.field_id,
            simulation_run_id=request.simulation_id,
            assimilation_run_id=run_record.id,
        )

        # 6. Store stats and mark COMPLETED
        run_record.total_cycles = result.total_cycles
        run_record.executed_cycles = result.executed_cycles
        run_record.skipped_cycles = result.skipped_cycles
        run_record.observations_used = result.total_observations_assimilated
        run_record.status = "COMPLETED"
        run_record.completed_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        db.refresh(run_record)

    except Exception as e:
        logger.error(
            "Assimilation run failed for simulation %s, run %s: %s",
            request.simulation_id, run_record.id, e, exc_info=True
        )
        run_record.status = "FAILED"
        run_record.completed_at = datetime.datetime.now(datetime.timezone.utc)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Assimilation run failed: {type(e).__name__}: {str(e)}"
        )

    return AssimilationRunStartResponse(
        assimilation_run_id=run_record.id,
        status=run_record.status,
        executed_cycles=run_record.executed_cycles,
        observations_assimilated=run_record.observations_used,
    )


@router.get(
    "/status/{simulation_id}",
    response_model=AssimilationStatusResponse,
    summary="Get status of latest assimilation run",
    description="Returns the execution status, configuration, and progression of the latest assimilation run for the given simulation run ID.",
    tags=["Assimilation"],
)
def get_assimilation_status(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> AssimilationStatusResponse:
    # 1. Fetch simulation run
    sim_run = db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
    if not sim_run:
        raise HTTPException(
            status_code=404,
            detail=f"SimulationRun with ID {simulation_id} not found."
        )

    # 2. Fetch latest assimilation run
    latest_run = (
        db.query(AssimilationRun)
        .filter(AssimilationRun.simulation_id == simulation_id)
        .order_by(AssimilationRun.started_at.desc())
        .first()
    )
    if not latest_run:
        raise HTTPException(
            status_code=404,
            detail=f"No assimilation runs found for simulation run ID {simulation_id}."
        )

    # 3. Query latest cycle date from states associated with the run
    latest_state = (
        db.query(AssimilationState)
        .filter(AssimilationState.assimilation_run_id == latest_run.id)
        .order_by(AssimilationState.assimilation_time.desc())
        .first()
    )
    latest_cycle_date = latest_state.assimilation_time.date() if latest_state else None

    return AssimilationStatusResponse(
        assimilation_run_id=latest_run.id,
        latest_assimilation_run=latest_run.id,
        status=latest_run.status,
        ensemble_size=latest_run.ensemble_size,
        total_cycles=latest_run.total_cycles,
        executed_cycles=latest_run.executed_cycles,
        skipped_cycles=latest_run.skipped_cycles,
        latest_cycle_date=latest_cycle_date,
        observations_assimilated=latest_run.observations_used,
    )


@router.get(
    "/{simulation_id}/history",
    response_model=List[CycleHistoryItem],
    summary="Get EnKF assimilation history",
    description="Returns step-by-step audit history of EnKF updates for the latest assimilation run of the given simulation ID.",
    tags=["Assimilation"],
)
def get_assimilation_history(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> List[CycleHistoryItem]:
    sim_run = db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
    if not sim_run:
        raise HTTPException(
            status_code=404,
            detail=f"SimulationRun with ID {simulation_id} not found."
        )
    latest_run = (
        db.query(AssimilationRun)
        .filter(AssimilationRun.simulation_id == simulation_id)
        .order_by(AssimilationRun.started_at.desc())
        .first()
    )
    if not latest_run:
        raise HTTPException(
            status_code=404,
            detail=f"No assimilation runs found for simulation ID {simulation_id}."
        )

    service = AssimilationVisualizationService(db)
    return service.get_history(simulation_id)


@router.get(
    "/{simulation_id}/timeseries",
    response_model=TimeSeriesResponse,
    summary="Get comparative timeseries data",
    description="Returns comparative timeseries curves (open-loop, assimilated with updates propagated, and observations) for crop parameters.",
    tags=["Assimilation"],
)
def get_assimilation_timeseries(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> TimeSeriesResponse:
    sim_run = db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
    if not sim_run:
        raise HTTPException(
            status_code=404,
            detail=f"SimulationRun with ID {simulation_id} not found."
        )

    service = AssimilationVisualizationService(db)
    return service.get_timeseries(simulation_id)


@router.get(
    "/{simulation_id}/yield-evolution",
    response_model=List[YieldEvolutionPoint],
    summary="Get yield prediction evolution",
    description="Returns predicted crop yield evolution across each assimilation cycle.",
    tags=["Assimilation"],
)
def get_yield_evolution(
    simulation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> List[YieldEvolutionPoint]:
    sim_run = db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
    if not sim_run:
        raise HTTPException(
            status_code=404,
            detail=f"SimulationRun with ID {simulation_id} not found."
        )
    latest_run = (
        db.query(AssimilationRun)
        .filter(AssimilationRun.simulation_id == simulation_id)
        .order_by(AssimilationRun.started_at.desc())
        .first()
    )
    if not latest_run:
        raise HTTPException(
            status_code=404,
            detail=f"No assimilation runs found for simulation ID {simulation_id}."
        )

    service = AssimilationVisualizationService(db)
    return service.get_yield_evolution(simulation_id)

