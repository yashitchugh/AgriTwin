"""
tests/test_assimilation_service.py — AssimilationService Tests
==============================================================

All tests use MagicMock — no WOFOST engine, no DB, no network.

Test IDs:
    AS-01: test_config_defaults
    AS-02: test_qc_filter_defaults
    AS-03: test_vec_to_dict_full
    AS-04: test_vec_to_dict_nan_becomes_none
    AS-05: test_build_obs_vector_single_obs
    AS-06: test_build_obs_vector_aggregation_mean
    AS-07: test_build_obs_vector_aggregation_best_quality
    AS-08: test_build_obs_vector_unknown_variable_skipped
    AS-09: test_build_obs_vector_empty_returns_all_nan
    AS-10: test_qc_source_filter
    AS-11: test_qc_quality_score_filter
    AS-12: test_qc_cloud_cover_filter_satellite_only
    AS-13: test_qc_outlier_z_score_filter
    AS-14: test_qc_passes_good_observation
    AS-15: test_cycle_skipped_when_no_obs
    AS-16: test_cycle_skipped_when_obs_below_minimum
    AS-17: test_cycle_executes_enkf_and_injects
    AS-18: test_cycle_result_variables_updated
    AS-19: test_cycle_result_innovation_populated
    AS-20: test_run_season_no_field_id_returns_empty
    AS-21: test_run_season_aggregates_cycles
    AS-22: test_season_result_total_obs_assimilated
    AS-23: test_persist_called_on_successful_cycle
    AS-24: test_cycle_skips_unknown_sv_variables
    AS-25: test_injection_results_per_member
"""

import datetime
import uuid
from unittest.mock import MagicMock, patch, call
import warnings

import numpy as np
import pytest

from backend.app.assimilation.services.assimilation_service import (
    AssimilationService,
    AssimilationConfig,
    AssimilationCycleResult,
    SeasonAssimilationResult,
    QCFilter,
    _OBS_VAR_TO_SV,
)
from backend.app.assimilation.state.state_vector import STATE_DIM, STATE_VARIABLES, STATE_INDEX
from backend.app.assimilation.models.observation import ObservationSource, ObservationStatus

TODAY   = datetime.date(2024, 4, 1)
FIELD_ID = uuid.uuid4()
UTC     = datetime.timezone.utc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_obs(
    variable_name: str = "LAI",
    value: float = 2.4,
    uncertainty: float = 0.3,
    source: str = "SATELLITE",
    status: str = "VALID",
    quality_score: int | None = 85,
    cloud_cover: float | None = 0.05,
    ts: datetime.datetime | None = None,
) -> MagicMock:
    obs = MagicMock()
    obs.variable_name  = variable_name
    obs.value          = value
    obs.uncertainty    = uncertainty
    obs.source         = ObservationSource(source)
    obs.status         = ObservationStatus(status)
    obs.quality_score  = quality_score
    obs.cloud_cover    = cloud_cover
    obs.timestamp      = ts or datetime.datetime.combine(TODAY, datetime.time(6, 30), tzinfo=UTC)
    return obs


def _make_member(terminated: bool = False) -> MagicMock:
    member = MagicMock()
    member.wofost.flag_terminate = terminated
    member.current_date = TODAY
    member.current_state = MagicMock()
    return member


def _make_manager(n: int = 5, terminated: bool = False) -> MagicMock:
    manager = MagicMock()
    manager.members = [_make_member(terminated) for _ in range(n)]
    return manager


def _make_X_f(n: int = 5, lai_mean: float = 2.0) -> np.ndarray:
    """Build a fake forecast ensemble matrix shape (STATE_DIM, n)."""
    X_f = np.full((STATE_DIM, n), np.nan)
    lai_idx = STATE_INDEX["lai"]
    sm_idx  = STATE_INDEX["sm"]
    X_f[lai_idx, :] = np.random.normal(lai_mean, 0.2, n)
    X_f[sm_idx,  :] = np.random.normal(0.28, 0.02, n)
    return X_f


