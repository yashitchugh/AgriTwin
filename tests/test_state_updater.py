"""
tests/test_state_updater.py — StateUpdater Unit Tests
======================================================

All tests use MagicMock for the WOFOST engine — no PCSE simulation is run,
no internet access is needed, and tests complete in milliseconds.

The MagicMock wofost captures every set_variable() call and replays values
via get_variable() using a simple internal dict, letting us verify the full
injection-then-read-back cycle without a real PCSE engine.

Test IDs:
    SU-01: test_pcse_key_map_contains_all_required_variables
    SU-02: test_injectable_variables_tuple
    SU-03: test_injection_result_defaults
    SU-04: test_injection_result_success_property
    SU-05: test_injection_result_summary_string
    SU-06: test_inject_full_state_all_accepted
    SU-07: test_inject_skips_none_fields
    SU-08: test_inject_skips_nan_fields
    SU-09: test_inject_clamps_lai_above_max
    SU-10: test_inject_clamps_lai_below_min
    SU-11: test_inject_clamps_rftra_above_one
    SU-12: test_inject_clamps_sm_below_zero
    SU-13: test_inject_error_handling
    SU-14: test_inject_variable_whitelist
    SU-15: test_inject_dvs_disabled_by_default
    SU-16: test_inject_dvs_enabled
    SU-17: test_inject_rd_disabled_by_default
    SU-18: test_inject_rd_enabled
    SU-19: test_inject_verify_true_reads_back
    SU-20: test_inject_verify_false_skips_readback
    SU-21: test_inject_ensemble_all_members
    SU-22: test_inject_ensemble_length_mismatch
    SU-23: test_read_state
    SU-24: test_inject_partial_state_only_populated
    SU-25: test_inject_result_date_propagated
"""

import math
import datetime
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from backend.app.assimilation.updater.state_updater import (
    StateUpdater,
    InjectionResult,
    INJECTABLE_VARIABLES,
    PCSE_KEY_MAP,
    _BOUNDS,
)
from backend.app.assimilation.state.state_vector import StateVector

TODAY = datetime.date(2024, 4, 1)


# ── Mock WOFOST factory ───────────────────────────────────────────────────────

def _make_wofost(initial_state: dict | None = None) -> MagicMock:
    """Return a MagicMock that behaves like a minimal PCSE engine.

    - set_variable(key, value) stores value in internal dict
    - get_variable(key) returns the stored value (or None if not set)
    - wofost.day returns TODAY
    """
    store: dict[str, float] = dict(initial_state or {})

    wofost = MagicMock()
    wofost.day = TODAY

    def _set(key: str, value: float) -> None:
        store[key] = value

    def _get(key: str) -> float | None:
        return store.get(key)

    wofost.set_variable.side_effect = _set
    wofost.get_variable.side_effect = _get
    return wofost


