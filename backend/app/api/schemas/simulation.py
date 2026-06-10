"""
api/schemas/simulation.py — Response Schemas for /simulations Endpoints
=========================================================================

Defines the Pydantic response models for:
  GET  /simulations              → SimulationListResponse
  GET  /simulations/{id}         → SimulationDetailResponse
  DELETE /simulations/{id}       → 204 No Content (no body)

Key design decisions:
  - All response models use explicit field names (simulation_id, not id) so
    that JSON output is unambiguous. ORM → schema mapping is done via
    explicit from_orm_row() class methods, not model_validate() with aliases.
  - DailyStateRecord mirrors DailyOutput ORM columns exactly.
  - List view (SimulationSummary) omits daily_states and JSON blobs for speed.
  - Detail view (SimulationDetailResponse) includes everything.
"""

import datetime
import uuid
from typing import Optional

from pydantic import BaseModel, Field


class DailyStateRecord(BaseModel):
    """One day of WOFOST output read from the daily_outputs table."""

    date: datetime.date
    dvs: Optional[float] = None
    lai: Optional[float] = None
    sm: Optional[float] = None
    tagp: Optional[float] = None
    twso: Optional[float] = None
    twlv: Optional[float] = None
    twst: Optional[float] = None
    twrt: Optional[float] = None
    rftra: Optional[float] = None
    tra: Optional[float] = None
    evs: Optional[float] = None
    rd: Optional[float] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, row: object) -> "DailyStateRecord":
        return cls(
            date=row.date,
            dvs=row.dvs,
            lai=row.lai,
            sm=row.sm,
            tagp=row.tagp,
            twso=row.twso,
            twlv=row.twlv,
            twst=row.twst,
            twrt=row.twrt,
            rftra=row.rftra,
            tra=row.tra,
            evs=row.evs,
            rd=row.rd,
        )


class SimulationSummary(BaseModel):
    """Compact simulation record for list views.

    Excludes daily_states and JSON blobs to keep list responses fast.
    Use GET /simulations/{simulation_id} for the full record.
    """

    simulation_id: uuid.UUID = Field(description="UUID of this SimulationRun.")
    field_id: Optional[uuid.UUID] = Field(None, description="Parent Field UUID.")
    run_type: str = Field(description="'baseline' | 'irrigated'")
    status: str = Field(description="'completed' | 'failed' | 'running'")
    model_name: str
    crop: str
    variety: str
    latitude: float
    longitude: float
    sowing_date: datetime.date
    harvest_date: Optional[datetime.date] = None
    yield_kg_ha: Optional[float] = None
    peak_lai: Optional[float] = None
    harvest_index: Optional[float] = None
    total_days: Optional[int] = None
    doe: Optional[datetime.date] = None
    doa: Optional[datetime.date] = None
    dom: Optional[datetime.date] = None
    doh: Optional[datetime.date] = None
    created_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, run: object) -> "SimulationSummary":
        return cls(
            simulation_id=run.id,
            field_id=run.field_id,
            run_type=run.run_type,
            status=run.status,
            model_name=run.model_name,
            crop=run.crop,
            variety=run.variety,
            latitude=run.latitude,
            longitude=run.longitude,
            sowing_date=run.sowing_date,
            harvest_date=run.harvest_date,
            yield_kg_ha=run.yield_kg_ha,
            peak_lai=run.peak_lai,
            harvest_index=run.harvest_index,
            total_days=run.total_days,
            doe=run.doe,
            doa=run.doa,
            dom=run.dom,
            doh=run.doh,
            created_at=run.created_at,
        )


class SimulationDetailResponse(BaseModel):
    """Full simulation record including all JSON payloads and daily time series."""

    simulation_id: uuid.UUID
    field_id: Optional[uuid.UUID] = None
    run_type: str
    status: str
    error_message: Optional[str] = None
    model_name: str
    model_version: str
    crop: str
    variety: str
    latitude: float
    longitude: float
    sowing_date: datetime.date
    harvest_date: Optional[datetime.date] = None
    use_real_weather: bool
    use_real_soil: bool

    yield_kg_ha: Optional[float] = None
    peak_lai: Optional[float] = None
    harvest_index: Optional[float] = None
    final_tagp: Optional[float] = None
    final_twso: Optional[float] = None
    total_days: Optional[int] = None

    dos: Optional[datetime.date] = None
    doe: Optional[datetime.date] = None
    doa: Optional[datetime.date] = None
    dom: Optional[datetime.date] = None
    doh: Optional[datetime.date] = None

    request_payload: Optional[dict] = None
    metrics_payload: Optional[dict] = None
    summary_payload: Optional[dict] = None
    weather_snapshot: Optional[dict] = None
    soil_snapshot: Optional[dict] = None
    warnings: Optional[list[str]] = None
    notes: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    daily_states: list[DailyStateRecord] = Field(
        default=[],
        description="Daily simulation output, ordered by date ASC.",
    )

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, run: object) -> "SimulationDetailResponse":
        return cls(
            simulation_id=run.id,
            field_id=run.field_id,
            run_type=run.run_type,
            status=run.status,
            error_message=getattr(run, "error_message", None),
            model_name=run.model_name,
            model_version=run.model_version,
            crop=run.crop,
            variety=run.variety,
            latitude=run.latitude,
            longitude=run.longitude,
            sowing_date=run.sowing_date,
            harvest_date=run.harvest_date,
            use_real_weather=run.use_real_weather,
            use_real_soil=run.use_real_soil,
            yield_kg_ha=run.yield_kg_ha,
            peak_lai=run.peak_lai,
            harvest_index=run.harvest_index,
            final_tagp=run.final_tagp,
            final_twso=run.final_twso,
            total_days=run.total_days,
            dos=run.dos,
            doe=run.doe,
            doa=run.doa,
            dom=run.dom,
            doh=run.doh,
            request_payload=run.request_payload,
            metrics_payload=run.metrics_payload,
            summary_payload=run.summary_payload,
            weather_snapshot=run.weather_snapshot,
            soil_snapshot=run.soil_snapshot,
            warnings=run.warnings,
            notes=run.notes,
            created_at=run.created_at,
        )


class SimulationListResponse(BaseModel):
    """Paginated list of SimulationSummary records."""

    total: int = Field(description="Total matching records before pagination.")
    limit: int
    offset: int
    items: list[SimulationSummary]
