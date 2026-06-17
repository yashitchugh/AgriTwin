"""
assimilation/repositories/observation_repository.py — Observation Data Access
==============================================================================

Handles all database read/write operations for Observation and ObservationBatch
records.  Follows the exact same repository pattern as SimulationRepository:

    - Session is injected at construction time.
    - Repository never calls commit() or rollback() — session lifecycle
      belongs to the FastAPI get_db() dependency or the calling service.
    - flush() after add()/delete() forces SQL to the DB engine within the
      current transaction without committing.
    - refresh() after flush() reloads server-generated columns (created_at).
    - All queries use SQLAlchemy 2.0 select() API — NOT session.query().

Methods:
    Observation writes:
        save_observation(obs)            → Observation
        save_many(obs_list)              → list[Observation]

    Observation reads:
        get_by_id(obs_id)               → Observation | None
        get_by_date(field_id, date)     → list[Observation]
        get_by_variable(variable_name, ...) → list[Observation]
        get_latest(field_id, variable_name) → Observation | None
        get_observations_between(...)   → list[Observation]

    Batch writes:
        save_batch(batch)               → ObservationBatch
        update_batch_status(...)        → ObservationBatch | None

    Batch reads:
        get_batch(batch_id)             → ObservationBatch | None
        get_batches_for_field(...)      → list[ObservationBatch]
"""

import uuid
import datetime
import logging
from typing import Optional

from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from backend.app.assimilation.models.observation import (
    Observation,
    ObservationSource,
    ObservationStatus,
)
from backend.app.assimilation.models.observation_batch import (
    ObservationBatch,
    BatchProcessingStatus,
)

logger = logging.getLogger(__name__)


