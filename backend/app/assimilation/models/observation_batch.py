"""
assimilation/models/observation_batch.py — ObservationBatch ORM Model
=======================================================================

An ObservationBatch groups observations that were ingested together from
a single data acquisition event.  Examples:

    - A single Sentinel-2 overpass of a region covering 10 fields
      → one ObservationBatch, N Observation rows (one per field)

    - A 24-hour download from a soil moisture sensor network
      → one ObservationBatch, 96 Observation rows (one per 15-min reading)

    - A morning field scouting session by one agronomist visiting 3 fields
      → one ObservationBatch, 3 Observation rows

Design rationale:
    Grouping observations into batches serves three purposes:
      1. PROVENANCE — the batch records where the data came from (scene ID,
         sensor network, upload session) and when it was processed.
      2. STATUS TRACKING — a batch has an atomic processing_status that tells
         the pipeline whether all observations were saved correctly (SUCCESS),
         partially (PARTIAL), or failed (FAILED).
      3. PIPELINE EFFICIENCY — the EnKF assimilation service can query
         "all VALID batches since last assimilation date" efficiently using
         the batch-level indexes rather than scanning the observations table.

Table: observation_batches

Relationships:
    ObservationBatch.field → Field (many-to-one, nullable)
    ObservationBatch.observations → list[Observation] (one-to-many)
"""

import datetime
import enum
import uuid
from typing import Any, Optional, TYPE_CHECKING

