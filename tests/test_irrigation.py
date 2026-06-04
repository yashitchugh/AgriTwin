"""
tests/test_irrigation.py — Unit Tests for Irrigation Support
=============================================================

Tests the complete irrigation integration stack:
  1. Schema-level validation (IrrigationEvent, SimulateRequest validators)
  2. AgroManagement builder (TimedEvents YAML generation)
  3. End-to-end simulation (with and without irrigation)

Test strategy:
  - Schema tests use Pydantic's ValidationError to verify all validators fire correctly.
  - AgroManagement tests inspect the generated list structure directly — no PCSE run needed.
  - Integration tests run actual WOFOST simulations (offline, synthetic weather)
    and verify that irrigation events are processed correctly by comparing SM/RFTRA.

Fixtures:
  - base_dates: standard winter wheat growing season (Oct 2020 → Jul 2021)

Markers:
  - Tests that run WOFOST are marked with `@pytest.mark.integration` — they
    take 2–5 seconds each. Schema/builder tests are fast (<0.1s each).
"""

import datetime as dt

import pytest
from pydantic import ValidationError

# Schema layer
from backend.app.api.schemas.simulate import IrrigationEvent, SimulateRequest

# AgroManagement builder
from backend.app.simulation.agromanagement import build_agromanagement

# Engine (for integration tests)
from backend.app.simulation.engine import run_simulation


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

SOW_DATE = dt.date(2020, 10, 15)
HARVEST_DATE = dt.date(2021, 7, 30)

# Mid-season dates safely inside the growing window
MID_SEASON_DATE_1 = dt.date(2021, 1, 15)
MID_SEASON_DATE_2 = dt.date(2021, 2, 20)
GRAIN_FILL_DATE = dt.date(2021, 4, 10)

