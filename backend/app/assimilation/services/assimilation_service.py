"""
assimilation/services/assimilation_service.py — Sequential Forecast-Assimilate Loop
=====================================================================================

Implements the complete EnKF data assimilation cycle:

    while not harvest:
        1. Find next observation date
        2. Forecast ensemble to that date
        3. Retrieve + QC-filter observations from DB
        4. Build observation vector y and error covariance R
        5. Apply EnKF update  → X_a
        6. Persist AssimilationState record
        7. Inject corrected states into ensemble members
        8. Continue

Design principles:
    - STATELESS: holds no mutable simulation state between calls.
    - DB-decoupled: the observation repo and state repo are injected.
    - No WOFOST imports: delegates to EnsembleManager / StateUpdater.
    - Partial observations: variables absent from y are left as NaN → EnKF skips them.
    - Outlier rejection: configurable z-score gate before building y.
    - Cloud/quality filtering: configurable thresholds on Observation metadata.
    - Irregular intervals: driven by actual observation timestamps — no fixed stride.
"""

from __future__ import annotations

import datetime
import logging
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from backend.app.assimilation.ensemble.ensemble_manager import EnsembleManager
from backend.app.assimilation.filters.enkf import enkf_update
from backend.app.assimilation.forecast.forecast_step import forecast_until
from backend.app.assimilation.models.assimilation_state import AssimilationState
from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus
from backend.app.assimilation.repositories.assimilation_state_repository import AssimilationStateRepository
from backend.app.assimilation.repositories.observation_repository import ObservationRepository
from backend.app.assimilation.state.state_vector import STATE_VARIABLES, STATE_INDEX, STATE_DIM, StateVector
from backend.app.assimilation.updater.state_updater import StateUpdater, InjectionResult
from backend.app.models.assimilation_run import AssimilationRun

logger = logging.getLogger(__name__)

# Map observation variable_name (uppercase DB convention) → StateVector lowercase key
_OBS_VAR_TO_SV: dict[str, str] = {
    "LAI":   "lai",
    "SM":    "sm",
    "TAGP":  "tagp",
    "TWSO":  "twso",
    "RFTRA": "rftra",
    "TWLV":  "twlv",
    "TWST":  "twst",
    "TWRT":  "twrt",
    "DVS":   "dvs",
    "RD":    "rd",
}


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class QCFilter:
    """Quality-control thresholds applied before building the observation vector.

    Observations failing any active threshold are silently excluded from the
    current assimilation cycle (they remain VALID in the DB — no status mutation).
    """
    min_quality_score: Optional[int]   = 60     # Skip obs with quality_score < this
    max_cloud_cover:   Optional[float] = 0.20   # Skip satellite obs with cloud_cover > this
    max_z_score:       float           = 3.0    # Outlier gate: skip if |z| > this vs ensemble


@dataclass
class AssimilationConfig:
    """Configuration for a full-season assimilation run."""
    # ── Observation sources to include
    include_sources: list[str] = field(
        default_factory=lambda: ["SATELLITE", "SENSOR", "MANUAL", "WEATHER"]
    )
    # ── QC settings
    qc: QCFilter = field(default_factory=QCFilter)
    # ── Ensemble settings
    ensemble_size: int = 50
    # ── Aggregation: when multiple obs for same variable on same date, how to combine
    # "mean" averages value and propagates uncertainty; "best_quality" picks highest score
    aggregation: str = "mean"
    # ── Minimum observations to trigger an EnKF update (skip if fewer pass QC)
    min_obs_for_update: int = 1
    # ── StateUpdater flags
    inject_dvs: bool = False
    inject_rd:  bool = False


# ── Per-cycle result ──────────────────────────────────────────────────────────

@dataclass
class AssimilationCycleResult:
    """Result of a single forecast → assimilate → inject cycle."""
    cycle_date:          datetime.date
    obs_retrieved:       int                         # raw obs from DB
    obs_after_qc:        int                         # obs passing QC
    obs_assimilated:     int                         # obs with matching SV variable
    variables_updated:   list[str]                   # SV variables that received EnKF update
    ensemble_mean_prior: dict[str, Optional[float]]  # x_f mean as dict
    ensemble_mean_post:  dict[str, Optional[float]]  # x_a mean as dict
    innovation:          dict[str, Optional[float]]  # y - H*x_f per variable
    injection_results:   list[InjectionResult]       # per-member injection records
    persisted_state_id:  Optional[uuid.UUID]         # AssimilationState DB pk
    skipped:             bool = False                # True if min_obs not met
    skip_reason:         Optional[str] = None


