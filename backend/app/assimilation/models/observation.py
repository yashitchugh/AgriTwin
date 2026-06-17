"""
assimilation/models/observation.py — Observation ORM Model
===========================================================

An Observation stores a single field measurement from any heterogeneous
data source.  It is the atomic unit of the digital twin's data assimilation
layer.

Design principles:
    1. Source-agnostic — one table for all observation types (satellite,
       sensor, weather, manual, model). Discriminated by the `source` enum.
    2. Variable-agnostic — any WOFOST state variable or environmental quantity
       can be stored. The `variable_name` column holds the name; `units`
       holds the physical unit string. No constraint to a fixed variable set
       allows future variables (CANOPY_TEMPERATURE, NDVI, EVI) without
       schema changes.
    3. Uncertainty-first — every observation carries an `uncertainty` column
       (observation error standard deviation).  This is mandatory for any
       Kalman-type assimilation where the observation error covariance matrix R
       is constructed from individual observation uncertainties.
    4. Quality-aware — `quality_score` (0–100) and `status` enum allow
       automated QC routines (cloud masking, outlier detection) to flag or
       reject records before assimilation.
    5. Full provenance — `raw_payload` (JSON) stores the complete original
       API response or sensor packet so that observations can be re-processed
       if the QC algorithm changes.

Supported variable names (illustrative, not enforced by DB constraint):
    WOFOST state variables:
        LAI     — Leaf Area Index [m²/m²]
        SM      — Soil Moisture volumetric [cm³/cm³]
        TAGP    — Total Above-Ground Production [kg/ha]
        TWSO    — Total Weight Storage Organs [kg/ha]
        DVS     — Development Stage [-]

    Environmental quantities:
        AIR_TEMPERATURE     — [°C]
        CANOPY_TEMPERATURE  — [°C]
        RELATIVE_HUMIDITY   — [%]
        RAINFALL            — [mm/day]
        NDVI                — [-]
        EVI                 — [-]

Table: observations

Relationships:
    Observation.field          → Field (many-to-one, nullable FK)
    Observation.simulation_run → SimulationRun (many-to-one, nullable FK)
    Observation.batch          → ObservationBatch (many-to-one, nullable FK)
"""

