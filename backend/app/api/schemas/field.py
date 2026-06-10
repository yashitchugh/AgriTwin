"""
api/schemas/field.py — Request and Response Schemas for /fields Endpoints
==========================================================================
"""

import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, Field


class FieldCreate(BaseModel):
    """Request body for POST /fields.

    farm_id is optional — if not supplied a 'Default Farm' is created/reused.
    """

    farm_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of an existing Farm. If omitted, 'Default Farm' is auto-created."
        ),
    )
    name: str = Field(..., min_length=1, max_length=256, examples=["Block A North"])
    latitude: float = Field(..., ge=-90.0, le=90.0, examples=[26.8])
    longitude: float = Field(..., ge=-180.0, le=180.0, examples=[80.9])
    area_ha: Optional[float] = Field(default=None, ge=0.0, examples=[4.5])
    elevation_m: Optional[float] = Field(default=None, examples=[100.0])
    description: Optional[str] = Field(default=None, max_length=2000)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Kharif Field 2020",
                "latitude": 26.8,
                "longitude": 80.9,
                "area_ha": 4.5,
                "description": "Main paddy field — Lucknow district",
            }
        }
    }


class FieldResponse(BaseModel):
    """Response body for GET /fields and GET /fields/{id}."""

    field_id: uuid.UUID = Field(description="UUID primary key of this field.")
    farm_id: uuid.UUID = Field(description="Parent Farm UUID.")
    name: str
    latitude: float
    longitude: float
    area_ha: Optional[float] = None
    elevation_m: Optional[float] = None
    description: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None
    simulation_count: int = Field(default=0)

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, field: object, simulation_count: int = 0) -> "FieldResponse":
        return cls(
            field_id=field.id,
            farm_id=field.farm_id,
            name=field.name,
            latitude=field.latitude,
            longitude=field.longitude,
            area_ha=field.area_ha,
            elevation_m=field.elevation_m,
            description=field.description,
            created_at=field.created_at,
            updated_at=field.updated_at,
            simulation_count=simulation_count,
        )


class FieldListResponse(BaseModel):
    """Paginated list of FieldResponse records."""

    total: int = Field(description="Total matching fields before pagination.")
    limit: int
    offset: int
    items: list[FieldResponse]
