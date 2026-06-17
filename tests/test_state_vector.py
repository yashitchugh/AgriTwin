"""
tests/test_state_vector.py — StateVector Unit Tests
====================================================

Tests for backend/app/assimilation/state/state_vector.py

No WOFOST, no DB, no network.  All tests are pure in-memory.

Test IDs:
    SV-01: test_state_variables_order_and_count
    SV-02: test_state_index_matches_variables
    SV-03: test_state_dim
    SV-04: test_default_construction
    SV-05: test_partial_construction
    SV-06: test_full_construction
    SV-07: test_frozen_immutability
    SV-08: test_to_numpy_full
    SV-09: test_to_numpy_partial_fills_nan
    SV-10: test_to_numpy_fill_value_zero
    SV-11: test_to_numpy_shape_and_dtype
    SV-12: test_to_numpy_canonical_order
    SV-13: test_from_numpy_roundtrip
    SV-14: test_from_numpy_nan_becomes_none
    SV-15: test_from_numpy_wrong_shape
    SV-16: test_from_daily_output
    SV-17: test_from_daily_output_none_fields
    SV-18: test_to_dict_full
    SV-19: test_to_dict_none_fields
    SV-20: test_to_dict_date_iso_string
    SV-21: test_populated_variables
    SV-22: test_is_complete
    SV-23: test_missing_variables
    SV-24: test_get_valid
    SV-25: test_get_invalid_key
    SV-26: test_repr
"""

import datetime
import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.app.assimilation.state.state_vector import (
    StateVector,
    STATE_VARIABLES,
    STATE_INDEX,
    STATE_DIM,
)

TODAY = datetime.date(2024, 3, 15)

# Canonical full set of values — keyed by STATE_VARIABLES names
FULL_VALUES = {
    "lai":   2.4,
    "sm":    0.28,
    "tagp":  1200.0,
    "twso":  350.0,
    "rftra": 0.95,
    "twlv":  310.0,
    "twst":  280.0,
    "twrt":  85.0,
    "dvs":   0.55,
    "rd":    42.0,
}


def _full_sv() -> StateVector:
    """Helper: construct a fully populated StateVector."""
    return StateVector(date=TODAY, **FULL_VALUES)


def _make_daily_output_mock(**overrides) -> MagicMock:
    """Create a MagicMock that quacks like a DailyOutput ORM row."""
    mock = MagicMock()
    mock.date = TODAY
    defaults = dict(FULL_VALUES)
    defaults.update(overrides)
    for attr, val in defaults.items():
        setattr(mock, attr, val)
    return mock


# ═══════════════════════════════════════════════════════════════════════════════
# SV-01, SV-02, SV-03 — Module-level constants
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_variables_order_and_count():
    """SV-01: STATE_VARIABLES has exactly the 10 required variables in order."""
    expected = ("lai", "sm", "tagp", "twso", "rftra", "twlv", "twst", "twrt", "dvs", "rd")
    assert STATE_VARIABLES == expected


def test_state_index_matches_variables():
    """SV-02: STATE_INDEX maps each variable to the correct 0-based index."""
    for i, var in enumerate(STATE_VARIABLES):
        assert STATE_INDEX[var] == i, f"{var} should be at index {i}, got {STATE_INDEX[var]}"


def test_state_dim():
    """SV-03: STATE_DIM equals 10."""
    assert STATE_DIM == 10


# ═══════════════════════════════════════════════════════════════════════════════
# SV-04, SV-05, SV-06 — Construction
# ═══════════════════════════════════════════════════════════════════════════════

def test_default_construction():
    """SV-04: Default StateVector has all None fields and None date."""
    sv = StateVector()
    assert sv.date is None
    for var in STATE_VARIABLES:
        assert getattr(sv, var) is None


def test_partial_construction():
    """SV-05: Partial StateVector preserves provided values; others remain None."""
    sv = StateVector(date=TODAY, lai=2.4, sm=0.28)
    assert sv.date == TODAY
    assert sv.lai == pytest.approx(2.4)
    assert sv.sm  == pytest.approx(0.28)
    for var in STATE_VARIABLES:
        if var not in ("lai", "sm"):
            assert getattr(sv, var) is None


def test_full_construction():
    """SV-06: Fully populated StateVector holds all values."""
    sv = _full_sv()
    for var, expected in FULL_VALUES.items():
        assert getattr(sv, var) == pytest.approx(expected), f"{var} mismatch"


# ═══════════════════════════════════════════════════════════════════════════════
# SV-07 — Immutability
# ═══════════════════════════════════════════════════════════════════════════════

def test_frozen_immutability():
    """SV-07: StateVector is frozen — attribute assignment raises FrozenInstanceError."""
    sv = _full_sv()
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError (or AttributeError)
        sv.lai = 99.0  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# SV-08 through SV-12 — to_numpy()