from sqlalchemy import (
    DateTime, Enum, ForeignKey, Index,
    Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.field import Field
    from backend.app.assimilation.models.observation import Observation


# ── Enumeration ───────────────────────────────────────────────────────────────

class BatchProcessingStatus(str, enum.Enum):
    """Processing lifecycle status of an observation batch.

    Transitions:
        PENDING → (processing) → SUCCESS
        PENDING → (processing) → FAILED
        PENDING → (partial save) → PARTIAL

    Only SUCCESS and PARTIAL batches contribute observations to the
    assimilation pipeline.  FAILED batches are flagged for re-ingestion.

    Values:
        PENDING  — Batch record created; observation ingestion not yet started.
                   Set at batch creation time; transitions immediately on
                   processing start.

        SUCCESS  — All expected observations were ingested and passed initial
                   QC.  number_of_observations == actual saved rows.

        PARTIAL  — Some observations were saved but others failed (e.g. some
                   satellite pixels had cloud cover > threshold; some sensor
                   readings had transmission errors). number_of_observations
                   reflects only the rows actually saved.

        FAILED   — Ingestion failed completely (network error, API timeout,
                   corrupt file). No Observation rows were saved for this batch.
                   The batch record is retained so the pipeline can retry.
    """
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED  = "FAILED"


# ── ORM Model ─────────────────────────────────────────────────────────────────

class ObservationBatch(TimestampMixin, Base):
    """Grouped container for observations from a single data acquisition event.

    Acts as a processing manifest: it records the metadata of the acquisition
    (satellite scene ID, sensor session ID, agronomist visit) and the
    outcome of the ingestion pipeline.

    Table: observation_batches
    """

    __tablename__ = "observation_batches"

    # ── Indexes ───────────────────────────────────────────────────────────
    __table_args__ = (
        # "All batches for a field ordered by time" — common assimilation query
        Index("ix_obsbatch_field_start", "field_id", "start_time"),
        # "All satellite batches" — used by satellite ingestion pipeline
        Index("ix_obsbatch_source", "source"),
        # "All pending batches" — used by re-ingestion retry pipeline
        Index("ix_obsbatch_status", "processing_status"),
        # "All batches from a specific provider" — e.g. 'Sentinel2'
        Index("ix_obsbatch_provider", "provider_name"),
    )

    # ── Primary key ───────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc=(
            "UUID primary key for this batch, generated at creation time. "
            "Referenced by each Observation.batch_id for batch-level lookups."
        ),
    )

    # ── Field linkage (optional) ──────────────────────────────────────────
    field_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fields.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc=(
            "FK → fields.id — the Field this batch is associated with. "
            "Nullable: a batch may cover multiple fields (e.g. a satellite scene "
            "covering a large region). In that case, field_id is NULL and each "
            "individual Observation carries its own field_id. "
            "When a batch covers exactly one field (e.g. a sensor upload for a "
            "specific plot), set field_id here for efficient batch-level filtering."
        ),
    )

    # ── Source classification ─────────────────────────────────────────────
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "Origin category of this batch. Uses the same vocabulary as "
            "ObservationSource: 'SATELLITE', 'SENSOR', 'WEATHER', 'MANUAL', 'MODEL'. "
            "Stored as plain String (not Enum FK) at the batch level so that "
            "mixed-source batches (e.g. a field visit with both manual and sensor "
            "readings) can be represented without forcing a single enum value."
        ),
    )

    provider_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        doc=(
            "Name of the data provider or instrument system that produced this batch. "
            "Examples: "
            "  'Sentinel2_L2A_T43RGP'  — Sentinel-2 tile/granule ID "
            "  'SMAP_SPL4SMGP_v007'    — SMAP soil moisture product version "
            "  'WeatherStation_Lucknow' — named on-farm weather station "
            "  'FieldScout_Session_042' — agronomist field visit session ID "
            "  'SyntheticBatch_Test'    — unit test synthetic data "
            "Stored for data lineage and deduplication (same scene re-ingested twice "
            "can be detected by matching field_id + provider_name + start_time)."
        ),
    )

    # ── Temporal coverage ─────────────────────────────────────────────────
    start_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "Start of the time window covered by this batch (UTC). "
            "For satellites: start of the scene acquisition window. "
            "For sensors: first reading timestamp in the download. "
            "For manual visits: start time of the field scouting session."
        ),
    )

    end_time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc=(
            "End of the time window covered by this batch (UTC). "
            "For instantaneous acquisitions (satellite overpass): same as start_time. "
            "For sensor downloads: last reading timestamp. "
            "For manual visits: end time of the field scouting session. "
            "The [start_time, end_time] interval is used to query 'all batches "
            "overlapping a given assimilation window'."
        ),
    )

    # ── Observation count ─────────────────────────────────────────────────
    number_of_observations: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc=(
            "Number of Observation rows that were successfully saved for this batch. "
            "Set to 0 at creation; updated by the ingestion pipeline after saving. "
            "For SUCCESS batches: equals the total number of expected observations. "
            "For PARTIAL batches: equals only the rows that were saved (< expected). "
            "For FAILED batches: 0. "
            "Used for data completeness monitoring: flagging batches where "
            "number_of_observations < expected (from satellite metadata tile count, "
            "sensor reading count in header, or field scouting plan)."
        ),
    )

    # ── Processing outcome ────────────────────────────────────────────────
    processing_status: Mapped[BatchProcessingStatus] = mapped_column(
        Enum(BatchProcessingStatus, name="batch_processing_status_enum"),
        nullable=False,
        default=BatchProcessingStatus.PENDING,
        doc=(
            "Current status of the ingestion pipeline for this batch. "
            "PENDING → SUCCESS/PARTIAL/FAILED. "
            "See BatchProcessingStatus enum docstring for full semantics. "
            "Only SUCCESS and PARTIAL batches contribute to assimilation. "
            "FAILED batches are retried by the ingestion pipeline."
        ),
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc=(
            "Error message set when processing_status = FAILED or PARTIAL. "
            "Includes exception type, message, and (for PARTIAL) count of failed rows. "
            "NULL when processing_status = SUCCESS or PENDING. "
            "Example: 'SentinelHub API returned 429 Too Many Requests after 3 retries'."
        ),
    )

    # ── Metadata payload ─────────────────────────────────────────────────
    metadata_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        doc=(
            "Source-specific metadata for the entire batch. "
            "Structure depends on provider: "
            "  Sentinel-2: {'scene_id': 'S2A_T43RGP_20240315T054151', "
            "               'cloud_cover_scene': 0.03, "
            "               'processing_baseline': '05.09', "
            "               'tile_crs': 'EPSG:32643', "
            "               'pixel_count': 10980} "
            "  SMAP:       {'granule_id': 'SPL4SMGP.007_9km_...', "
            "               'orbit_direction': 'descending', "
            "               'spatial_resolution_km': 9} "
            "  Sensor:     {'device_ids': ['SM01', 'SM02'], "
            "               'firmware_version': '3.1.4', "
            "               'battery_levels': {'SM01': 87, 'SM02': 92}} "
            "  Manual:     {'scout_id': 'agr_0023', 'form_version': 'v4', "
            "               'gps_accuracy_m': 3.2} "
            "Not used by automated processing — stored for audit and debugging."
        ),
    )

    # ── Relationships ──────────────────────────────────────────────────────
    field: Mapped[Optional["Field"]] = relationship(
        "Field",
        foreign_keys=[field_id],
        doc="Parent Field (None if batch covers multiple fields or field not registered).",
    )

    observations: Mapped[list["Observation"]] = relationship(
        "Observation",
        back_populates="batch",
        foreign_keys="Observation.batch_id",
        # No cascade delete — observations are independently valuable even if
        # the batch record is cleaned up. The FK uses SET NULL.
        cascade="save-update, merge",
        lazy="select",
        doc=(
            "All Observation rows associated with this batch. "
            "Loaded lazily — batches can contain hundreds of observations "
            "and should only be loaded when explicitly requested."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ObservationBatch id={self.id!s:.8} "
            f"provider={self.provider_name!r} "
            f"source={self.source!r} "
            f"n_obs={self.number_of_observations} "
            f"status={self.processing_status.value}>"
        )
