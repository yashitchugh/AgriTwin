"""
assimilation/schemas/observation.py — Pydantic v2 Schemas for Observation API
===============================================================================

These schemas define the public API contract for the /observations endpoints.
They are decoupled from the ORM models (Observation, ObservationBatch) —
changes to the database schema do NOT automatically propagate to the API
contract (by design).

Schema hierarchy:
    ObservationCreate        → POST /observations body
    ObservationResponse      → GET /observations response item
    ObservationListResponse  → GET /observations paginated list

    ObservationBatchCreate   → POST /observations/batch body
    ObservationBatchResponse → GET /observations/batches/{id} response

Design:
    - All schemas use from_attributes=True (Pydantic v2) to enable
      construction from ORM objects via model_validate().
    - Enum fields are serialized as their string values (not names).
    - UUID fields are serialized as hyphenated UUID strings.
    - Datetime fields are serialized as ISO 8601 strings with timezone offset.
    - Optional fields default to None — never omitted from JSON output.
"""

import datetime
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Observation Schemas ───────────────────────────────────────────────────────

class ObservationCreate(BaseModel):
    """Request body for POST /observations.

    Creates a single Observation record.  The `id` is generated server-side;
    callers must NOT provide it.

    Validation rules:
        - uncertainty must be strictly positive (required for EnKF).
        - timestamp must be timezone-aware.
        - value is unrestricted at the schema level; variable-specific bounds
          are enforced by the QC pipeline after ingestion.
    """

    field_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the Field this observation belongs to. "
            "Omit if the field has not been registered yet — the observation "
            "will be stored without a field linkage and can be linked later."
        ),
    )

    simulation_run_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the SimulationRun associated with this observation. "
            "Set for MODEL-source observations generated from a specific run. "
            "Leave null for all real (satellite/sensor/manual) observations."
        ),
    )

    batch_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the ObservationBatch this observation belongs to. "
            "Set by ingestion pipelines that create a batch record first. "
            "Omit for individually submitted observations."
        ),
    )

    timestamp: datetime.datetime = Field(
        ...,
        description=(
            "UTC timestamp of the measurement. Must include timezone information. "
            "Example: '2024-03-15T06:30:00+00:00' (Sentinel-2 overpass time)."
        ),
        examples=["2024-03-15T06:30:00+00:00"],
    )

    variable_name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Name of the measured variable. Use WOFOST uppercase names where applicable: "
            "LAI, SM, TAGP, TWSO, DVS, RD, RFTRA, TRA, TWLV, TWST, TWRT. "
            "For environmental quantities: AIR_TEMPERATURE, CANOPY_TEMPERATURE, "
            "RELATIVE_HUMIDITY, RAINFALL, NDVI, EVI."
        ),
        examples=["LAI"],
    )

    units: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description=(
            "Physical unit of the value. Examples: 'm2/m2' (LAI), 'cm3/cm3' (SM), "
            "'kg/ha' (TAGP, TWSO), 'degC' (temperature), 'percent' (humidity), "
            "'mm/day' (rainfall), '-' (dimensionless: NDVI, DVS)."
        ),
        examples=["m2/m2"],
    )

    value: float = Field(
        ...,
        description="Measured or retrieved numerical value in the specified units.",
        examples=[2.4],
    )

    uncertainty: float = Field(
        ...,
        gt=0.0,
        description=(
            "Observation error standard deviation in the same units as `value`. "
            "Must be strictly positive (> 0). Used to construct the EnKF "
            "observation error covariance matrix R. "
            "Typical values: LAI ±0.3 m²/m² (Sentinel-2), SM ±0.04 cm³/cm³ (SMAP)."
        ),
        examples=[0.3],
    )

    source: str = Field(
        ...,
        description=(
            "Origin of the observation. One of: "
            "SATELLITE, SENSOR, WEATHER, MANUAL, MODEL."
        ),
        examples=["SATELLITE"],
    )

    provider_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "Name of the specific instrument or product. "
            "Examples: 'Sentinel2_L2A', 'SMAP_L4', 'SoilSensor_01', "
            "'WeatherStation_Lucknow', 'WOFOST72_Synthetic'."
        ),
        examples=["Sentinel2_L2A"],
    )

    latitude: Optional[float] = Field(
        default=None,
        ge=-90.0,
        le=90.0,
        description="WGS84 latitude of the observation point or pixel centroid.",
        examples=[26.8],
    )

    longitude: Optional[float] = Field(
        default=None,
        ge=-180.0,
        le=180.0,
        description="WGS84 longitude of the observation point or pixel centroid.",
        examples=[80.9],
    )

    quality_score: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Quality score [0–100]. Higher is better. "
            "Sentinel-2: ESA SCL cloud confidence. "
            "Sensors: signal-to-noise ratio. "
            "Manual: form completeness. Null if not applicable."
        ),
        examples=[92],
    )

    cloud_cover: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Cloud cover fraction over the observation footprint [0.0–1.0]. "
            "Only relevant for SATELLITE source. Null otherwise."
        ),
        examples=[0.03],
    )

    status: Optional[str] = Field(
        default="VALID",
        description=(
            "Initial QC status. One of: VALID, MISSING, OUTLIER, REJECTED. "
            "Default: VALID — new observations are assumed usable. "
            "The QC pipeline updates this after automated checks."
        ),
        examples=["VALID"],
    )

    raw_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Complete raw response from the data source API or sensor firmware. "
            "Stored verbatim for provenance and re-processing. Not indexed."
        ),
    )

    notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Free-text notes from the agronomist or QC analyst.",
    )

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, v: datetime.datetime) -> datetime.datetime:
        """Reject naive datetimes — all storage and arithmetic is UTC."""
        if v.tzinfo is None:
            raise ValueError(
                "timestamp must be timezone-aware. "
                "Append '+00:00' for UTC: '2024-03-15T06:30:00+00:00'."
            )
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "field_id": "550e8400-e29b-41d4-a716-446655440000",
                "timestamp": "2024-03-15T06:30:00+00:00",
                "variable_name": "LAI",
                "units": "m2/m2",
                "value": 2.4,
                "uncertainty": 0.3,
                "source": "SATELLITE",
                "provider_name": "Sentinel2_L2A",
                "latitude": 26.8,
                "longitude": 80.9,
                "quality_score": 92,
                "cloud_cover": 0.03,
                "status": "VALID",
            }
        }
    }