def _full_state_vector(**overrides) -> StateVector:
    """Construct a fully populated StateVector for testing."""
    defaults = dict(
        date=TODAY,
        lai=2.4,
        sm=0.28,
        tagp=1200.0,
        twso=350.0,
        rftra=0.95,
        twlv=310.0,
        twst=280.0,
        twrt=85.0,
        dvs=0.55,
        rd=42.0,
    )
    defaults.update(overrides)
    return StateVector(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# SU-01, SU-02 — Module-level constants
# ═══════════════════════════════════════════════════════════════════════════════

def test_pcse_key_map_contains_all_required_variables():
    """SU-01: PCSE_KEY_MAP contains exactly the 8 required variables."""
    required = {"lai", "sm", "tagp", "twso", "twlv", "twst", "twrt", "rftra"}
    assert required <= set(PCSE_KEY_MAP.keys()), (
        f"Missing from PCSE_KEY_MAP: {required - set(PCSE_KEY_MAP.keys())}"
    )
    # dvs and rd should NOT be in the default map
    assert "dvs" not in PCSE_KEY_MAP
    assert "rd"  not in PCSE_KEY_MAP


def test_injectable_variables_tuple():
    """SU-02: INJECTABLE_VARIABLES is a tuple of the PCSE_KEY_MAP keys."""
    assert isinstance(INJECTABLE_VARIABLES, tuple)
    assert set(INJECTABLE_VARIABLES) == set(PCSE_KEY_MAP.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# SU-03, SU-04, SU-05 — InjectionResult
# ═══════════════════════════════════════════════════════════════════════════════

def test_injection_result_defaults():
    """SU-03: InjectionResult initialises with empty collections."""
    r = InjectionResult()
    assert r.date is None
    assert r.injected == {}
    assert r.skipped_none == []
    assert r.skipped_nan == []
    assert r.clamped == {}
    assert r.errors == {}
    assert r.read_back == {}
    assert r.success is False
    assert r.injection_count == 0


def test_injection_result_success_property():
    """SU-04: success is True only when injected is non-empty AND errors is empty."""
    r = InjectionResult(injected={"lai": 2.4})
    assert r.success is True

    r_error = InjectionResult(injected={"lai": 2.4}, errors={"lai": "PCSEError: failed"})
    assert r_error.success is False

    r_empty = InjectionResult()
    assert r_empty.success is False


def test_injection_result_summary_string():
    """SU-05: summary() returns a non-empty string mentioning the date."""
    r = InjectionResult(date=TODAY, injected={"lai": 2.4}, skipped_none=["twso"])
    s = r.summary()
    assert "2024-04-01" in s
    assert "lai" in s
    assert "twso" in s


# ═══════════════════════════════════════════════════════════════════════════════
# SU-06 — Full-state injection (happy path)
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_full_state_all_accepted():
    """SU-06: inject() calls set_variable for all 8 non-None injectable fields."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = _full_state_vector()

    result = updater.inject(wofost, sv)

    # All 8 injectable variables should be injected
    assert set(result.injected.keys()) == set(INJECTABLE_VARIABLES)
    assert result.errors == {}
    assert result.skipped_none == []

    # dvs and rd are not injectable by default — they should not appear
    assert "dvs" not in result.injected
    assert "rd"  not in result.injected

    # Verify set_variable was called for each injectable variable
    injected_pcse_keys = {c.args[0] for c in wofost.set_variable.call_args_list}
    assert "LAI"   in injected_pcse_keys
    assert "SM"    in injected_pcse_keys
    assert "TAGP"  in injected_pcse_keys
    assert "TWSO"  in injected_pcse_keys
    assert "TWLV"  in injected_pcse_keys
    assert "TWST"  in injected_pcse_keys
    assert "TWRT"  in injected_pcse_keys
    assert "RFTRA" in injected_pcse_keys


# ═══════════════════════════════════════════════════════════════════════════════
# SU-07, SU-08 — Skipping logic
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_skips_none_fields():
    """SU-07: Variables that are None in the StateVector are skipped."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    # Only LAI and SM populated; everything else None
    sv = StateVector(date=TODAY, lai=2.4, sm=0.28)

    result = updater.inject(wofost, sv)

    assert "lai" in result.injected
    assert "sm"  in result.injected
    assert "tagp"  in result.skipped_none
    assert "twso"  in result.skipped_none
    assert "rftra" in result.skipped_none
    assert "twlv"  in result.skipped_none
    assert "twst"  in result.skipped_none
    assert "twrt"  in result.skipped_none


def test_inject_skips_nan_fields():
    """SU-08: Variables that are NaN in the analysis array are skipped (not None)."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()

    # Simulate a StateVector where lai=nan (EnKF produced no update)
    # We use a plain object with attribute access to simulate the duck-typed interface
    class MockState:
        date  = TODAY
        lai   = float("nan")
        sm    = 0.28
        tagp  = None
        twso  = None
        rftra = None
        twlv  = None
        twst  = None
        twrt  = None
        dvs   = None
        rd    = None

    result = updater.inject(wofost, MockState())

    assert "lai" in result.skipped_nan
    assert "sm"  in result.injected


# ═══════════════════════════════════════════════════════════════════════════════
# SU-09 through SU-12 — Physical bounds clamping
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_clamps_lai_above_max():
    """SU-09: LAI > 20 is clamped to 20.0 with a warning."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, lai=999.0)  # physically impossible

    result = updater.inject(wofost, sv)

    assert "lai" in result.clamped
    original, clamped = result.clamped["lai"]
    assert original == pytest.approx(999.0)
    assert clamped  == pytest.approx(_BOUNDS["lai"][1])  # 20.0

    # The value actually injected must be the clamped value
    assert result.injected["lai"] == pytest.approx(_BOUNDS["lai"][1])
    # Verify set_variable received 20.0, not 999.0
    wofost.set_variable.assert_called_with("LAI", pytest.approx(_BOUNDS["lai"][1]))


def test_inject_clamps_lai_below_min():
    """SU-10: LAI < 0 is clamped to 0.0."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, lai=-5.0)

    result = updater.inject(wofost, sv)

    assert "lai" in result.clamped
    assert result.injected["lai"] == pytest.approx(0.0)


def test_inject_clamps_rftra_above_one():
    """SU-11: RFTRA > 1.0 is clamped to 1.0 (dimensionless factor bounded by definition)."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, rftra=1.5)

    result = updater.inject(wofost, sv)

    assert "rftra" in result.clamped
    assert result.injected["rftra"] == pytest.approx(1.0)


def test_inject_clamps_sm_below_zero():
    """SU-12: SM < 0 is clamped to 0.0 (moisture fraction cannot be negative)."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, sm=-0.1)

    result = updater.inject(wofost, sv)

    assert "sm" in result.clamped
    assert result.injected["sm"] == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# SU-13 — Error handling
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_error_handling():
    """SU-13: If set_variable() raises, the error is captured and injection continues."""
    updater = StateUpdater(verify=False)
    wofost  = MagicMock()

    # Make set_variable raise only for LAI; SM succeeds
    def _set_side_effect(key, value):
        if key == "LAI":
            raise RuntimeError("PCSE internal: LAI state not found in kiosk")

    wofost.set_variable.side_effect = _set_side_effect

    sv = StateVector(date=TODAY, lai=2.4, sm=0.28)
    result = updater.inject(wofost, sv)

    assert "lai"  in result.errors
    assert "sm"   in result.injected
    assert result.success is False   # errors present → not fully successful
    assert result.injection_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# SU-14 — Variable whitelist
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_variable_whitelist():
    """SU-14: variables=['lai', 'sm'] restricts injection to those two variables."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = _full_state_vector()

    result = updater.inject(wofost, sv, variables=["lai", "sm"])

    assert set(result.injected.keys()) == {"lai", "sm"}
    # Other injectable variables are not in any category (whitelist excluded them)
    for var in ("tagp", "twso", "twlv", "twst", "twrt", "rftra"):
        assert var not in result.injected
        assert var not in result.skipped_none
        assert var not in result.skipped_nan


# ═══════════════════════════════════════════════════════════════════════════════
# SU-15 through SU-18 — DVS and RD flags
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_dvs_disabled_by_default():
    """SU-15: DVS is NOT injected by default even when the StateVector has a dvs value."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = _full_state_vector()  # dvs=0.55

    result = updater.inject(wofost, sv)

    assert "dvs" not in result.injected
    # DVS should not appear in any call to set_variable
    injected_keys = {c.args[0] for c in wofost.set_variable.call_args_list}
    assert "DVS" not in injected_keys


def test_inject_dvs_enabled():
    """SU-16: DVS is injected when inject_dvs=True."""
    updater = StateUpdater(inject_dvs=True, verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, dvs=0.55)

    result = updater.inject(wofost, sv)

    assert "dvs" in result.injected
    assert result.injected["dvs"] == pytest.approx(0.55)
    injected_keys = {c.args[0] for c in wofost.set_variable.call_args_list}
    assert "DVS" in injected_keys


def test_inject_rd_disabled_by_default():
    """SU-17: RD is NOT injected by default."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = _full_state_vector()  # rd=42.0

    result = updater.inject(wofost, sv)

    assert "rd" not in result.injected
    injected_keys = {c.args[0] for c in wofost.set_variable.call_args_list}
    assert "RD" not in injected_keys


def test_inject_rd_enabled():
    """SU-18: RD is injected when inject_rd=True."""
    updater = StateUpdater(inject_rd=True, verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, rd=42.0)

    result = updater.inject(wofost, sv)

    assert "rd" in result.injected
    assert result.injected["rd"] == pytest.approx(42.0)


# ═══════════════════════════════════════════════════════════════════════════════
# SU-19, SU-20 — verify flag
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_verify_true_reads_back():
    """SU-19: verify=True populates result.read_back with get_variable() values."""
    updater = StateUpdater(verify=True)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, lai=2.4, sm=0.28)

    result = updater.inject(wofost, sv)

    # read_back should be populated for injected variables
    assert "lai" in result.read_back
    assert "sm"  in result.read_back
    # Values stored by _make_wofost's internal store should match what was set
    assert result.read_back["lai"] == pytest.approx(2.4)
    assert result.read_back["sm"]  == pytest.approx(0.28)


def test_inject_verify_false_skips_readback():
    """SU-20: verify=False means get_variable() is never called after injection."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=TODAY, lai=2.4)

    result = updater.inject(wofost, sv)

    assert result.read_back == {}
    # get_variable should not have been called
    wofost.get_variable.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# SU-21, SU-22 — inject_ensemble()
# ═══════════════════════════════════════════════════════════════════════════════

def _make_mock_member(member_id: int) -> MagicMock:
    """Create a mock EnsembleMember with a mock WOFOST engine."""
    member = MagicMock()
    member.member_id = member_id
    member.wofost = _make_wofost()
    return member


def test_inject_ensemble_all_members():
    """SU-21: inject_ensemble() processes all members and returns one result per member."""
    updater = StateUpdater(verify=False)
    N = 5
    members = [_make_mock_member(i) for i in range(N)]

    # Build analysis states — perturb lai slightly per member
    states = [StateVector(date=TODAY, lai=2.4 + i * 0.01, sm=0.28) for i in range(N)]

    results = updater.inject_ensemble(members, states)

    assert len(results) == N
    for i, result in enumerate(results):
        assert "lai" in result.injected
        assert result.injected["lai"] == pytest.approx(2.4 + i * 0.01)
        assert "sm"  in result.injected


def test_inject_ensemble_length_mismatch():
    """SU-22: inject_ensemble() raises ValueError if member count != state count."""
    updater = StateUpdater(verify=False)
    members = [_make_mock_member(i) for i in range(3)]
    states  = [StateVector(date=TODAY, lai=2.4) for _ in range(5)]  # wrong count

    with pytest.raises(ValueError, match="len"):
        updater.inject_ensemble(members, states)


# ═══════════════════════════════════════════════════════════════════════════════
# SU-23 — read_state()
# ═══════════════════════════════════════════════════════════════════════════════

def test_read_state():
    """SU-23: read_state() returns a dict with all injectable variables plus DVS and RD."""
    pre_loaded = {"LAI": 2.4, "SM": 0.28, "DVS": 0.55, "RD": 42.0}
    wofost = _make_wofost(initial_state=pre_loaded)

    state = StateUpdater.read_state(wofost)

    # Must contain all PCSE_KEY_MAP entries + dvs + rd
    expected_sv_keys = set(PCSE_KEY_MAP.keys()) | {"dvs", "rd"}
    assert expected_sv_keys <= set(state.keys())

    assert state["lai"] == pytest.approx(2.4)
    assert state["sm"]  == pytest.approx(0.28)
    assert state["dvs"] == pytest.approx(0.55)
    assert state["rd"]  == pytest.approx(42.0)
    # Variables not pre-loaded should be None
    assert state["tagp"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# SU-24 — Partial state (only LAI and SM from observation)
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_partial_state_only_populated():
    """SU-24: Only populated fields are injected; model retains its own state for the rest."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost(initial_state={"TAGP": 999.0})  # PCSE already has a value
    sv      = StateVector(date=TODAY, lai=2.4, sm=0.28)    # Only LAI and SM corrected

    result = updater.inject(wofost, sv)

    # Only LAI and SM injected
    assert set(result.injected.keys()) == {"lai", "sm"}
    # TAGP was NOT touched by inject()
    assert "tagp" in result.skipped_none
    assert wofost.get_variable("TAGP") == pytest.approx(999.0)  # model value retained


# ═══════════════════════════════════════════════════════════════════════════════
# SU-25 — Date propagation
# ═══════════════════════════════════════════════════════════════════════════════

def test_inject_result_date_propagated():
    """SU-25: InjectionResult.date matches the StateVector.date."""
    updater = StateUpdater(verify=False)
    wofost  = _make_wofost()
    sv      = StateVector(date=datetime.date(2024, 6, 15), lai=3.1)

    result = updater.inject(wofost, sv)

    assert result.date == datetime.date(2024, 6, 15)