def _make_service(obs_list=None, state_repo_save_ok=True) -> tuple[AssimilationService, MagicMock, MagicMock]:
    obs_repo   = MagicMock()
    state_repo = MagicMock()

    obs_repo.get_by_date.return_value          = obs_list or []
    obs_repo.get_observations_between.return_value = obs_list or []

    if state_repo_save_ok:
        saved = MagicMock()
        saved.id = uuid.uuid4()
        state_repo.save_state.return_value = saved

    service = AssimilationService(obs_repo=obs_repo, state_repo=state_repo)
    return service, obs_repo, state_repo


# ═══════════════════════════════════════════════════════════════════════════════
# AS-01, AS-02 — Config defaults
# ═══════════════════════════════════════════════════════════════════════════════

def test_config_defaults():
    """AS-01: AssimilationConfig has sensible defaults."""
    cfg = AssimilationConfig()
    assert cfg.ensemble_size == 50
    assert cfg.min_obs_for_update == 1
    assert "SATELLITE" in cfg.include_sources
    assert cfg.aggregation == "mean"


def test_qc_filter_defaults():
    """AS-02: QCFilter default thresholds are reasonable."""
    qc = QCFilter()
    assert qc.min_quality_score == 60
    assert qc.max_cloud_cover   == pytest.approx(0.20)
    assert qc.max_z_score       == pytest.approx(3.0)


# ═══════════════════════════════════════════════════════════════════════════════
# AS-03, AS-04 — _vec_to_dict utility
# ═══════════════════════════════════════════════════════════════════════════════

def test_vec_to_dict_full():
    """AS-03: _vec_to_dict converts a full array to named dict."""
    arr = np.arange(STATE_DIM, dtype=float)
    d = AssimilationService._vec_to_dict(arr)
    for i, var in enumerate(STATE_VARIABLES):
        assert d[var] == pytest.approx(float(i))


def test_vec_to_dict_nan_becomes_none():
    """AS-04: NaN values become None in the dict."""
    arr = np.full(STATE_DIM, np.nan)
    arr[STATE_INDEX["lai"]] = 2.4
    d = AssimilationService._vec_to_dict(arr)
    assert d["lai"] == pytest.approx(2.4)
    for var in STATE_VARIABLES:
        if var != "lai":
            assert d[var] is None


# ═══════════════════════════════════════════════════════════════════════════════
# AS-05 through AS-09 — _build_observation_vector
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_obs_vector_single_obs():
    """AS-05: Single LAI observation correctly populates y and R."""
    service, _, _ = _make_service()
    obs = [_make_obs("LAI", value=2.4, uncertainty=0.3)]
    y, R, n = service._build_observation_vector(obs)

    lai_idx = STATE_INDEX["lai"]
    assert n == 1
    assert y[lai_idx] == pytest.approx(2.4)
    assert R[lai_idx, lai_idx] == pytest.approx(0.3 ** 2)
    # Other entries should be NaN in y and 0 in R
    for i, var in enumerate(STATE_VARIABLES):
        if var != "lai":
            assert np.isnan(y[i])
            assert R[i, i] == pytest.approx(0.0)


def test_build_obs_vector_aggregation_mean():
    """AS-06: Two LAI obs → inverse-variance weighted mean."""
    service, _, _ = _make_service()
    obs = [
        _make_obs("LAI", value=2.0, uncertainty=0.2),
        _make_obs("LAI", value=3.0, uncertainty=0.4),
    ]
    y, R, n = service._build_observation_vector(obs)
    lai_idx = STATE_INDEX["lai"]

    # Expected: weights = [1/0.04, 1/0.16] = [25, 6.25]; sum = 31.25
    # weighted_mean = (2.0*25 + 3.0*6.25) / 31.25 = (50 + 18.75)/31.25 = 2.2
    assert y[lai_idx] == pytest.approx(2.2, abs=1e-4)
    assert n == 1


def test_build_obs_vector_aggregation_best_quality():
    """AS-07: best_quality aggregation picks the highest quality_score obs."""
    service, _, _ = _make_service()
    service.config.aggregation = "best_quality"
    obs = [
        _make_obs("LAI", value=2.0, uncertainty=0.2, quality_score=60),
        _make_obs("LAI", value=3.0, uncertainty=0.4, quality_score=90),
    ]
    y, _, n = service._build_observation_vector(obs)
    lai_idx = STATE_INDEX["lai"]
    assert y[lai_idx] == pytest.approx(3.0)


