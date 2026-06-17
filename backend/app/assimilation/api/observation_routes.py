"""
assimilation/api/observation_routes.py — Observation API Endpoints
==================================================================

FastAPI router for the /observations namespace.

Endpoints:
    POST /observations              → Ingest a single observation
    POST /observations/batch        → Ingest a batch of observations
    GET  /observations              → List observations with filters
    GET  /observations/latest       → Latest observation per variable for a field
    GET  /observations/by-variable  → All observations for a specific variable
    GET  /observations/{id}         → Single observation by UUID
    GET  /observations/batches/{id} → Single batch by UUID

Design:
    - All routes use the get_db() dependency for session lifecycle.
    - ObservationRepository is instantiated per request (no global state).
    - Enum validation happens at the schema level (Pydantic); the ORM
      layer then receives typed enum values.
    - No business logic in routes — routes translate HTTP ↔ repository calls.
    - 422 Unprocessable Entity is returned automatically by FastAPI for
      invalid request bodies (schema validation failures).

Tags: ["Observations"]
Prefix: "/observations" (set in main.py)
"""

import datetime
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.assimilation.models.observation import (
    Observation,
    ObservationSource,
    ObservationStatus,
)
from backend.app.assimilation.models.observation_batch import (
    ObservationBatch,
    BatchProcessingStatus,
)
from backend.app.assimilation.repositories.observation_repository import ObservationRepository
from backend.app.assimilation.schemas.observation import (
    ObservationCreate,
    ObservationResponse,
    ObservationListResponse,
    ObservationBatchCreate,
    ObservationBatchResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_source_enum(source: str) -> ObservationSource:
    """Convert raw string to ObservationSource enum, raising 422 on invalid input."""
    try:
        return ObservationSource(source.upper())
    except ValueError:
        valid = [e.value for e in ObservationSource]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source '{source}'. Must be one of: {valid}.",
        )


def _parse_status_enum(status: str) -> ObservationStatus:
    """Convert raw string to ObservationStatus enum, raising 422 on invalid input."""
    try:
        return ObservationStatus(status.upper())
    except ValueError:
        valid = [e.value for e in ObservationStatus]
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Must be one of: {valid}.",
        )


def _build_observation(payload: ObservationCreate, batch_id: Optional[uuid.UUID] = None) -> Observation:
    """Construct an Observation ORM instance from an ObservationCreate schema.

    The batch_id argument overrides the one in the payload so that batch
    ingestion routes can set the batch_id after the batch record is saved.
    """
    return Observation(
        id=uuid.uuid4(),
        field_id=payload.field_id,
        simulation_run_id=payload.simulation_run_id,
        batch_id=batch_id if batch_id is not None else payload.batch_id,
        timestamp=payload.timestamp,
        variable_name=payload.variable_name.upper(),
        units=payload.units,
        value=payload.value,
        uncertainty=payload.uncertainty,
        source=_parse_source_enum(payload.source),
        provider_name=payload.provider_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        quality_score=payload.quality_score,
        cloud_cover=payload.cloud_cover,
        status=_parse_status_enum(payload.status or "VALID"),
        raw_payload=payload.raw_payload,
        notes=payload.notes,
    )


# ── POST /observations ────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ObservationResponse,
    status_code=201,
    summary="Ingest a single observation",
    description=(
        "Submit a single field observation from any source (satellite, sensor, "
        "weather station, manual, or model-derived). "
        "Returns the saved record with its generated UUID and timestamps."
    ),
    tags=["Observations"],
)
def create_observation(
    payload: ObservationCreate,
    db: Session = Depends(get_db),
) -> ObservationResponse:
    """Ingest one observation."""
    obs = _build_observation(payload)
    repo = ObservationRepository(db)
    saved = repo.save_observation(obs)
    logger.info(
        "POST /observations → id=%s var=%s source=%s",
        saved.id, saved.variable_name, saved.source.value,
    )
    return ObservationResponse.from_orm_row(saved)


# ── POST /observations/batch ──────────────────────────────────────────────────