# ═══════════════════════════════════════════════════════════════════════════════

def test_to_numpy_full():
    """SV-08: to_numpy() returns correct values for a fully populated vector."""
    sv = _full_sv()
    arr = sv.to_numpy()
    for var, expected in FULL_VALUES.items():
        idx = STATE_INDEX[var]
        assert arr[idx] == pytest.approx(expected), f"{var} at index {idx}"


def test_to_numpy_partial_fills_nan():
    """SV-09: to_numpy() fills None fields with NaN by default."""
    sv = StateVector(lai=2.4, sm=0.28)
    arr = sv.to_numpy()
    assert arr[STATE_INDEX["lai"]] == pytest.approx(2.4)
    assert arr[STATE_INDEX["sm"]]  == pytest.approx(0.28)
    for var in STATE_VARIABLES:
        if var not in ("lai", "sm"):
            assert math.isnan(arr[STATE_INDEX[var]]), f"{var} should be NaN"


def test_to_numpy_fill_value_zero():
    """SV-10: to_numpy(fill_value=0.0) replaces None with 0 instead of NaN."""
    sv = StateVector(lai=2.4)
    arr = sv.to_numpy(fill_value=0.0)
    assert arr[STATE_INDEX["lai"]] == pytest.approx(2.4)
    for var in STATE_VARIABLES:
        if var != "lai":
            assert arr[STATE_INDEX[var]] == pytest.approx(0.0), f"{var} should be 0.0"


def test_to_numpy_shape_and_dtype():
    """SV-11: to_numpy() returns shape (STATE_DIM,) and dtype float64."""
    arr = _full_sv().to_numpy()
    assert arr.shape == (STATE_DIM,)
    assert arr.dtype == np.float64


def test_to_numpy_canonical_order():
    """SV-12: to_numpy() index 0=LAI, 1=SM, 2=TAGP, ... matches STATE_VARIABLES."""
    sv = _full_sv()
    arr = sv.to_numpy()
    assert arr[0] == pytest.approx(FULL_VALUES["lai"])    # index 0: lai
    assert arr[1] == pytest.approx(FULL_VALUES["sm"])     # index 1: sm
    assert arr[2] == pytest.approx(FULL_VALUES["tagp"])   # index 2: tagp
    assert arr[3] == pytest.approx(FULL_VALUES["twso"])   # index 3: twso
    assert arr[4] == pytest.approx(FULL_VALUES["rftra"])  # index 4: rftra
    assert arr[5] == pytest.approx(FULL_VALUES["twlv"])   # index 5: twlv
    assert arr[6] == pytest.approx(FULL_VALUES["twst"])   # index 6: twst
    assert arr[7] == pytest.approx(FULL_VALUES["twrt"])   # index 7: twrt
    assert arr[8] == pytest.approx(FULL_VALUES["dvs"])    # index 8: dvs
    assert arr[9] == pytest.approx(FULL_VALUES["rd"])     # index 9: rd


# ═══════════════════════════════════════════════════════════════════════════════
# SV-13 through SV-15 — from_numpy()
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_numpy_roundtrip():
    """SV-13: from_numpy(sv.to_numpy()) recovers the original StateVector."""
    sv = _full_sv()
    arr = sv.to_numpy()
    sv2 = StateVector.from_numpy(arr, date=sv.date)

    assert sv2.date == sv.date
    for var in STATE_VARIABLES:
        assert getattr(sv2, var) == pytest.approx(getattr(sv, var)), f"{var} mismatch"


def test_from_numpy_nan_becomes_none():
    """SV-14: from_numpy() converts NaN array elements back to None fields."""
    arr = np.full(STATE_DIM, np.nan)
    arr[STATE_INDEX["lai"]] = 2.4
    arr[STATE_INDEX["sm"]]  = 0.28

    sv = StateVector.from_numpy(arr, date=TODAY)
    assert sv.lai == pytest.approx(2.4)
    assert sv.sm  == pytest.approx(0.28)
    for var in STATE_VARIABLES:
        if var not in ("lai", "sm"):
            assert getattr(sv, var) is None, f"{var} should be None"


def test_from_numpy_wrong_shape():
    """SV-15: from_numpy() raises ValueError for wrong array shape."""
    with pytest.raises(ValueError, match="shape"):
        StateVector.from_numpy(np.zeros(5))

    with pytest.raises(ValueError, match="shape"):
        StateVector.from_numpy(np.zeros((STATE_DIM, 2)))  # 2-D array