def test_build_obs_vector_unknown_variable_skipped():
    """AS-08: Observations for unknown variables (not in state vector) are skipped."""
    service, _, _ = _make_service()
    obs = [_make_obs("CANOPY_TEMPERATURE", value=25.0, uncertainty=1.0)]
    y, R, n = service._build_observation_vector(obs)
    assert n == 0
    assert all(np.isnan(y))


def test_build_obs_vector_empty_returns_all_nan():
    """AS-09: Empty observation list → y is all NaN, n=0."""
    service, _, _ = _make_service()
    y, R, n = service._build_observation_vector([])
    assert n == 0
    assert all(np.isnan(y))


# ═══════════════════════════════════════════════════════════════════════════════
# AS-10 through AS-14 — _apply_qc
# ═══════════════════════════════════════════════════════════════════════════════

def test_qc_source_filter():
    """AS-10: Observations from excluded sources are dropped."""
    service, _, _ = _make_service()
    service.config.include_sources = ["SATELLITE"]
    # Disable z-score filter so only the source filter is tested
    service.config.qc.max_z_score = 100.0

    X_f     = _make_X_f(lai_mean=2.0)
    x_mean  = np.nanmean(X_f, axis=1)
    obs_sat = _make_obs(source="SATELLITE", value=2.05)  # close to ensemble mean
    obs_sen = _make_obs(source="SENSOR",    value=2.05)

    passed = service._apply_qc([obs_sat, obs_sen], X_f, x_mean)
    assert len(passed) == 1
    assert passed[0].source == ObservationSource.SATELLITE


def test_qc_quality_score_filter():
    """AS-11: Obs with quality_score below threshold are dropped."""
    service, _, _ = _make_service()
    service.config.qc.min_quality_score = 70
    service.config.qc.max_z_score = 100.0  # disable outlier gate for this test

    X_f    = _make_X_f(lai_mean=2.4)  # both obs at 2.0 → z≈2
    x_mean = np.nanmean(X_f, axis=1)
    obs_lo = _make_obs(quality_score=50, value=2.4)
    obs_hi = _make_obs(quality_score=80, value=2.4)

    passed = service._apply_qc([obs_lo, obs_hi], X_f, x_mean)
    assert len(passed) == 1
    assert passed[0].quality_score == 80


def test_qc_cloud_cover_filter_satellite_only():
    """AS-12: Cloud cover filter applies only to SATELLITE observations."""
    service, _, _ = _make_service()
    service.config.qc.max_cloud_cover = 0.1
    service.config.qc.max_z_score = 100.0  # disable outlier gate for this test

    X_f    = _make_X_f(lai_mean=2.1)
    x_mean = np.nanmean(X_f, axis=1)

    sat_cloudy = _make_obs(source="SATELLITE", cloud_cover=0.5,  value=2.1)
    sat_clear  = _make_obs(source="SATELLITE", cloud_cover=0.05, value=2.1)
    sen_cloudy = _make_obs(source="SENSOR",    cloud_cover=0.5,  value=2.1)

    passed = service._apply_qc([sat_cloudy, sat_clear, sen_cloudy], X_f, x_mean)
    sources = [p.source.value for p in passed]
    assert "SATELLITE" in sources
    assert "SENSOR"    in sources
    assert len(passed) == 2


def test_qc_outlier_z_score_filter():
    """AS-13: Outlier z-score gate rejects observations far from ensemble mean."""
    service, _, _ = _make_service()
    service.config.qc.max_z_score = 2.0

    X_f    = _make_X_f(lai_mean=2.0)  # LAI ensemble mean ≈ 2.0, std ≈ 0.2
    x_mean = np.nanmean(X_f, axis=1)

    obs_normal  = _make_obs("LAI", value=2.1)   # z ≈ 0.5 → passes
    obs_outlier = _make_obs("LAI", value=10.0)  # z >> 2 → fails

    passed = service._apply_qc([obs_normal, obs_outlier], X_f, x_mean)
    assert len(passed) == 1
    assert passed[0].value == pytest.approx(2.1)


def test_qc_passes_good_observation():
    """AS-14: A well-formed observation with good metadata passes all QC checks."""
    service, _, _ = _make_service()
    X_f    = _make_X_f(lai_mean=2.0)
    x_mean = np.nanmean(X_f, axis=1)
    obs    = _make_obs("LAI", value=2.1, quality_score=90, cloud_cover=0.02)

    passed = service._apply_qc([obs], X_f, x_mean)
    assert len(passed) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# AS-15 through AS-19 — run_single_cycle