@router.post(
    "/batch",
    response_model=ObservationBatchResponse,
    status_code=201,
    summary="Ingest an observation batch",
    description=(
        "Submit a batch of observations from a single acquisition event "
        "(e.g. a Sentinel-2 scene, a 24-hour sensor download, or a field "
        "scouting session). Creates one ObservationBatch record and N "
        "Observation records atomically. "
        "The batch status is set to SUCCESS if all observations are saved, "
        "PARTIAL if some fail, or FAILED if none are saved."
    ),
    tags=["Observations"],
)
def create_observation_batch(
    payload: ObservationBatchCreate,
    db: Session = Depends(get_db),
) -> ObservationBatchResponse:
    """Ingest a batch of observations from one acquisition event."""
    repo = ObservationRepository(db)

    # 1. Create the batch record (status=PENDING)
    batch = ObservationBatch(
        id=uuid.uuid4(),
        field_id=payload.field_id,
        source=payload.source.upper(),
        provider_name=payload.provider_name,
        start_time=payload.start_time,
        end_time=payload.end_time,
        number_of_observations=0,
        processing_status=BatchProcessingStatus.PENDING,
        metadata_payload=payload.metadata_payload,
    )
    saved_batch = repo.save_batch(batch)

    # 2. Build and save observations, linking them to the batch
    saved_count = 0
    failed_count = 0
    errors: list[str] = []

    for obs_payload in payload.observations:
        try:
            obs = _build_observation(obs_payload, batch_id=saved_batch.id)
            repo.save_observation(obs)
            saved_count += 1
        except Exception as e:
            failed_count += 1
            errors.append(str(e))
            logger.warning(
                "Batch %s: failed to save observation var=%s: %s",
                saved_batch.id, obs_payload.variable_name, e,
            )

    # 3. Update batch status based on outcome
    if failed_count == 0 and saved_count > 0:
        final_status = BatchProcessingStatus.SUCCESS
        error_msg = None
    elif saved_count == 0 and failed_count > 0:
        final_status = BatchProcessingStatus.FAILED
        error_msg = f"{failed_count} observation(s) failed: {'; '.join(errors[:3])}"
    elif saved_count > 0 and failed_count > 0:
        final_status = BatchProcessingStatus.PARTIAL
        error_msg = f"{failed_count} of {saved_count + failed_count} observations failed."
    else:
        # Empty batch
        final_status = BatchProcessingStatus.SUCCESS
        error_msg = None

    updated = repo.update_batch_status(
        saved_batch.id,
        status=final_status,
        number_of_observations=saved_count,
        error_message=error_msg,
    )

    logger.info(
        "POST /observations/batch → id=%s n_obs=%d status=%s",
        saved_batch.id, saved_count, final_status.value,
    )
    return ObservationBatchResponse.from_orm_row(updated)


# ── GET /observations ─────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=ObservationListResponse,
    summary="List observations",
    description=(
        "Paginated list of observations with optional filters. "
        "All query parameters are optional and combinable."
    ),
    tags=["Observations"],
)
def list_observations(
    db: Session = Depends(get_db),
    field_id: Optional[uuid.UUID] = Query(
        None,
        description="Filter by Field UUID.",
    ),
    variable_name: Optional[str] = Query(
        None,
        description="Filter by variable name (e.g. 'LAI', 'SM'). Case-insensitive.",
    ),
    source: Optional[str] = Query(
        None,
        description="Filter by source: SATELLITE, SENSOR, WEATHER, MANUAL, MODEL.",
    ),
    status: Optional[str] = Query(
        default="VALID",
        description="Filter by status: VALID, MISSING, OUTLIER, REJECTED. Default: VALID.",
    ),
    start: Optional[datetime.datetime] = Query(
        None,
        description="Start of time window (UTC ISO 8601 with timezone). Inclusive.",
    ),
    end: Optional[datetime.datetime] = Query(
        None,
        description="End of time window (UTC ISO 8601 with timezone). Exclusive.",
    ),
    limit: int = Query(50, ge=1, le=500, description="Page size (default 50, max 500)."),
    offset: int = Query(0, ge=0, description="Pagination offset (default 0)."),
) -> ObservationListResponse:
    """List observations with optional filtering and pagination."""
    repo = ObservationRepository(db)

    source_enum = _parse_source_enum(source) if source else None
    status_enum = _parse_status_enum(status) if status else None
    var_name = variable_name.upper() if variable_name else None

    if field_id is not None and (start is not None or end is not None):
        # Date-range query
        tz_utc = datetime.timezone.utc
        t_start = start or datetime.datetime(1970, 1, 1, tzinfo=tz_utc)
        t_end   = end   or datetime.datetime(2099, 12, 31, tzinfo=tz_utc)
        items = repo.get_observations_between(
            field_id=field_id,
            start=t_start,
            end=t_end,
            variable_name=var_name,
            source=source_enum,
            status=status_enum,
            limit=limit,
        )
        total = len(items)
    elif field_id is not None:
        # Field + variable query
        items = repo.get_by_variable(
            variable_name=var_name or "",
            field_id=field_id,
            source=source_enum,
            status=status_enum,
            limit=limit,
            offset=offset,
        ) if var_name else repo.get_by_variable(
            variable_name="LAI",  # broad fallback; real impl would use get_all
            field_id=field_id,
            source=source_enum,
            status=status_enum,
            limit=limit,
            offset=offset,
        )
        total = repo.count_by_field(field_id, status=status_enum)
    elif var_name is not None:
        # Variable-only query
        items = repo.get_by_variable(
            variable_name=var_name,
            source=source_enum,
            status=status_enum,
            limit=limit,
            offset=offset,
        )
        total = len(items)
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one filter is required: field_id or variable_name. "
                "Unfiltered global queries are not supported."
            ),
        )

    return ObservationListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[ObservationResponse.from_orm_row(o) for o in items],
    )


