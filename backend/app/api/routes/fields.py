"""
api/routes/fields.py — Field CRUD Endpoints
=============================================

GET    /fields               → paginated list with spatial bounding box filter
POST   /fields               → create a new field (auto-creates Default Farm if needed)
GET    /fields/{field_id}    → single field with simulation count
DELETE /fields/{field_id}    → cascade delete field → SimulationRun → DailyOutput
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.farm import Farm
from backend.app.models.field import Field
from backend.app.models.simulation_run import SimulationRun
from backend.app.repositories.field_repository import FieldRepository
from backend.app.api.schemas.field import FieldCreate, FieldResponse, FieldListResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_farm_id(farm_id: Optional[uuid.UUID], db: Session) -> uuid.UUID:
    """Return provided farm_id or auto-create/reuse 'Default Farm'."""
    if farm_id is not None:
        existing = db.get(Farm, farm_id)
        if existing is None:
            raise HTTPException(
                status_code=404,
                detail=f"Farm {farm_id} not found. Omit farm_id to use 'Default Farm'.",
            )
        return farm_id

    stmt = select(Farm).where(Farm.name == "Default Farm").limit(1)
    default_farm = db.execute(stmt).scalars().first()

    if default_farm is None:
        default_farm = Farm(
            id=uuid.uuid4(),
            name="Default Farm",
            description="Auto-created for fields without an explicit farm_id.",
        )
        db.add(default_farm)
        db.flush()
        logger.info("Auto-created Default Farm id=%s", default_farm.id)

    return default_farm.id


def _sim_count(field_id: uuid.UUID, db: Session) -> int:
    stmt = select(func.count(SimulationRun.id)).where(SimulationRun.field_id == field_id)
    return db.execute(stmt).scalar_one()


@router.get(
    "",
    response_model=FieldListResponse,
    summary="List fields",
    tags=["Fields"],
)
def list_fields(
    db: Session = Depends(get_db),
    farm_id: Optional[uuid.UUID] = Query(None),
    lat_min: Optional[float] = Query(None, ge=-90.0),
    lat_max: Optional[float] = Query(None, le=90.0),
    lon_min: Optional[float] = Query(None, ge=-180.0),
    lon_max: Optional[float] = Query(None, le=180.0),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FieldListResponse:
    repo = FieldRepository(db)
    items = repo.get_all_fields(
        farm_id=farm_id, lat_min=lat_min, lat_max=lat_max,
        lon_min=lon_min, lon_max=lon_max, limit=limit, offset=offset,
    )
    total = repo.get_all_fields(
        farm_id=farm_id, lat_min=lat_min, lat_max=lat_max,
        lon_min=lon_min, lon_max=lon_max, limit=100_000, offset=0,
    )
    field_ids = [f.id for f in items]
    sim_counts: dict[uuid.UUID, int] = {}
    if field_ids:
        count_stmt = (
            select(SimulationRun.field_id, func.count(SimulationRun.id).label("cnt"))
            .where(SimulationRun.field_id.in_(field_ids))
            .group_by(SimulationRun.field_id)
        )
        for row in db.execute(count_stmt):
            sim_counts[row.field_id] = row.cnt

    return FieldListResponse(
        total=len(total),
        limit=limit,
        offset=offset,
        items=[
            FieldResponse.from_orm_row(f, sim_counts.get(f.id, 0))
            for f in items
        ],
    )


@router.post(
    "",
    response_model=FieldResponse,
    status_code=201,
    summary="Create a field",
    tags=["Fields"],
)
def create_field(
    payload: FieldCreate,
    db: Session = Depends(get_db),
) -> FieldResponse:
    resolved_farm_id = _resolve_farm_id(payload.farm_id, db)
    field = Field(
        id=uuid.uuid4(),
        farm_id=resolved_farm_id,
        name=payload.name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        area_ha=payload.area_ha,
        elevation_m=payload.elevation_m,
        description=payload.description,
        boundary_geojson=payload.boundary_geojson,
    )
    repo = FieldRepository(db)
    saved = repo.create_field(field)
    db.commit()
    logger.info("POST /fields → id=%s name=%r", saved.id, saved.name)
    return FieldResponse.from_orm_row(saved, simulation_count=0)


@router.get(
    "/{field_id}",
    response_model=FieldResponse,
    summary="Get a field",
    tags=["Fields"],
)
def get_field(
    field_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> FieldResponse:
    repo = FieldRepository(db)
    field = repo.get_field(field_id)
    if field is None:
        raise HTTPException(status_code=404, detail=f"Field {field_id} not found.")
    return FieldResponse.from_orm_row(field, _sim_count(field_id, db))


@router.delete(
    "/{field_id}",
    status_code=204,
    summary="Delete a field",
    description="Cascade: Field → SimulationRun → DailyOutput. Irreversible.",
    tags=["Fields"],
)
def delete_field(
    field_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    repo = FieldRepository(db)
    deleted = repo.delete_field(field_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Field {field_id} not found.")