class ObservationResponse(BaseModel):
    """Response body for a single Observation.

    Returned by:
        POST /observations    (201 Created)
        GET  /observations    (items in paginated list)
        GET  /observations/latest
        GET  /observations/by-variable
    """

    id: uuid.UUID = Field(description="UUID primary key of this observation.")
    field_id: Optional[uuid.UUID] = Field(default=None)
    simulation_run_id: Optional[uuid.UUID] = Field(default=None)
    batch_id: Optional[uuid.UUID] = Field(default=None)
    timestamp: datetime.datetime
    variable_name: str
    units: str
    value: float
    uncertainty: float
    source: str
    provider_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    quality_score: Optional[int] = None
    cloud_cover: Optional[float] = None
    status: str
    notes: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, obs: object) -> "ObservationResponse":
        """Construct from an Observation ORM instance.

        Maps ORM enum values to their string representation.
        """
        return cls(
            id=obs.id,
            field_id=obs.field_id,
            simulation_run_id=obs.simulation_run_id,
            batch_id=obs.batch_id,
            timestamp=obs.timestamp,
            variable_name=obs.variable_name,
            units=obs.units,
            value=obs.value,
            uncertainty=obs.uncertainty,
            source=obs.source.value,
            provider_name=obs.provider_name,
            latitude=obs.latitude,
            longitude=obs.longitude,
            quality_score=obs.quality_score,
            cloud_cover=obs.cloud_cover,
            status=obs.status.value,
            notes=obs.notes,
            created_at=obs.created_at,
            updated_at=obs.updated_at,
        )