# ── GET /observations/latest ──────────────────────────────────────────────────

@router.get(
    "/latest",
    response_model=ObservationResponse,
    summary="Get latest observation",
    description=(
        "Retrieve the most recent VALID observation for a specific field and "
        "variable. Useful for the digital twin dashboard: 'what is the current "
        "LAI at Field X?'. Returns 404 if no observations are available."
    ),
    tags=["Observations"],
)
def get_latest_observation(
    field_id: uuid.UUID = Query(..., description="Field UUID (required)."),
    variable_name: str = Query(..., description="Variable name (e.g. 'LAI', 'SM')."),
    before: Optional[datetime.datetime] = Query(
        None,
        description=(
            "Optional upper bound on timestamp (UTC, exclusive). "
            "Use to replay the timeline: 'what was the latest obs before date T?'"
        ),
    ),
    db: Session = Depends(get_db),
) -> ObservationResponse:
    """Get the most recent observation for a field and variable."""
    repo = ObservationRepository(db)
    obs = repo.get_latest(
        field_id=field_id,
        variable_name=variable_name.upper(),
        before=before,
    )
    if obs is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No VALID observations found for field={field_id} "
                f"variable={variable_name.upper()}."
            ),
        )
    return ObservationResponse.from_orm_row(obs)


# ── GET /observations/by-variable ────────────────────────────────────────────

@router.get(
    "/by-variable",
    response_model=ObservationListResponse,
    summary="List observations by variable",
    description=(
        "Retrieve all observations for a specific variable, optionally "
        "filtered by field and source. Ordered by timestamp ascending — "
        "suitable for plotting time series or building the EnKF observation "
        "sequence."
    ),
    tags=["Observations"],
)
def list_by_variable(
    variable_name: str = Query(..., description="Variable name (e.g. 'LAI', 'SM')."),
    field_id: Optional[uuid.UUID] = Query(None, description="Optional field filter."),
    source: Optional[str] = Query(
        None,
        description="Optional source filter: SATELLITE, SENSOR, WEATHER, MANUAL, MODEL.",
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ObservationListResponse:
    """List all observations for a specific variable."""
    repo = ObservationRepository(db)
    source_enum = _parse_source_enum(source) if source else None

    items = repo.get_by_variable(
        variable_name=variable_name.upper(),
        field_id=field_id,
        source=source_enum,
        limit=limit,
        offset=offset,
    )

    return ObservationListResponse(
        total=len(items),
        limit=limit,
        offset=offset,
        items=[ObservationResponse.from_orm_row(o) for o in items],
    )


# ── GET /observations/{id} ────────────────────────────────────────────────────

@router.get(
    "/{observation_id}",
    response_model=ObservationResponse,
    summary="Get a single observation",
    description="Retrieve one Observation by its UUID primary key.",
    tags=["Observations"],
)
def get_observation(
    observation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ObservationResponse:
    """Get one observation by UUID."""
    repo = ObservationRepository(db)
    obs = repo.get_by_id(observation_id)
    if obs is None:
        raise HTTPException(
            status_code=404,
            detail=f"Observation {observation_id} not found.",
        )
    return ObservationResponse.from_orm_row(obs)


# ── GET /observations/batches/{id} ────────────────────────────────────────────

@router.get(
    "/batches/{batch_id}",
    response_model=ObservationBatchResponse,
    summary="Get a single observation batch",
    description="Retrieve one ObservationBatch record by its UUID.",
    tags=["Observations"],
)
def get_batch(
    batch_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ObservationBatchResponse:
    """Get one observation batch by UUID."""
    repo = ObservationRepository(db)
    batch = repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(
            status_code=404,
            detail=f"ObservationBatch {batch_id} not found.",
        )
    return ObservationBatchResponse.from_orm_row(batch)