import datetime
import enum
import uuid
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import (
    DateTime, Enum, Float, ForeignKey, Index,
    Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.field import Field
    from backend.app.models.simulation_run import SimulationRun
    from backend.app.assimilation.models.observation_batch import ObservationBatch


# ── Enumerations ──────────────────────────────────────────────────────────────

class ObservationSource(str, enum.Enum):
    """Identifies the physical or computational origin of an observation.

    Used to route observations to source-specific processing pipelines and
    to construct source-stratified observation error covariance matrices.

    Values:
        SATELLITE — Remote sensing products (Sentinel-2, MODIS, Landsat).
                    Variables: LAI, NDVI, EVI, canopy temperature.
                    Uncertainty driven by atmospheric correction and spatial
                    resolution.  cloud_cover column is populated for satellites.

        SENSOR    — Ground-based electronic sensors (soil moisture probes,
                    lysimeters, leaf-clip LAI meters).
                    Variables: SM, LAI, soil temperature.
                    Uncertainty driven by sensor calibration error.

        WEATHER   — On-farm or gridded weather station data.
                    Variables: AIR_TEMPERATURE, RELATIVE_HUMIDITY, RAINFALL,
                    wind speed, solar radiation.
                    Uncertainty driven by interpolation error (gridded) or
                    calibration error (station).

        MANUAL    — Field-scouted measurements entered by agronomists.
                    Variables: canopy height, phenological stage, LAI
                    (via destructive sampling).
                    Uncertainty typically large (human variability).

        MODEL     — Synthetic observations derived from a model run.
                    Used for unit testing EnKF code without real data.
                    Always flagged as MODEL so assimilation modules can
                    treat them separately from real observations.
    """
    SATELLITE = "SATELLITE"
    SENSOR    = "SENSOR"
    WEATHER   = "WEATHER"
    MANUAL    = "MANUAL"
    MODEL     = "MODEL"


class ObservationStatus(str, enum.Enum):
    """Quality control lifecycle status of an observation.

    Controls whether an observation is eligible for assimilation.
    Only VALID observations should be passed to the EnKF analysis step.

    Values:
        VALID    — Passed all automated and manual QC checks. Ready for
                   assimilation. This is the default on ingestion.

        MISSING  — The expected observation was not available (sensor offline,
                   cloud cover > threshold, manual entry not submitted).
                   The observation row exists as a placeholder to maintain the
                   temporal record of expected vs. received data.

        OUTLIER  — Automated QC flagged the value as statistically implausible
                   (e.g. LAI = 20 m²/m², or soil moisture > saturation).
                   The raw value is retained for audit; the observation is
                   excluded from assimilation until manually reviewed.

        REJECTED — Manually marked as unusable (wrong field, sensor failure,
                   known atmospheric contamination). Permanently excluded
                   from assimilation.
    """
    VALID    = "VALID"
    MISSING  = "MISSING"
    OUTLIER  = "OUTLIER"
    REJECTED = "REJECTED"


# ── ORM Model ─────────────────────────────────────────────────────────────────

class Observation(TimestampMixin, Base):
    """A single field measurement from any observation source.

    This is the atomic unit of the EnKF data assimilation layer.
    Each row represents one variable measured at one point in time at one
    location, with its associated uncertainty and quality metadata.

    Table: observations
    """

    __tablename__ = "observations"

    # ── Indexes ───────────────────────────────────────────────────────────
    __table_args__ = (
        # Primary query: "all observations for a field between dates T1 and T2"
        # Covered by this composite index — field_id filters, timestamp orders.
        Index("ix_obs_field_timestamp", "field_id", "timestamp"),

        # "All LAI observations across all fields for date range"
        # Used by the EnKF analysis step to gather observations by variable.
        Index("ix_obs_variable_timestamp", "variable_name", "timestamp"),

        # "All satellite observations" — used by source-specific pipelines.
        Index("ix_obs_source", "source"),

        # Status-based filtering: "all VALID observations" for assimilation.
        Index("ix_obs_status", "status"),

        # Batch lookup: "all observations belonging to a satellite scene"
        Index("ix_obs_batch_id", "batch_id"),
    )

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc=(
            "UUID primary key, generated by the application at creation time. "
            "Stable across DB migrations and useful as an external reference "
            "in API responses and observation batch manifests."
        ),
    )

    # ── Field linkage (optional) ──────────────────────────────────────────
    field_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fields.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "FK → fields.id — the Field this observation belongs to. "
            "Nullable: an observation can be ingested before a Field record "
            "exists (e.g. satellite pre-processing pipeline runs before field "
            "registration). SET NULL on field deletion preserves the observation "
            "record for audit purposes. "
            "Index enables fast spatial filtering: 'all observations for field X'."
        ),
    )

    # ── Simulation linkage (optional) ─────────────────────────────────────
    simulation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "FK → simulation_runs.id — the SimulationRun this observation "
            "is associated with, if any. "
            "Set when MODEL-source observations are generated from a specific run, "
            "or when an observation is used to correct a specific simulation. "
            "NULL for real satellite/sensor observations that exist independently "
            "of any simulation. SET NULL on run deletion preserves the observation."
        ),
    )

    # ── Batch linkage (optional) ──────────────────────────────────────────
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("observation_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "FK → observation_batches.id — the batch this observation belongs to. "
            "Set when the observation was ingested as part of a satellite scene "
            "download or bulk sensor data upload. "
            "NULL for observations ingested individually (e.g. manual entry via API)."
        ),
    )

    # ── Temporal ─────────────────────────────────────────────────────────
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "UTC timestamp of the actual measurement or observation. "
            "For satellites: the scene acquisition time (overpass UTC). "
            "For sensors: the sensor reading timestamp. "
            "For manual entries: the date/time the agronomist recorded the value. "
            "For daily model outputs: midnight UTC of the simulation day. "
            "Always timezone-aware (UTC). Applications should convert to local "
            "time for display only — all storage and arithmetic is UTC."
        ),
    )

    # ── Variable identity ─────────────────────────────────────────────────
    variable_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc=(
            "Name of the measured physical or model variable. "
            "WOFOST state variable names (uppercase): "
            "  LAI, SM, TAGP, TWSO, DVS, RD, RFTRA, TRA, TWLV, TWST, TWRT. "
            "Environmental quantities (uppercase): "
            "  AIR_TEMPERATURE, CANOPY_TEMPERATURE, RELATIVE_HUMIDITY, "
            "  RAINFALL, NDVI, EVI, WIND_SPEED, SOLAR_RADIATION. "
            "Using uppercase matches WOFOST naming convention; the EnKF "
            "analysis step matches observation variable names directly to "
            "WOFOST get_variable() / set_variable() keys."
        ),
    )

    units: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "Physical unit of the measured value (SI preferred). "
            "Standard units per variable: "
            "  LAI → 'm2/m2' "
            "  SM  → 'cm3/cm3' "
            "  TAGP, TWSO, TWLV, TWST, TWRT → 'kg/ha' "
            "  AIR_TEMPERATURE, CANOPY_TEMPERATURE → 'degC' "
            "  RELATIVE_HUMIDITY → 'percent' "
            "  RAINFALL → 'mm/day' "
            "  NDVI, EVI, DVS, RFTRA → '-' (dimensionless) "
            "  RD, TRA → 'cm' or 'cm/day' "
            "Stored explicitly so that unit-conversion errors in the "
            "assimilation pipeline can be diagnosed from audit logs."
        ),
    )

    # ── Measurement value ─────────────────────────────────────────────────
    value: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc=(
            "The measured or retrieved numerical value of the variable. "
            "Stored in the physical unit specified by `units`. "
            "For categorical observations (phenological stage) use a "
            "numeric encoding and document the mapping in raw_payload. "
            "Note: no range validation at the DB level — the application "
            "layer applies variable-specific bounds (e.g. LAI ∈ [0, 15]) "
            "during QC and sets status=OUTLIER for out-of-bounds values."
        ),
    )

    uncertainty: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc=(
            "Observation error standard deviation in the same units as `value`. "
            "Represents the total measurement uncertainty from all sources: "
            "  Satellite: atmospheric correction error + retrieval algorithm uncertainty "
            "  Sensor: calibration error + spatial representativeness error "
            "  Manual: inter-observer variability + sampling protocol uncertainty "
            "Used directly to construct the observation error covariance matrix R "
            "in the EnKF analysis step: R = diag(uncertainty_i²) for independent obs. "
            "Must be > 0. A value of 0 would make R singular and the Kalman gain "
            "infinite — always assign a minimum uncertainty floor (e.g. 0.01 for LAI)."
        ),
    )

    # ── Source classification ─────────────────────────────────────────────
    source: Mapped[ObservationSource] = mapped_column(
        Enum(ObservationSource, name="observation_source_enum"),
        nullable=False,
        doc=(
            "Physical or computational origin of this observation. "
            "Drives source-specific processing pipelines and allows "
            "stratified uncertainty quantification (satellite observations "
            "generally have higher uncertainty than close-range sensors). "
            "See ObservationSource enum docstring for full semantics."
        ),
    )

    provider_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc=(
            "Name of the specific instrument, product, or system that "
            "provided this observation. Finer-grained than `source`. "
            "Examples per source type: "
            "  SATELLITE → 'Sentinel2_L2A', 'MODIS_MOD15A2H', 'SMAP_L4' "
            "  SENSOR    → 'SoilMoistureSensor_01', 'LysimeterA', 'ASD_FieldSpec' "
            "  WEATHER   → 'AWOS_Lucknow', 'IMD_GriddedDaily', 'ERA5_Reanalysis' "
            "  MANUAL    → 'FieldScout_AgriTwinApp', 'BBCH_Scale_Manual' "
            "  MODEL     → 'WOFOST72_WLP_FD_Synthetic', 'SyntheticTest' "
            "Used to filter observations by data product in the assimilation "
            "service (e.g. 'only use Sentinel-2 LAI, not MODIS LAI')."
        ),
    )

    # ── Spatial coordinates ───────────────────────────────────────────────
    latitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Decimal-degree latitude (WGS84) of the observation point or "
            "the centroid of the satellite pixel/footprint. "
            "Nullable because some observations (batch-level aggregates) "
            "carry their spatial reference on the ObservationBatch record. "
            "Required for satellite observations to enable spatial matching "
            "to field boundaries (point-in-polygon or area-weighted average)."
        ),
    )

    longitude: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Decimal-degree longitude (WGS84) of the observation. "
            "See latitude docstring for spatial semantics."
        ),
    )

    # ── Quality control ───────────────────────────────────────────────────
    quality_score: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        doc=(
            "Dimensionless quality score in the range [0, 100]. "
            "Higher is better. Semantics depend on the source: "
            "  Sentinel-2: scene_classification confidence (ESA SCL product) "
            "  Soil sensor: signal-to-noise ratio from the sensor firmware "
            "  Manual: completeness score of the field scouting form (0–100) "
            "  Model: always 100 (synthetic, no measurement error beyond uncertainty) "
            "NULL if no quality score is available for this source. "
            "The assimilation pipeline may filter out observations below a "
            "configurable quality_score threshold (e.g. 70)."
        ),
    )

    cloud_cover: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
        doc=(
            "Cloud cover fraction over the observation footprint [0.0–1.0]. "
            "Only populated for SATELLITE source observations. "
            "NULL for all other source types. "
            "Cloud cover > 0.1 (10%) typically degrades optical reflectance "
            "retrievals (LAI, NDVI); the QC pipeline should set status=OUTLIER "
            "or status=MISSING for heavily cloud-contaminated pixels. "
            "Cloud cover is provided by the satellite L2 processing chain "
            "(ESA SentinelHub SCL mask for Sentinel-2, MOD09 QA bits for MODIS)."
        ),
    )

    status: Mapped[ObservationStatus] = mapped_column(
        Enum(ObservationStatus, name="observation_status_enum"),
        nullable=False,
        default=ObservationStatus.VALID,
        doc=(
            "Quality control status of this observation. "
            "Default: VALID — newly ingested observations are assumed usable. "
            "The QC pipeline updates this field after automated checks. "
            "Only VALID observations are passed to the EnKF analysis step. "
            "See ObservationStatus enum docstring for full state semantics."
        ),
    )

    # ── Raw provenance ────────────────────────────────────────────────────
    raw_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Complete raw response from the data source API or sensor firmware. "
            "Stored verbatim for full provenance and re-processing capability. "
            "Structure depends on source: "
            "  Sentinel-2: SentinelHub API JSON response (geometry, bands, cloud mask) "
            "  SMAP: HDF5-extracted dict (lat, lon, soil_moisture, quality_flag) "
            "  SoilSensor: MQTT payload (device_id, timestamp, readings, battery) "
            "  Manual: Form submission JSON (observer_id, field_notes, photos) "
            "  Model: SimulationResult snippet (run_id, date, variable, value) "
            "Not indexed — access only through filtered queries on other columns."
        ),
    )

    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Free-text notes from the agronomist or QC analyst. "
            "Examples: 'Heavy rain before measurement, soil may be saturated', "
            "'Calibration certificate expired — uncertainty doubled', "
            "'Used for EnKF test run 2024-03 — do not re-use in production'. "
            "Not used by automated processing pipelines."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    field: Mapped[Optional["Field"]] = relationship(
        "Field",
        foreign_keys=[field_id],
        doc=(
            "Parent Field record. None for observations ingested before "
            "field registration or for ad-hoc API submissions."
        ),
    )

    simulation_run: Mapped[Optional["SimulationRun"]] = relationship(
        "SimulationRun",
        foreign_keys=[simulation_run_id],
        doc=(
            "Associated SimulationRun (MODEL source) or the run that used "
            "this observation for assimilation. None for real observations "
            "not yet linked to a simulation."
        ),
    )

    batch: Mapped[Optional["ObservationBatch"]] = relationship(
        "ObservationBatch",
        back_populates="observations",
        foreign_keys=[batch_id],
        doc="Parent ObservationBatch (satellite scene or sensor upload). None if ingested individually.",
    )

    def __repr__(self) -> str:
        return (
            f"<Observation id={self.id!s:.8} "
            f"var={self.variable_name!r} "
            f"value={self.value:.4f} "
            f"±{self.uncertainty:.4f} "
            f"source={self.source.value} "
            f"ts={self.timestamp.date()} "
            f"status={self.status.value}>"
        )