class ObservationRepository:
    """Data access layer for Observation and ObservationBatch records.

    Instantiate with an active SQLAlchemy Session.
    Never holds state beyond the session — safe to create per-request.

    Args:
        db: An open SQLAlchemy Session (injected by FastAPI Depends(get_db)).

    Example:
        def my_route(db: Session = Depends(get_db)):
            repo = ObservationRepository(db)
            obs = repo.get_latest(field_id=fid, variable_name="LAI")
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ──────────────────────────────────────────────────────────────────────
    # OBSERVATION WRITE OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def save_observation(self, obs: Observation) -> Observation:
        """Persist a single Observation record to the database.

        Accepts a fully constructed Observation ORM instance.
        Does NOT commit — the caller owns the transaction.

        Args:
            obs: Observation instance (transient — not yet added to session).

        Returns:
            The same Observation instance after flush and refresh.
            All server-generated columns (created_at, updated_at) are populated.

        Example:
            obs = Observation(
                field_id=field_id,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
                variable_name="LAI",
                units="m2/m2",
                value=2.4,
                uncertainty=0.3,
                source=ObservationSource.SATELLITE,
                provider_name="Sentinel2_L2A",
                status=ObservationStatus.VALID,
            )
            saved = repo.save_observation(obs)
            print(saved.id)  # populated after flush
        """
        self.db.add(obs)
        self.db.flush()
        self.db.refresh(obs)
        logger.info(
            "Saved Observation id=%s var=%s value=%.4f source=%s status=%s",
            obs.id, obs.variable_name, obs.value, obs.source.value, obs.status.value,
        )
        return obs

    def save_many(self, observations: list[Observation]) -> list[Observation]:
        """Bulk-persist a list of Observation records.

        Uses add_all() for a single ORM round-trip rather than N individual
        add() calls.  Still uses flush()/refresh() per row so that
        server-generated columns are populated on each object.

        Args:
            observations: List of Observation instances (transient state).

        Returns:
            The same list of Observation instances after flush.

        Performance note:
            For very large batches (> 10 000 rows), consider using
            Session.bulk_insert_mappings() instead of add_all() for maximum
            throughput.  For the typical AgriTwin use case (< 500 obs per
            satellite scene per field), add_all() is adequate.
        """
        if not observations:
            return []

        self.db.add_all(observations)
        self.db.flush()

        # Refresh each object to populate server-generated columns.
        for obs in observations:
            self.db.refresh(obs)

        logger.info("Bulk-saved %d Observation rows", len(observations))
        return observations

    # ──────────────────────────────────────────────────────────────────────
    # OBSERVATION READ OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def get_by_id(self, obs_id: uuid.UUID) -> Optional[Observation]:
        """Fetch a single Observation by its UUID primary key.

        Uses Session.get() which checks the identity map before issuing SQL.

        Args:
            obs_id: UUID of the Observation.

        Returns:
            Observation instance, or None if not found.
        """
        obs = self.db.get(Observation, obs_id)
        if obs is None:
            logger.debug("get_by_id: Observation %s not found", obs_id)
        return obs

    def get_by_date(
        self,
        *,
        field_id: uuid.UUID,
        date: datetime.date,
        variable_name: Optional[str] = None,
        source: Optional[ObservationSource] = None,
        status: Optional[ObservationStatus] = ObservationStatus.VALID,
    ) -> list[Observation]:
        """Fetch observations for a field on a specific calendar date (UTC).

        Matches the UTC date component of the timestamp column, not the full
        datetime.  This is correct for daily WOFOST assimilation: we want all
        observations recorded on the same calendar day, regardless of hour.

        Args:
            field_id:      Field UUID (required).
            date:          Calendar date to match (UTC date).
            variable_name: Optional variable filter (e.g. "LAI", "SM").
            source:        Optional source filter (e.g. ObservationSource.SATELLITE).
            status:        Observation status filter. Default: VALID only.
                           Pass None to return all statuses.

        Returns:
            List of matching Observation instances, ordered by timestamp ascending.
        """
        # Build date range from calendar day boundaries in UTC
        start = datetime.datetime(date.year, date.month, date.day,
                                  tzinfo=datetime.timezone.utc)
        end   = start + datetime.timedelta(days=1)

        stmt = select(Observation).where(
            Observation.field_id == field_id,
            Observation.timestamp >= start,
            Observation.timestamp <  end,
        )

        if variable_name is not None:
            stmt = stmt.where(Observation.variable_name == variable_name)
        if source is not None:
            stmt = stmt.where(Observation.source == source)
        if status is not None:
            stmt = stmt.where(Observation.status == status)

        stmt = stmt.order_by(Observation.timestamp.asc())
        results = self.db.execute(stmt).scalars().all()
        logger.debug(
            "get_by_date: field=%s date=%s var=%s → %d rows",
            field_id, date, variable_name, len(results),
        )
        return list(results)

    def get_by_variable(
        self,
        *,
        variable_name: str,
        field_id: Optional[uuid.UUID] = None,
        source: Optional[ObservationSource] = None,
        status: Optional[ObservationStatus] = ObservationStatus.VALID,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Observation]:
        """Fetch observations filtered by variable name, with optional field/source filter.

        Designed for the assimilation service to retrieve all LAI or SM
        observations for a field, regardless of observation date.

        Args:
            variable_name: WOFOST or environmental variable (e.g. "LAI", "SM").
            field_id:      Optional Field UUID filter.
            source:        Optional source filter.
            status:        Status filter. Default: VALID only.
            limit:         Maximum records to return (default 200).
            offset:        Records to skip for pagination (default 0).

        Returns:
            List of Observation instances ordered by timestamp ascending.
        """
        stmt = select(Observation).where(
            Observation.variable_name == variable_name
        )

        if field_id is not None:
            stmt = stmt.where(Observation.field_id == field_id)
        if source is not None:
            stmt = stmt.where(Observation.source == source)
        if status is not None:
            stmt = stmt.where(Observation.status == status)

        stmt = (
            stmt.order_by(Observation.timestamp.asc())
                .offset(offset)
                .limit(limit)
        )
        results = self.db.execute(stmt).scalars().all()
        logger.debug(
            "get_by_variable: var=%s field=%s source=%s → %d rows",
            variable_name, field_id, source, len(results),
        )
        return list(results)

    def get_latest(
        self,
        *,
        field_id: uuid.UUID,
        variable_name: str,
        status: Optional[ObservationStatus] = ObservationStatus.VALID,
        before: Optional[datetime.datetime] = None,
    ) -> Optional[Observation]:
        """Fetch the most recent observation for a field and variable.

        Designed for the digital twin dashboard: "what is the latest LAI
        observed for this field?"

        Args:
            field_id:      Field UUID.
            variable_name: Variable to query (e.g. "LAI").
            status:        Status filter. Default: VALID only.
            before:        Optional upper bound on timestamp (exclusive).
                           Useful for replaying the assimilation timeline:
                           "what was the latest obs known before date T?"

        Returns:
            The most recent Observation, or None if no records match.
        """
        stmt = (
            select(Observation)
            .where(
                Observation.field_id == field_id,
                Observation.variable_name == variable_name,
            )
        )

        if status is not None:
            stmt = stmt.where(Observation.status == status)
        if before is not None:
            stmt = stmt.where(Observation.timestamp < before)

        stmt = stmt.order_by(Observation.timestamp.desc()).limit(1)
        result = self.db.execute(stmt).scalars().first()
        logger.debug(
            "get_latest: field=%s var=%s → %s",
            field_id, variable_name, result,
        )
        return result

    def get_observations_between(
        self,
        *,
        field_id: uuid.UUID,
        start: datetime.datetime,
        end: datetime.datetime,
        variable_name: Optional[str] = None,
        source: Optional[ObservationSource] = None,
        status: Optional[ObservationStatus] = ObservationStatus.VALID,
        limit: int = 1000,
    ) -> list[Observation]:
        """Fetch observations in a datetime range (inclusive start, exclusive end).

        Primary method for the EnKF assimilation service to load all observations
        covering an assimilation window (e.g. one crop season).

        Args:
            field_id:      Field UUID (required).
            start:         Window start (UTC, inclusive).
            end:           Window end (UTC, exclusive).
            variable_name: Optional variable filter.
            source:        Optional source filter.
            status:        Status filter. Default: VALID only.
            limit:         Maximum records (safeguard against huge result sets).

        Returns:
            List of Observation instances ordered by timestamp ascending.
        """
        stmt = select(Observation).where(
            Observation.field_id == field_id,
            Observation.timestamp >= start,
            Observation.timestamp <  end,
        )

        if variable_name is not None:
            stmt = stmt.where(Observation.variable_name == variable_name)
        if source is not None:
            stmt = stmt.where(Observation.source == source)
        if status is not None:
            stmt = stmt.where(Observation.status == status)

        stmt = (
            stmt.order_by(Observation.timestamp.asc())
                .limit(limit)
        )
        results = self.db.execute(stmt).scalars().all()
        logger.debug(
            "get_observations_between: field=%s %s→%s var=%s → %d rows",
            field_id, start.date(), end.date(), variable_name, len(results),
        )
        return list(results)

    def count_by_field(
        self,
        field_id: uuid.UUID,
        *,
        status: Optional[ObservationStatus] = None,
    ) -> int:
        """Count observations for a field (with optional status filter).

        Used by the API list endpoint to compute total counts for pagination.
        """
        stmt = select(func.count(Observation.id)).where(
            Observation.field_id == field_id
        )
        if status is not None:
            stmt = stmt.where(Observation.status == status)
        return self.db.execute(stmt).scalar_one()

    # ──────────────────────────────────────────────────────────────────────
    # BATCH WRITE OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def save_batch(self, batch: ObservationBatch) -> ObservationBatch:
        """Persist an ObservationBatch record.

        Called at the START of an ingestion pipeline run, before observations
        are saved.  The batch record acts as a processing manifest.

        Args:
            batch: ObservationBatch instance (transient).

        Returns:
            The same ObservationBatch after flush and refresh.
        """
        self.db.add(batch)
        self.db.flush()
        self.db.refresh(batch)
        logger.info(
            "Saved ObservationBatch id=%s provider=%s status=%s",
            batch.id, batch.provider_name, batch.processing_status.value,
        )
        return batch

    def update_batch_status(
        self,
        batch_id: uuid.UUID,
        *,
        status: BatchProcessingStatus,
        number_of_observations: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> Optional[ObservationBatch]:
        """Update the processing_status (and optionally observation count) of a batch.

        Called at the END of the ingestion pipeline with the final outcome.

        Args:
            batch_id:               UUID of the batch to update.
            status:                 New BatchProcessingStatus.
            number_of_observations: Updated count of saved observations.
            error_message:          Error details if status = FAILED or PARTIAL.

        Returns:
            Updated ObservationBatch, or None if not found.
        """
        batch = self.db.get(ObservationBatch, batch_id)
        if batch is None:
            logger.warning("update_batch_status: batch %s not found", batch_id)
            return None

        batch.processing_status = status
        if number_of_observations is not None:
            batch.number_of_observations = number_of_observations
        if error_message is not None:
            batch.error_message = error_message

        self.db.flush()
        self.db.refresh(batch)
        logger.info(
            "Updated ObservationBatch id=%s → status=%s n_obs=%d",
            batch_id, status.value, batch.number_of_observations,
        )
        return batch

    # ──────────────────────────────────────────────────────────────────────
    # BATCH READ OPERATIONS
    # ──────────────────────────────────────────────────────────────────────

    def get_batch(self, batch_id: uuid.UUID) -> Optional[ObservationBatch]:
        """Fetch a single ObservationBatch by UUID.

        Args:
            batch_id: UUID of the ObservationBatch.

        Returns:
            ObservationBatch instance, or None if not found.
        """
        return self.db.get(ObservationBatch, batch_id)

    def get_batches_for_field(
        self,
        field_id: uuid.UUID,
        *,
        source: Optional[str] = None,
        status: Optional[BatchProcessingStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ObservationBatch]:
        """Fetch ObservationBatch records for a field.

        Args:
            field_id: Field UUID (required).
            source:   Optional source filter (e.g. 'SATELLITE').
            status:   Optional processing status filter.
            limit:    Max records (default 50).
            offset:   Pagination offset.

        Returns:
            List of ObservationBatch records, ordered by start_time descending.
        """
        stmt = select(ObservationBatch).where(
            ObservationBatch.field_id == field_id
        )

        if source is not None:
            stmt = stmt.where(ObservationBatch.source == source)
        if status is not None:
            stmt = stmt.where(ObservationBatch.processing_status == status)

        stmt = (
            stmt.order_by(ObservationBatch.start_time.desc())
                .offset(offset)
                .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())