# ── Full-season result ────────────────────────────────────────────────────────

@dataclass
class SeasonAssimilationResult:
    """Aggregated result for a complete season assimilation run."""
    field_id:            Optional[uuid.UUID]
    simulation_run_id:   Optional[uuid.UUID]
    sow_date:            datetime.date
    harvest_date:        datetime.date
    total_cycles:        int
    executed_cycles:     int
    skipped_cycles:      int
    cycle_results:       list[AssimilationCycleResult]

    @property
    def total_observations_assimilated(self) -> int:
        return sum(c.obs_assimilated for c in self.cycle_results)


# ── Service ───────────────────────────────────────────────────────────────────

class AssimilationService:
    """Sequential forecast-assimilate loop for EnKF crop state estimation.

    Usage:
        manager = EnsembleManager(...)
        manager.create_ensemble(n=50)

        service = AssimilationService(
            obs_repo=ObservationRepository(db),
            state_repo=AssimilationStateRepository(db),
        )
        result = service.run_season(
            manager=manager,
            harvest_date=date(2024, 7, 30),
            field_id=field_uuid,
        )
    """

    def __init__(
        self,
        obs_repo: ObservationRepository,
        state_repo: AssimilationStateRepository,
        config: Optional[AssimilationConfig] = None,
    ) -> None:
        self.obs_repo   = obs_repo
        self.state_repo = state_repo
        self.config     = config or AssimilationConfig()
        self._updater   = StateUpdater(
            inject_dvs=self.config.inject_dvs,
            inject_rd=self.config.inject_rd,
            verify=False,  # speed: skip read-back in production loop
        )

    # ── Public API ────────────────────────────────────────────────────────

    def run_season(
        self,
        manager: EnsembleManager,
        harvest_date: datetime.date,
        *,
        field_id:          Optional[uuid.UUID] = None,
        simulation_run_id: Optional[uuid.UUID] = None,
        assimilation_run_id: Optional[uuid.UUID] = None,
    ) -> SeasonAssimilationResult:
        """Run the complete forecast-assimilate loop for a full crop season.

        Discovers observation dates automatically from the DB, then iterates
        the EnKF cycle for each date up to harvest_date.

        Args:
            manager:           Initialised EnsembleManager with N members created.
            harvest_date:      Stop criterion — loop ends when all members reach this date.
            field_id:          Optional field UUID for DB queries and persistence.
            simulation_run_id: Optional SimulationRun UUID for linking AssimilationState records.
            assimilation_run_id: Optional AssimilationRun UUID. If not provided but simulation_run_id is, a new run will be created.

        Returns:
            SeasonAssimilationResult with per-cycle diagnostics.
        """
        if not manager.members:
            raise ValueError("EnsembleManager has no members. Call create_ensemble() first.")

        sow_date      = manager.members[0].current_date
        obs_dates     = self._discover_observation_dates(field_id, sow_date, harvest_date)
        cycle_results: list[AssimilationCycleResult] = []

        logger.info(
            "AssimilationService.run_season: field=%s sow=%s harvest=%s obs_dates=%d",
            field_id, sow_date, harvest_date, len(obs_dates),
        )

        db_session = self.state_repo.session
        auto_run = None
        if simulation_run_id is not None and assimilation_run_id is None:
            try:
                auto_run = AssimilationRun(
                    simulation_id=simulation_run_id,
                    ensemble_size=len(manager.members),
                    status="RUNNING",
                    total_cycles=len(obs_dates),
                    executed_cycles=0,
                    skipped_cycles=0,
                    observations_used=0,
                )
                db_session.add(auto_run)
                db_session.commit()
                db_session.refresh(auto_run)
                assimilation_run_id = auto_run.id
            except Exception as e:
                logger.error("Failed to automatically create AssimilationRun record: %s", e)
                db_session.rollback()

        try:
            for obs_date in obs_dates:
                if obs_date >= harvest_date:
                    break
                # Check if all members have already terminated
                if all(m.wofost.flag_terminate for m in manager.members):
                    logger.info("All ensemble members terminated before harvest. Stopping.")
                    break

                result = self._run_cycle(
                    manager=manager,
                    obs_date=obs_date,
                    field_id=field_id,
                    simulation_run_id=simulation_run_id,
                    assimilation_run_id=assimilation_run_id,
                )
                cycle_results.append(result)

            executed = sum(1 for c in cycle_results if not c.skipped)
            skipped  = sum(1 for c in cycle_results if c.skipped)

            if auto_run is not None:
                auto_run.status = "COMPLETED"
                auto_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                auto_run.executed_cycles = executed
                auto_run.skipped_cycles = skipped
                auto_run.observations_used = sum(c.obs_assimilated for c in cycle_results)
                db_session.commit()
                db_session.refresh(auto_run)

        except Exception as e:
            if auto_run is not None:
                try:
                    auto_run.status = "FAILED"
                    auto_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                    db_session.commit()
                except Exception as db_err:
                    logger.error("Failed to update auto_run to FAILED status: %s", db_err)
            raise e

        return SeasonAssimilationResult(
            field_id=field_id,
            simulation_run_id=simulation_run_id,
            sow_date=sow_date,
            harvest_date=harvest_date,
            total_cycles=len(cycle_results),
            executed_cycles=executed,
            skipped_cycles=skipped,
            cycle_results=cycle_results,
        )

    def run_single_cycle(
        self,
        manager: EnsembleManager,
        obs_date: datetime.date,
        *,
        field_id:          Optional[uuid.UUID] = None,
        simulation_run_id: Optional[uuid.UUID] = None,
        assimilation_run_id: Optional[uuid.UUID] = None,
    ) -> AssimilationCycleResult:
        """Run one forecast → assimilate → inject cycle for a given observation date.

        Useful for step-by-step control or replaying a single assimilation event.
        """
        return self._run_cycle(
            manager=manager,
            obs_date=obs_date,
            field_id=field_id,
            simulation_run_id=simulation_run_id,
            assimilation_run_id=assimilation_run_id,
        )

    # ── Internal loop ─────────────────────────────────────────────────────

    def _run_cycle(
        self,
        manager: EnsembleManager,
        obs_date: datetime.date,
        field_id: Optional[uuid.UUID],
        simulation_run_id: Optional[uuid.UUID],
        assimilation_run_id: Optional[uuid.UUID] = None,
    ) -> AssimilationCycleResult:
        """Execute one EnKF cycle: forecast → observe → update → inject → persist."""

        # ── 1. Forecast ensemble to observation date ──────────────────────
        logger.debug("Cycle %s: running forecast step", obs_date)
        X_f, x_mean_f = forecast_until(manager, obs_date)

        # ── 2. Retrieve observations from DB ─────────────────────────────
        raw_obs = self._fetch_observations(field_id, obs_date)
        logger.debug("Cycle %s: retrieved %d observations", obs_date, len(raw_obs))

        # ── 3. QC filtering ───────────────────────────────────────────────
        qc_obs = self._apply_qc(raw_obs, X_f, x_mean_f)
        logger.debug("Cycle %s: %d observations passed QC", obs_date, len(qc_obs))

        if len(qc_obs) < self.config.min_obs_for_update:
            return AssimilationCycleResult(
                cycle_date=obs_date,
                obs_retrieved=len(raw_obs),
                obs_after_qc=len(qc_obs),
                obs_assimilated=0,
                variables_updated=[],
                ensemble_mean_prior=self._vec_to_dict(x_mean_f),
                ensemble_mean_post=self._vec_to_dict(x_mean_f),
                innovation={v: None for v in STATE_VARIABLES},
                injection_results=[],
                persisted_state_id=None,
                skipped=True,
                skip_reason=f"Only {len(qc_obs)} obs passed QC (min={self.config.min_obs_for_update})",
            )

        # ── 4. Build y and R ──────────────────────────────────────────────
        y, R, obs_assimilated = self._build_observation_vector(qc_obs)

        if obs_assimilated == 0:
            return AssimilationCycleResult(
                cycle_date=obs_date,
                obs_retrieved=len(raw_obs),
                obs_after_qc=len(qc_obs),
                obs_assimilated=0,
                variables_updated=[],
                ensemble_mean_prior=self._vec_to_dict(x_mean_f),
                ensemble_mean_post=self._vec_to_dict(x_mean_f),
                innovation={v: None for v in STATE_VARIABLES},
                injection_results=[],
                persisted_state_id=None,
                skipped=True,
                skip_reason="No QC-passed obs mapped to a known StateVector variable",
            )

        # ── 5. EnKF update ────────────────────────────────────────────────
        X_a, d, K = enkf_update(X_f, y, R)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            x_mean_a = np.nanmean(X_a, axis=1)

        variables_updated = [
            STATE_VARIABLES[i] for i in range(STATE_DIM) if not np.isnan(d[i])
        ]
        logger.info("Cycle %s: EnKF updated %d variables: %s", obs_date, len(variables_updated), variables_updated)

        # ── 6. Persist AssimilationState ──────────────────────────────────
        state_id = self._persist(
            X_f=X_f, X_a=X_a, y=y, d=d, K=K,
            n_members=len(manager.members),
            n_obs=obs_assimilated,
            obs_date=obs_date,
            field_id=field_id,
            simulation_run_id=simulation_run_id,
            assimilation_run_id=assimilation_run_id,
        )

        # ── 7. Inject corrected states ────────────────────────────────────
        analysis_states = [
            StateVector.from_numpy(X_a[:, i], date=obs_date)
            for i in range(X_a.shape[1])
        ]
        injection_results = self._updater.inject_ensemble(manager.members, analysis_states)

        return AssimilationCycleResult(
            cycle_date=obs_date,
            obs_retrieved=len(raw_obs),
            obs_after_qc=len(qc_obs),
            obs_assimilated=obs_assimilated,
            variables_updated=variables_updated,
            ensemble_mean_prior=self._vec_to_dict(x_mean_f),
            ensemble_mean_post=self._vec_to_dict(x_mean_a),
            innovation=self._vec_to_dict(d),
            injection_results=injection_results,
            persisted_state_id=state_id,
        )

    # ── Observation helpers ───────────────────────────────────────────────

    def _discover_observation_dates(
        self,
        field_id: Optional[uuid.UUID],
        sow_date: datetime.date,
        harvest_date: datetime.date,
    ) -> list[datetime.date]:
        """Return sorted list of unique calendar dates with valid observations."""
        if field_id is None:
            return []

        start = datetime.datetime.combine(sow_date, datetime.time.min, tzinfo=datetime.timezone.utc)
        end   = datetime.datetime.combine(harvest_date, datetime.time.min, tzinfo=datetime.timezone.utc)

        obs = self.obs_repo.get_observations_between(
            field_id=field_id,
            start=start,
            end=end,
            status=ObservationStatus.VALID,
            limit=10000,
        )
        dates = sorted({o.timestamp.date() for o in obs})
        logger.info("Discovered %d unique observation dates for field=%s", len(dates), field_id)
        return dates

    def _fetch_observations(
        self,
        field_id: Optional[uuid.UUID],
        obs_date: datetime.date,
    ) -> list[Observation]:
        """Fetch all VALID observations for a field on a given calendar date."""
        if field_id is None:
            return []
        return self.obs_repo.get_by_date(
            field_id=field_id,
            date=obs_date,
            status=ObservationStatus.VALID,
        )

    def _apply_qc(
        self,
        observations: list[Observation],
        X_f: np.ndarray,
        x_mean_f: np.ndarray,
    ) -> list[Observation]:
        """Apply quality filters; return observations that pass all checks."""
        cfg = self.config.qc
        passed: list[Observation] = []

        for obs in observations:
            # Source filter
            if obs.source.value not in self.config.include_sources:
                logger.debug("QC skip (source): %s", obs)
                continue

            # Quality score filter
            if cfg.min_quality_score is not None and obs.quality_score is not None:
                if obs.quality_score < cfg.min_quality_score:
                    logger.debug("QC skip (quality_score=%s): %s", obs.quality_score, obs)
                    continue

            # Cloud cover filter (satellite only)
            if (
                obs.source == ObservationSource.SATELLITE
                and cfg.max_cloud_cover is not None
                and obs.cloud_cover is not None
                and obs.cloud_cover > cfg.max_cloud_cover
            ):
                logger.debug("QC skip (cloud_cover=%.2f): %s", obs.cloud_cover, obs)
                continue

            # Outlier z-score gate vs ensemble forecast
            sv_key = _OBS_VAR_TO_SV.get(obs.variable_name.upper())
            if sv_key is not None:
                idx = STATE_INDEX[sv_key]
                ens_mean = x_mean_f[idx]
                ens_std  = float(np.nanstd(X_f[idx, :]))
                if not np.isnan(ens_mean) and ens_std > 0:
                    z = abs(obs.value - ens_mean) / ens_std
                    if z > cfg.max_z_score:
                        logger.debug(
                            "QC skip (outlier z=%.2f > %.2f): %s", z, cfg.max_z_score, obs
                        )
                        continue

            passed.append(obs)

        return passed

    def _build_observation_vector(
        self, observations: list[Observation]
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Aggregate observations into the EnKF y vector and R matrix.

        Multiple observations for the same variable on the same date are
        combined according to config.aggregation ("mean" or "best_quality").

        Returns:
            y: observation vector shape (STATE_DIM,), NaN for unobserved vars
            R: observation error covariance (STATE_DIM, STATE_DIM), diagonal
            n_assimilated: number of distinct variables in y (non-NaN count)
        """
        # Group by sv_key
        groups: dict[str, list[Observation]] = {}
        for obs in observations:
            sv_key = _OBS_VAR_TO_SV.get(obs.variable_name.upper())
            if sv_key is None:
                continue  # variable not in state vector
            groups.setdefault(sv_key, []).append(obs)

        y = np.full(STATE_DIM, np.nan)
        r_diag = np.full(STATE_DIM, np.nan)

        for sv_key, obs_list in groups.items():
            idx = STATE_INDEX[sv_key]

            if self.config.aggregation == "best_quality":
                best = max(
                    obs_list,
                    key=lambda o: (o.quality_score or 0),
                )
                y[idx]      = best.value
                r_diag[idx] = best.uncertainty ** 2
            else:  # "mean"
                values  = np.array([o.value       for o in obs_list])
                errors  = np.array([o.uncertainty for o in obs_list])
                # Inverse-variance weighted mean
                weights = 1.0 / (errors ** 2)
                y[idx]      = float(np.average(values, weights=weights))
                r_diag[idx] = float(1.0 / np.sum(weights))  # combined variance

        # R = diagonal matrix; off-diagonals are zero (independent obs assumption)
        r_diag_safe = np.where(np.isnan(r_diag), 0.0, r_diag)
        R = np.diag(r_diag_safe)

        n_assimilated = int(np.sum(~np.isnan(y)))
        return y, R, n_assimilated

    # ── Persistence ───────────────────────────────────────────────────────

    def _persist(
        self,
        *,
        X_f: np.ndarray,
        X_a: np.ndarray,
        y: np.ndarray,
        d: np.ndarray,
        K: np.ndarray,
        n_members: int,
        n_obs: int,
        obs_date: datetime.date,
        field_id: Optional[uuid.UUID],
        simulation_run_id: Optional[uuid.UUID],
        assimilation_run_id: Optional[uuid.UUID] = None,
    ) -> Optional[uuid.UUID]:
        """Persist an AssimilationState record. Returns the new record's UUID, or None on error."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                x_mean_f = np.nanmean(X_f, axis=1)
                x_mean_a = np.nanmean(X_a, axis=1)
                # Full covariance (stored as flat list for JSON)
                cov_f = np.cov(X_f).tolist()

            assimilation_time = datetime.datetime.combine(
                obs_date, datetime.time.min, tzinfo=datetime.timezone.utc
            )

            record = AssimilationState(
                field_id=field_id,
                simulation_run_id=simulation_run_id,
                assimilation_run_id=assimilation_run_id,
                assimilation_time=assimilation_time,
                ensemble_mean=self._vec_to_dict(x_mean_f),
                ensemble_covariance={"matrix": cov_f, "variables": list(STATE_VARIABLES)},
                observation_vector=self._vec_to_dict(y),
                innovation_vector=self._vec_to_dict(d),
                kalman_gain={"matrix": K.tolist(), "variables": list(STATE_VARIABLES)},
                updated_state_vector=self._vec_to_dict(x_mean_a),
                forecast_state_vector=self._vec_to_dict(x_mean_f),
                number_of_members=n_members,
                observation_count=n_obs,
            )
            saved = self.state_repo.save_state(record)
            logger.info("Persisted AssimilationState id=%s date=%s", saved.id, obs_date)
            return saved.id

        except Exception as exc:
            logger.error("Failed to persist AssimilationState: %s", exc, exc_info=True)
            return None

    # ── Utility ───────────────────────────────────────────────────────────

    @staticmethod
    def _vec_to_dict(arr: np.ndarray) -> dict[str, Optional[float]]:
        """Convert a STATE_DIM numpy array to a {variable: value} dict. NaN → None."""
        result: dict[str, Optional[float]] = {}
        for i, var in enumerate(STATE_VARIABLES):
            v = float(arr[i]) if i < len(arr) else None
            result[var] = None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(v, 6)
        return result