# ═══════════════════════════════════════════════════════════════════════════════

@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
def test_cycle_skipped_when_no_obs(mock_forecast):
    """AS-15: Cycle is skipped when no observations are retrieved."""
    X_f = _make_X_f()
    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))

    service, obs_repo, _ = _make_service(obs_list=[])
    manager = _make_manager()

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    assert result.skipped is True
    assert result.obs_retrieved == 0
    assert result.obs_assimilated == 0
    assert result.skip_reason is not None


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
def test_cycle_skipped_when_obs_below_minimum(mock_forecast):
    """AS-16: Cycle is skipped when observations fail QC (min_obs_for_update not met)."""
    X_f = _make_X_f()
    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))

    # Obs fails cloud cover filter
    cloudy_obs = _make_obs("LAI", cloud_cover=0.99, source="SATELLITE")
    service, obs_repo, _ = _make_service(obs_list=[cloudy_obs])
    obs_repo.get_by_date.return_value = [cloudy_obs]
    manager = _make_manager()

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    assert result.skipped is True


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_cycle_executes_enkf_and_injects(mock_enkf, mock_forecast):
    """AS-17: When valid obs present, EnKF runs and injection is attempted."""
    N   = 5
    X_f = _make_X_f(n=N)
    X_a = X_f + 0.05  # small correction
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.1
    K   = np.zeros((STATE_DIM, STATE_DIM))

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, K)

    obs = [_make_obs("LAI", value=2.1, cloud_cover=0.01)]
    service, obs_repo, _ = _make_service(obs_list=obs)
    obs_repo.get_by_date.return_value = obs
    manager = _make_manager(n=N)

    # Mock the updater's inject_ensemble to return dummy results
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    assert not result.skipped
    assert result.obs_assimilated >= 1
    mock_enkf.assert_called_once()
    service._updater.inject_ensemble.assert_called_once()


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_cycle_result_variables_updated(mock_enkf, mock_forecast):
    """AS-18: variables_updated lists only variables with non-NaN innovation."""
    N   = 5
    X_f = _make_X_f(n=N)
    X_a = X_f.copy()
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.15   # only LAI updated
    d[STATE_INDEX["sm"]]  = -0.02  # and SM

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, np.zeros((STATE_DIM, STATE_DIM)))

    obs = [_make_obs("LAI", value=2.1), _make_obs("SM", value=0.30, uncertainty=0.02)]
    service, obs_repo, _ = _make_service(obs_list=obs)
    obs_repo.get_by_date.return_value = obs
    manager = _make_manager(n=N)
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    assert "lai" in result.variables_updated
    assert "sm"  in result.variables_updated


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_cycle_result_innovation_populated(mock_enkf, mock_forecast):
    """AS-19: innovation dict has non-None value for updated variable."""
    N   = 5
    X_f = _make_X_f(n=N, lai_mean=2.3)  # ensemble mean = 2.3 so obs=2.3 → z≈0
    X_a = X_f.copy()
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.22

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, np.zeros((STATE_DIM, STATE_DIM)))

    obs = [_make_obs("LAI", value=2.3)]  # right at ensemble mean → z≈0 → passes
    service, obs_repo, _ = _make_service(obs_list=obs)
    obs_repo.get_by_date.return_value = obs
    manager = _make_manager(n=N)
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)
    assert result.innovation["lai"] == pytest.approx(0.22, abs=1e-5)


# ═══════════════════════════════════════════════════════════════════════════════
# AS-20 through AS-23 — run_season
# ═══════════════════════════════════════════════════════════════════════════════