class ObservationListResponse(BaseModel):
    """Paginated list of observations.

    Returned by GET /observations and GET /observations/by-variable.
    """

    total: int = Field(description="Total matching observations (before pagination).")
    limit: int = Field(description="Page size used for this response.")
    offset: int = Field(description="Offset into the full result set.")
    items: list[ObservationResponse] = Field(description="Observations on this page.")


# ── Batch Schemas ─────────────────────────────────────────────────────────────

class ObservationBatchCreate(BaseModel):
    """Request body for POST /observations/batch.

    Creates an ObservationBatch record and all its child Observations
    in a single atomic request.
    """

    field_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Field UUID (nullable — use for single-field batches only).",
    )

    source: str = Field(
        ...,
        description="Origin category: SATELLITE, SENSOR, WEATHER, MANUAL, MODEL.",
        examples=["SATELLITE"],
    )

    provider_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Name of the instrument or data product.",
        examples=["Sentinel2_L2A"],
    )

    start_time: datetime.datetime = Field(
        ...,
        description="Start of the time window covered by this batch (UTC, timezone-aware).",
        examples=["2024-03-15T06:00:00+00:00"],
    )

    end_time: datetime.datetime = Field(
        ...,
        description="End of the time window (UTC, timezone-aware). Equal to start_time for satellites.",
        examples=["2024-03-15T06:30:00+00:00"],
    )

    metadata_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description="Source-specific metadata (scene ID, firmware version, etc.).",
    )

    observations: list[ObservationCreate] = Field(
        default_factory=list,
        description="Observations to ingest as part of this batch.",
    )

    @model_validator(mode="after")
    def end_must_be_gte_start(self) -> "ObservationBatchCreate":
        if self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time.")
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "field_id": "550e8400-e29b-41d4-a716-446655440000",
                "source": "SATELLITE",
                "provider_name": "Sentinel2_L2A",
                "start_time": "2024-03-15T06:30:00+00:00",
                "end_time": "2024-03-15T06:30:00+00:00",
                "metadata_payload": {"scene_id": "S2A_T43RGP_20240315T054151", "cloud_cover_scene": 0.03},
                "observations": [
                    {
                        "timestamp": "2024-03-15T06:30:00+00:00",
                        "variable_name": "LAI",
                        "units": "m2/m2",
                        "value": 2.4,
                        "uncertainty": 0.3,
                        "source": "SATELLITE",
                        "provider_name": "Sentinel2_L2A",
                        "latitude": 26.8,
                        "longitude": 80.9,
                        "quality_score": 92,
                        "cloud_cover": 0.03,
                    }
                ],
            }
        }
    }


class ObservationBatchResponse(BaseModel):
    """Response body for a single ObservationBatch."""

    id: uuid.UUID
    field_id: Optional[uuid.UUID] = None
    source: str
    provider_name: str
    start_time: datetime.datetime
    end_time: datetime.datetime
    number_of_observations: int
    processing_status: str
    error_message: Optional[str] = None
    metadata_payload: Optional[dict[str, Any]] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_row(cls, batch: object) -> "ObservationBatchResponse":
        return cls(
            id=batch.id,
            field_id=batch.field_id,
            source=batch.source,
            provider_name=batch.provider_name,
            start_time=batch.start_time,
            end_time=batch.end_time,
            number_of_observations=batch.number_of_observations,
            processing_status=batch.processing_status.value,
            error_message=batch.error_message,
            metadata_payload=batch.metadata_payload,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
        )


class ObservationBatchListResponse(BaseModel):
    """Paginated list of ObservationBatch records."""

    total: int
    limit: int
    offset: int
    items: list[ObservationBatchResponse]