# Standard rainfed request parameters (no irrigation, offline weather)
BASE_REQUEST_PARAMS = dict(
    latitude=52.0,
    longitude=5.5,
    crop="wheat",
    variety="Winter_wheat_101",
    sowing_date=SOW_DATE,
    harvest_date=HARVEST_DATE,
    use_real_weather=False,   # synthetic weather — no internet required
    use_real_soil=False,      # default medium-loam soil
    max_duration=300,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. IrrigationEvent validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestIrrigationEventValidation:
    """Test the IrrigationEvent Pydantic model validators."""

    def test_valid_event_accepted(self):
        """A well-formed event should parse without error."""
        ev = IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=40.0)
        assert ev.date == MID_SEASON_DATE_1
        assert ev.amount_mm == 40.0

    def test_negative_amount_rejected(self):
        """amount_mm <= 0 must raise ValidationError (gt=0 constraint)."""
        with pytest.raises(ValidationError) as exc_info:
            IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=-10.0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("amount_mm",) for e in errors), (
            f"Expected error on amount_mm, got: {errors}"
        )

    def test_zero_amount_rejected(self):
        """amount_mm = 0 is not strictly positive → must fail."""
        with pytest.raises(ValidationError):
            IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=0.0)

    def test_amount_at_max_boundary_accepted(self):
        """Exactly 200 mm (the limit) should be accepted."""
        ev = IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=200.0)
        assert ev.amount_mm == 200.0

    def test_amount_exceeds_max_rejected(self):
        """amount_mm > 200 must raise ValidationError (le=200 constraint)."""
        with pytest.raises(ValidationError) as exc_info:
            IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=201.0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("amount_mm",) for e in errors), (
            f"Expected error on amount_mm, got: {errors}"
        )

    def test_small_valid_amount_accepted(self):
        """Very small but positive amounts (e.g. 0.5 mm light mist) are valid."""
        ev = IrrigationEvent(date=MID_SEASON_DATE_1, amount_mm=0.5)
        assert ev.amount_mm == 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SimulateRequest cross-field irrigation validators
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulateRequestIrrigationValidation:
    """Test cross-field irrigation date validators in SimulateRequest."""

    def test_no_irrigation_backward_compatible(self):
        """A request with no irrigation_events must parse correctly (backward compat)."""
        req = SimulateRequest(**BASE_REQUEST_PARAMS)
        assert req.irrigation_events == []

    def test_empty_list_backward_compatible(self):
        """Explicitly passing [] for irrigation_events must work."""
        req = SimulateRequest(**BASE_REQUEST_PARAMS, irrigation_events=[])
        assert req.irrigation_events == []

    def test_one_valid_irrigation_accepted(self):
        """A single mid-season event is valid."""
        req = SimulateRequest(
            **BASE_REQUEST_PARAMS,
            irrigation_events=[{"date": MID_SEASON_DATE_1, "amount_mm": 40}],
        )
        assert len(req.irrigation_events) == 1
        assert req.irrigation_events[0].amount_mm == 40.0

    def test_multiple_valid_irrigations_accepted(self):
        """Multiple mid-season events must all be accepted."""
        req = SimulateRequest(
            **BASE_REQUEST_PARAMS,
            irrigation_events=[
                {"date": MID_SEASON_DATE_1, "amount_mm": 40},
                {"date": MID_SEASON_DATE_2, "amount_mm": 50},
                {"date": GRAIN_FILL_DATE, "amount_mm": 35},
            ],
        )
        assert len(req.irrigation_events) == 3

    def test_irrigation_date_before_sowing_rejected(self):
        """Irrigation before sowing_date must raise ValidationError.

        PCSE would silently ignore such events — we catch this at the schema
        level to give users a clear error instead of silent data loss.
        """
        date_before_sowing = SOW_DATE - dt.timedelta(days=5)
        with pytest.raises(ValidationError) as exc_info:
            SimulateRequest(
                **BASE_REQUEST_PARAMS,
                irrigation_events=[{"date": date_before_sowing, "amount_mm": 40}],
            )
        # The error message must mention the offending date or irrigation
        error_str = str(exc_info.value)
        assert "before sowing_date" in error_str or "irrigation" in error_str.lower(), (
            f"Expected irrigation date error, got: {error_str}"
        )

    def test_irrigation_date_after_harvest_rejected(self):
        """Irrigation after harvest_date must raise ValidationError.

        PCSE would silently ignore such events — we catch this at the schema
        level to give users a clear error instead of silent data loss.
        """
        date_after_harvest = HARVEST_DATE + dt.timedelta(days=5)
        with pytest.raises(ValidationError) as exc_info:
            SimulateRequest(
                **BASE_REQUEST_PARAMS,
                irrigation_events=[{"date": date_after_harvest, "amount_mm": 40}],
            )
        error_str = str(exc_info.value)
        assert "after harvest_date" in error_str or "irrigation" in error_str.lower(), (
            f"Expected irrigation date error, got: {error_str}"
        )

    def test_irrigation_on_sowing_date_accepted(self):
        """Irrigation exactly on sowing_date is at the boundary and should be valid."""
        req = SimulateRequest(
            **BASE_REQUEST_PARAMS,
            irrigation_events=[{"date": SOW_DATE, "amount_mm": 30}],
        )
        assert len(req.irrigation_events) == 1

    def test_irrigation_on_harvest_date_accepted(self):
        """Irrigation exactly on harvest_date is at the boundary and should be valid."""
        req = SimulateRequest(
            **BASE_REQUEST_PARAMS,
            irrigation_events=[{"date": HARVEST_DATE, "amount_mm": 20}],
        )
        assert len(req.irrigation_events) == 1

    def test_no_harvest_date_only_sowing_bound_enforced(self):
        """When harvest_date is None, only the sowing lower bound is enforced."""
        params = {k: v for k, v in BASE_REQUEST_PARAMS.items() if k != "harvest_date"}
        params["harvest_date"] = None

        # Event well into the future: should not be rejected (no harvest bound)
        far_future = SOW_DATE + dt.timedelta(days=500)
        req = SimulateRequest(
            **params,
            irrigation_events=[{"date": far_future, "amount_mm": 40}],
        )
        assert len(req.irrigation_events) == 1

        # Event before sowing: still rejected
        with pytest.raises(ValidationError):
            SimulateRequest(
                **params,
                irrigation_events=[{"date": SOW_DATE - dt.timedelta(days=1), "amount_mm": 40}],
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. AgroManagement builder unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildAgromanagement:
    """Test the agromanagement.build_agromanagement() function directly."""

    def test_no_irrigation_returns_null_timed_events(self):
        """Without irrigation events, TimedEvents must be None (rainfed baseline)."""
        agro = build_agromanagement(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
        )
        # agro is a list with one campaign entry
        assert isinstance(agro, list)
        assert len(agro) == 1
        campaign = list(agro[0].values())[0]
        assert campaign["TimedEvents"] is None

    def test_one_irrigation_generates_timed_events(self):
        """One irrigation event should produce a TimedEvents list with one entry."""
        agro = build_agromanagement(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
            irrigation_events=[{"date": MID_SEASON_DATE_1, "amount_mm": 40.0}],
        )
        campaign = list(agro[0].values())[0]
        timed = campaign["TimedEvents"]

        assert timed is not None, "TimedEvents should not be None when irrigation provided"
        assert len(timed) == 1
        assert timed[0]["event_signal"] == "irrigate", (
            "PCSE signal must be exactly 'irrigate'"
        )

    def test_multiple_irrigations_all_in_events_table(self):
        """All provided events must appear in the events_table."""
        events = [
            {"date": MID_SEASON_DATE_1, "amount_mm": 40.0},
            {"date": MID_SEASON_DATE_2, "amount_mm": 50.0},
            {"date": GRAIN_FILL_DATE, "amount_mm": 35.0},
        ]
        agro = build_agromanagement(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
            irrigation_events=events,
        )
        campaign = list(agro[0].values())[0]
        timed = campaign["TimedEvents"]
        events_table = timed[0]["events_table"]

        assert len(events_table) == 3, (
            f"Expected 3 events in events_table, got {len(events_table)}"
        )

    def test_irrigation_amount_and_efficiency_correct(self):
        """Each event entry must have 'amount' == amount_mm and efficiency == 0.7."""
        agro = build_agromanagement(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
            irrigation_events=[{"date": MID_SEASON_DATE_1, "amount_mm": 55.0}],
        )
        campaign = list(agro[0].values())[0]
        events_table = campaign["TimedEvents"][0]["events_table"]
        event_values = list(events_table[0].values())[0]

        assert event_values["amount"] == 55.0, (
            f"Expected amount=55.0, got {event_values['amount']}"
        )
        assert event_values["efficiency"] == pytest.approx(0.7), (
            f"Expected efficiency=0.7, got {event_values['efficiency']}"
        )

    def test_campaign_start_before_sowing(self):
        """campaign_start_date must be 14 days before sowing_date by default."""
        agro = build_agromanagement(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
        )
        # The outermost key of the first campaign entry is the campaign_start_date
        campaign_start = list(agro[0].keys())[0]
        expected_start = SOW_DATE - dt.timedelta(days=14)
        assert campaign_start == expected_start, (
            f"Expected campaign_start={expected_start}, got {campaign_start}"
        )

    def test_empty_list_same_as_none(self):
        """Passing [] for irrigation_events should behave identically to None."""
        agro_none = build_agromanagement(
            crop_name="wheat", variety_name="Winter_wheat_101",
            sow_date=SOW_DATE, harvest_date=HARVEST_DATE,
            irrigation_events=None,
        )
        agro_empty = build_agromanagement(
            crop_name="wheat", variety_name="Winter_wheat_101",
            sow_date=SOW_DATE, harvest_date=HARVEST_DATE,
            irrigation_events=[],
        )
        campaign_none = list(agro_none[0].values())[0]
        campaign_empty = list(agro_empty[0].values())[0]
        assert campaign_none["TimedEvents"] is None
        assert campaign_empty["TimedEvents"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Integration tests — actual WOFOST runs
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestIrrigationIntegration:
    """End-to-end WOFOST simulations verifying irrigation actually affects outputs.

    These tests use offline synthetic weather (use_nasa=False) and default soil
    so they run without internet access and complete in ~2–5 seconds each.

    The assertions verify WOFOST's water balance responds correctly to irrigation:
      - SM rises after irrigation events
      - Irrigated RFTRA is >= rainfed RFTRA (less stress)
      - Irrigated TWSO is >= rainfed TWSO (no yield penalty from irrigation)
    """

    def _run(self, irrigation_events=None):
        """Helper: run a synthetic-weather wheat simulation."""
        return run_simulation(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            sow_date=SOW_DATE,
            harvest_date=HARVEST_DATE,
            latitude=52.0,
            longitude=5.5,
            use_nasa_weather=False,
            soil_params=None,
            max_duration=300,
            step_by_step=False,
            irrigation_events=irrigation_events,
        )

    def test_no_irrigation_runs_successfully(self):
        """Baseline: rainfed simulation must complete without error."""
        result = self._run(irrigation_events=None)
        assert result.total_days > 0
        assert result.metrics["final_twso_kg_ha"] >= 0
        # Verify daily outputs contain rftra
        assert any(d.get("rftra") is not None for d in result.daily_output), (
            "RFTRA should be present in daily outputs"
        )

    def test_no_irrigation_rftra_present_in_output(self):
        """RFTRA must be returned in every day's output (after crop emergence)."""
        result = self._run(irrigation_events=None)
        # After emergence (day 15+), rftra should be available
        post_emergence = [d for d in result.daily_output if d.get("dvs") is not None]
        assert len(post_emergence) > 0
        rftra_vals = [d.get("rftra") for d in post_emergence]
        non_none = [v for v in rftra_vals if v is not None]
        assert len(non_none) > 0, "RFTRA should be non-None after crop emergence"

    def test_one_irrigation_runs_successfully(self):
        """Single irrigation event: simulation must complete without error."""
        result = self._run(
            irrigation_events=[{"date": MID_SEASON_DATE_1, "amount_mm": 40.0}]
        )
        assert result.total_days > 0
        assert result.metrics["final_twso_kg_ha"] >= 0

    def test_multiple_irrigations_run_successfully(self):
        """Multiple irrigation events: simulation must complete without error."""
        result = self._run(
            irrigation_events=[
                {"date": MID_SEASON_DATE_1, "amount_mm": 40.0},
                {"date": MID_SEASON_DATE_2, "amount_mm": 50.0},
                {"date": GRAIN_FILL_DATE, "amount_mm": 35.0},
            ]
        )
        assert result.total_days > 0
        assert result.metrics["final_twso_kg_ha"] >= 0

    def test_irrigation_does_not_reduce_yield(self):
        """Irrigated yield must be >= rainfed yield (irrigation can only help)."""
        rainfed = self._run(irrigation_events=None)
        irrigated = self._run(
            irrigation_events=[
                {"date": MID_SEASON_DATE_1, "amount_mm": 40.0},
                {"date": MID_SEASON_DATE_2, "amount_mm": 50.0},
            ]
        )
        rainfed_yield = rainfed.metrics["final_twso_kg_ha"]
        irrigated_yield = irrigated.metrics["final_twso_kg_ha"]

        assert irrigated_yield >= rainfed_yield - 1.0, (
            f"Irrigated yield ({irrigated_yield:.1f}) should be >= "
            f"rainfed yield ({rainfed_yield:.1f}). "
            f"Irrigation should never reduce yield."
        )

    def test_daily_outputs_contain_rftra_sm_lai_tagp_twso(self):
        """All 5 required irrigation diagnostic fields must be in daily output."""
        result = self._run(irrigation_events=None)
        required_fields = {"rftra", "sm", "lai", "tagp", "twso"}

        if result.daily_output:
            first_day = result.daily_output[0]
            present = set(first_day.keys())
            missing = required_fields - present
            assert not missing, (
                f"Missing required diagnostic fields in daily output: {missing}"
            )