def test_run_season_no_field_id_returns_empty():
    """AS-20: run_season with field_id=None produces zero cycles (no obs)."""
    service, _, _ = _make_service(obs_list=[])
    manager = _make_manager()

    result = service.run_season(
        manager,
        harvest_date=datetime.date(2024, 7, 30),
        field_id=None,
    )

    assert result.total_cycles == 0
    assert result.executed_cycles == 0
    assert result.cycle_results == []


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_run_season_aggregates_cycles(mock_enkf, mock_forecast):
    """AS-21: run_season returns one CycleResult per observation date."""
    N   = 3
    X_f = _make_X_f(n=N)
    X_a = X_f.copy()
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.1

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, np.zeros((STATE_DIM, STATE_DIM)))

    # Two observation dates
    date1 = datetime.date(2024, 4, 1)
    date2 = datetime.date(2024, 4, 15)
    obs1  = _make_obs("LAI", value=2.0, ts=datetime.datetime.combine(date1, datetime.time(6), tzinfo=UTC))
    obs2  = _make_obs("LAI", value=2.5, ts=datetime.datetime.combine(date2, datetime.time(6), tzinfo=UTC))

    service, obs_repo, _ = _make_service()
    obs_repo.get_observations_between.return_value = [obs1, obs2]
    # get_by_date returns matching obs per date
    obs_repo.get_by_date.side_effect = lambda field_id, date, **kw: (
        [obs1] if date == date1 else [obs2]
    )

    manager = _make_manager(n=N)
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_season(
        manager,
        harvest_date=datetime.date(2024, 7, 30),
        field_id=FIELD_ID,
    )

    assert result.total_cycles == 2


def test_season_result_total_obs_assimilated():
    """AS-22: SeasonAssimilationResult.total_observations_assimilated sums cycles."""
    c1 = AssimilationCycleResult(
        cycle_date=TODAY, obs_retrieved=3, obs_after_qc=3, obs_assimilated=2,
        variables_updated=["lai"], ensemble_mean_prior={}, ensemble_mean_post={},
        innovation={}, injection_results=[], persisted_state_id=None,
    )
    c2 = AssimilationCycleResult(
        cycle_date=TODAY, obs_retrieved=2, obs_after_qc=1, obs_assimilated=1,
        variables_updated=["sm"], ensemble_mean_prior={}, ensemble_mean_post={},
        innovation={}, injection_results=[], persisted_state_id=None,
    )
    season = SeasonAssimilationResult(
        field_id=FIELD_ID,
        simulation_run_id=None,
        sow_date=TODAY,
        harvest_date=datetime.date(2024, 7, 30),
        total_cycles=2,
        executed_cycles=2,
        skipped_cycles=0,
        cycle_results=[c1, c2],
    )
    assert season.total_observations_assimilated == 3


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_persist_called_on_successful_cycle(mock_enkf, mock_forecast):
    """AS-23: state_repo.save_state is called for each executed (non-skipped) cycle."""
    N   = 3
    X_f = _make_X_f(n=N)
    X_a = X_f.copy()
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.1

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, np.zeros((STATE_DIM, STATE_DIM)))

    obs = [_make_obs("LAI", value=2.1)]
    service, obs_repo, state_repo = _make_service(obs_list=obs)
    obs_repo.get_by_date.return_value = obs
    manager = _make_manager(n=N)
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    assert not result.skipped
    state_repo.save_state.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# AS-24, AS-25 — Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

def test_cycle_skips_unknown_sv_variables():
    """AS-24: Observations for variables not in the state vector build empty y."""
    service, _, _ = _make_service()
    obs = [_make_obs("NDVI", value=0.75, uncertainty=0.05)]
    y, R, n = service._build_observation_vector(obs)
    assert n == 0


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_injection_results_per_member(mock_enkf, mock_forecast):
    """AS-25: inject_ensemble is called with one StateVector per ensemble member."""
    N   = 4
    X_f = _make_X_f(n=N)
    X_a = X_f + 0.1
    d   = np.full(STATE_DIM, np.nan)
    d[STATE_INDEX["lai"]] = 0.1

    mock_forecast.return_value = (X_f, np.nanmean(X_f, axis=1))
    mock_enkf.return_value     = (X_a, d, np.zeros((STATE_DIM, STATE_DIM)))

    obs = [_make_obs("LAI", value=2.2)]
    service, obs_repo, _ = _make_service(obs_list=obs)
    obs_repo.get_by_date.return_value = obs
    manager = _make_manager(n=N)
    service._updater = MagicMock()
    service._updater.inject_ensemble.return_value = [MagicMock() for _ in range(N)]

    result = service.run_single_cycle(manager, TODAY, field_id=FIELD_ID)

    # inject_ensemble must receive exactly N StateVectors
    call_args = service._updater.inject_ensemble.call_args
    members_arg, states_arg = call_args[0]
    assert len(states_arg) == N