# ═══════════════════════════════════════════════════════════════════════════════
# SV-16 through SV-17 — from_daily_output()
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_daily_output():
    """SV-16: from_daily_output() correctly maps all DailyOutput fields."""
    mock_row = _make_daily_output_mock()
    sv = StateVector.from_daily_output(mock_row)

    assert sv.date == TODAY
    for var, expected in FULL_VALUES.items():
        assert getattr(sv, var) == pytest.approx(expected), f"{var} mismatch"


def test_from_daily_output_none_fields():
    """SV-17: from_daily_output() preserves None for missing WOFOST outputs."""
    mock_row = _make_daily_output_mock(twso=None, tagp=None, rftra=None)
    sv = StateVector.from_daily_output(mock_row)

    assert sv.twso  is None
    assert sv.tagp  is None
    assert sv.rftra is None
    # Other fields populated
    assert sv.lai == pytest.approx(FULL_VALUES["lai"])
    assert sv.sm  == pytest.approx(FULL_VALUES["sm"])


# ═══════════════════════════════════════════════════════════════════════════════
# SV-18 through SV-20 — to_dict()
# ═══════════════════════════════════════════════════════════════════════════════

def test_to_dict_full():
    """SV-18: to_dict() returns all 11 keys (date + 10 variables) with correct values."""
    sv = _full_sv()
    d = sv.to_dict()

    assert set(d.keys()) == {"date"} | set(STATE_VARIABLES)
    assert d["date"] == "2024-03-15"
    for var, expected in FULL_VALUES.items():
        assert d[var] == pytest.approx(expected), f"{var} mismatch"


def test_to_dict_none_fields():
    """SV-19: to_dict() preserves None for unpopulated variables."""
    sv = StateVector(date=TODAY, lai=2.4)
    d = sv.to_dict()
    assert d["lai"] == pytest.approx(2.4)
    for var in STATE_VARIABLES:
        if var != "lai":
            assert d[var] is None, f"{var} should be None in dict"


def test_to_dict_date_iso_string():
    """SV-20: to_dict() serialises date as ISO 8601 string; None date → None."""
    sv_with_date    = StateVector(date=datetime.date(2024, 3, 15))
    sv_without_date = StateVector()

    assert sv_with_date.to_dict()["date"]    == "2024-03-15"
    assert sv_without_date.to_dict()["date"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# SV-21 through SV-23 — Introspection properties
# ═══════════════════════════════════════════════════════════════════════════════

def test_populated_variables():
    """SV-21: populated_variables lists only non-None fields."""
    sv = StateVector(lai=2.4, sm=0.28, dvs=0.55)
    assert set(sv.populated_variables) == {"lai", "sm", "dvs"}

    sv_empty = StateVector()
    assert sv_empty.populated_variables == []

    sv_full = _full_sv()
    assert set(sv_full.populated_variables) == set(STATE_VARIABLES)


def test_is_complete():
    """SV-22: is_complete is True only when all 10 variables are populated."""
    assert _full_sv().is_complete is True
    assert StateVector(lai=2.4).is_complete is False
    assert StateVector().is_complete is False


def test_missing_variables():
    """SV-23: missing_variables lists only None fields."""
    sv = StateVector(lai=2.4, sm=0.28)
    missing = sv.missing_variables
    for var in STATE_VARIABLES:
        if var in ("lai", "sm"):
            assert var not in missing
        else:
            assert var in missing

    assert _full_sv().missing_variables == []
    assert StateVector().missing_variables == list(STATE_VARIABLES)


# ═══════════════════════════════════════════════════════════════════════════════
# SV-24 through SV-25 — get()
# ═══════════════════════════════════════════════════════════════════════════════

def test_get_valid():
    """SV-24: get() returns the correct value for a valid variable name."""
    sv = StateVector(lai=2.4, dvs=0.55)
    assert sv.get("lai") == pytest.approx(2.4)
    assert sv.get("dvs") == pytest.approx(0.55)
    assert sv.get("sm")  is None  # valid key, but None value


def test_get_invalid_key():
    """SV-25: get() raises KeyError for a variable not in STATE_VARIABLES."""
    sv = StateVector(lai=2.4)
    with pytest.raises(KeyError):
        sv.get("CANOPY_TEMPERATURE")
    with pytest.raises(KeyError):
        sv.get("LAI")  # case-sensitive — must be lowercase


# ═══════════════════════════════════════════════════════════════════════════════
# SV-26 — __repr__
# ═══════════════════════════════════════════════════════════════════════════════

def test_repr():
    """SV-26: repr shows date, key variable values, and population count."""
    sv = _full_sv()
    r = repr(sv)
    assert "StateVector" in r
    assert "2024-03-15" in r
    assert "10/10" in r

    sv_partial = StateVector(lai=2.4, sm=0.28)
    r2 = repr(sv_partial)
    assert "2/10" in r2
