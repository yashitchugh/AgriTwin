"""
tests/test_twin.py — Digital Twin State Abstraction Tests
==========================================================

Tests for:
  A. FieldState creation (direct construction)
  B. FieldState.from_daily_output()
  C. FieldState.from_simulation()
  D. Extended DailyOutput persistence (wlv, wst, wrt, wso columns)
  E. Extended DailyStateRecord API schema
  F. No regression — existing simulation endpoints still work
"""

import datetime as dt
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.twin.field_state import FieldState
from backend.app.models.daily_output import DailyOutput
from backend.app.models.simulation_run import SimulationRun

from tests.conftest import SIMULATE_PAYLOAD


# ═══════════════════════════════════════════════════════════════════════════════
# A. FieldState — Direct Construction
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldStateConstruction:
    """Verify FieldState can be created and represents a valid twin state."""

    def test_default_construction_all_none(self):
        """A FieldState with no arguments has all variables as None."""
        state = FieldState()
        assert state.field_id is None
        assert state.simulation_id is None
        assert state.current_date is None
        assert state.lai is None
        assert state.sm is None
        assert state.tagp is None
        assert state.twso is None
        assert state.dvs is None
        assert state.rd is None
        assert state.rftra is None
        assert state.tra is None
        assert state.evs is None
        assert state.twlv is None
        assert state.twst is None
        assert state.twrt is None
        assert state.wlv is None
        assert state.wst is None
        assert state.wrt is None
        assert state.wso is None

    def test_source_defaults_to_unknown(self):
        state = FieldState()
        assert state.source == "unknown"

    def test_updated_at_is_set_automatically(self):
        state = FieldState()
        assert state.updated_at is not None
        assert isinstance(state.updated_at, dt.datetime)

    def test_explicit_construction(self):
        fid = uuid.uuid4()
        sid = uuid.uuid4()
        today = dt.date.today()
        state = FieldState(
            field_id=fid, simulation_id=sid,
            current_date=today,
            lai=2.5, sm=0.25, tagp=3000.0, twso=800.0,
            dvs=1.2, rd=80.0,
            rftra=0.85, tra=0.3, evs=0.05,
            twlv=1200.0, twst=900.0, twrt=500.0,
            wlv=950.0, wst=750.0, wrt=400.0, wso=650.0,
            source="simulation",
        )
        assert state.field_id == fid
        assert state.simulation_id == sid
        assert state.current_date == today
        assert state.lai == 2.5
        assert state.sm == 0.25
        assert state.twso == 800.0
        assert state.dvs == 1.2
        assert state.rftra == 0.85
        assert state.twlv == 1200.0
        assert state.wlv == 950.0
        assert state.wso == 650.0
        assert state.source == "simulation"

    def test_is_water_stressed_when_rftra_below_1(self):
        state = FieldState(rftra=0.7)
        assert state.is_water_stressed is True

    def test_is_not_water_stressed_when_rftra_equals_1(self):
        state = FieldState(rftra=1.0)
        assert state.is_water_stressed is False

    def test_is_not_water_stressed_when_rftra_is_none(self):
        state = FieldState(rftra=None)
        assert state.is_water_stressed is False

    def test_has_live_state_false_when_all_none(self):
        state = FieldState(wlv=None, wst=None, wrt=None, wso=None)
        assert state.has_live_state is False

    def test_has_live_state_true_when_any_set(self):
        state = FieldState(wlv=500.0)
        assert state.has_live_state is True

    def test_to_dict_returns_all_keys(self):
        state = FieldState(lai=2.0, sm=0.3, source="simulation")
        d = state.to_dict()
        required_keys = {
            "field_id", "simulation_id", "current_date", "source", "updated_at",
            "lai", "sm", "tagp", "twso", "dvs", "rd",
            "rftra", "tra", "evs",
            "twlv", "twst", "twrt",
            "wlv", "wst", "wrt", "wso",
        }
        assert required_keys.issubset(d.keys())
        assert d["lai"] == 2.0
        assert d["sm"] == 0.3
        assert d["source"] == "simulation"

    def test_repr_contains_key_fields(self):
        state = FieldState(dvs=1.5, lai=3.2, sm=0.2, twso=1500.0, source="simulation")
        r = repr(state)
        assert "FieldState" in r
        assert "dvs=" in r
        assert "lai=" in r
        assert "sm=" in r
        assert "twso=" in r


# ═══════════════════════════════════════════════════════════════════════════════
# B. FieldState.from_daily_output()
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldStateFromDailyOutput:
    """Tests for FieldState.from_daily_output() factory."""

    def _make_row(self, **kwargs) -> DailyOutput:
        """Build a minimal mock DailyOutput with all columns set."""
        defaults = dict(
            simulation_run_id=uuid.uuid4(),
            date=dt.date(2021, 3, 15),
            dvs=1.2, lai=3.0, sm=0.22,
            tagp=5000.0, twso=1200.0,
            twlv=1800.0, twst=1400.0, twrt=600.0,
            rftra=0.9, tra=0.4, evs=0.1, rd=80.0,
            wlv=None, wst=None, wrt=None, wso=None,
        )
        defaults.update(kwargs)
        row = MagicMock(spec=DailyOutput)
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    def test_source_is_daily_output(self):
        row = self._make_row()
        state = FieldState.from_daily_output(row)
        assert state.source == "daily_output"

    def test_simulation_id_matches_row(self):
        sid = uuid.uuid4()
        row = self._make_row(simulation_run_id=sid)
        state = FieldState.from_daily_output(row)
        assert state.simulation_id == sid

    def test_date_is_transferred(self):
        row = self._make_row(date=dt.date(2021, 5, 10))
        state = FieldState.from_daily_output(row)
        assert state.current_date == dt.date(2021, 5, 10)

    def test_field_id_kwarg(self):
        fid = uuid.uuid4()
        row = self._make_row()
        state = FieldState.from_daily_output(row, field_id=fid)
        assert state.field_id == fid

    def test_core_variables_populated(self):
        row = self._make_row(lai=2.8, sm=0.19, tagp=4500.0, twso=900.0)
        state = FieldState.from_daily_output(row)
        assert state.lai == 2.8
        assert state.sm == 0.19
        assert state.tagp == 4500.0
        assert state.twso == 900.0

    def test_stress_variables_populated(self):
        row = self._make_row(rftra=0.75, tra=0.32, evs=0.08)
        state = FieldState.from_daily_output(row)
        assert state.rftra == 0.75
        assert state.tra == 0.32
        assert state.evs == 0.08

    def test_cumulative_biomass_populated(self):
        row = self._make_row(twlv=1500.0, twst=1100.0, twrt=550.0)
        state = FieldState.from_daily_output(row)
        assert state.twlv == 1500.0
        assert state.twst == 1100.0
        assert state.twrt == 550.0

    def test_live_state_none_for_batch_rows(self):
        """In batch mode all wlv/wst/wrt/wso are None — from_daily_output preserves this."""
        row = self._make_row(wlv=None, wst=None, wrt=None, wso=None)
        state = FieldState.from_daily_output(row)
        assert state.wlv is None
        assert state.wst is None
        assert state.wrt is None
        assert state.wso is None
        assert state.has_live_state is False

    def test_live_state_populated_for_step_by_step_rows(self):
        """When live-state columns are set (future step-by-step mode), they transfer correctly."""
        row = self._make_row(wlv=850.0, wst=720.0, wrt=380.0, wso=520.0)
        state = FieldState.from_daily_output(row)
        assert state.wlv == 850.0
        assert state.wst == 720.0
        assert state.wrt == 380.0
        assert state.wso == 520.0
        assert state.has_live_state is True

    def test_is_water_stressed_from_row(self):
        row = self._make_row(rftra=0.6)
        state = FieldState.from_daily_output(row)
        assert state.is_water_stressed is True


# ═══════════════════════════════════════════════════════════════════════════════
# C. FieldState.from_simulation()
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldStateFromSimulation:
    """Tests for FieldState.from_simulation() factory."""

    def _make_result(self, n_days: int = 5) -> object:
        """Build a mock SimulationResult with n_days of daily output."""
        result = MagicMock()
        base = dt.date(2020, 10, 15)
        result.daily_output = [
            {
                "date": (base + dt.timedelta(days=i)).isoformat(),
                "dvs": float(i) * 0.01,
                "lai": float(i) * 0.1,
                "sm": 0.22 - float(i) * 0.005,
                "tagp": float(i) * 50.0,
                "twso": float(i) * 10.0,
                "twlv": float(i) * 20.0,
                "twst": float(i) * 15.0,
                "twrt": float(i) * 8.0,
                "rftra": min(1.0, 0.8 + float(i) * 0.02),
                "tra": 0.2 + float(i) * 0.01,
                "evs": None,  # None in batch mode
                "rd": 10.0 + float(i) * 2.0,
                "wlv": None, "wst": None, "wrt": None, "wso": None,
            }
            for i in range(n_days)
        ]
        return result

    def test_source_is_simulation(self):
        result = self._make_result()
        state = FieldState.from_simulation(result)
        assert state.source == "simulation"

    def test_uses_last_day_by_default(self):
        result = self._make_result(n_days=5)
        state = FieldState.from_simulation(result)
        expected_date = dt.date(2020, 10, 15) + dt.timedelta(days=4)
        assert state.current_date == expected_date

    def test_specific_date_selection(self):
        result = self._make_result(n_days=5)
        target = dt.date(2020, 10, 17)
        state = FieldState.from_simulation(result, date=target)
        assert state.current_date == target

    def test_unknown_date_falls_back_to_last(self):
        result = self._make_result(n_days=5)
        far_future = dt.date(2099, 1, 1)
        state = FieldState.from_simulation(result, date=far_future)
        # Falls back to last day
        expected_date = dt.date(2020, 10, 15) + dt.timedelta(days=4)
        assert state.current_date == expected_date

    def test_field_id_and_simulation_id_attached(self):
        result = self._make_result()
        fid = uuid.uuid4()
        sid = uuid.uuid4()
        state = FieldState.from_simulation(result, field_id=fid, simulation_id=sid)
        assert state.field_id == fid
        assert state.simulation_id == sid

    def test_core_variables_from_simulation(self):
        result = self._make_result(n_days=3)
        state = FieldState.from_simulation(result)
        # Last record: i=2
        assert state.dvs == pytest.approx(0.02, abs=1e-6)
        assert state.lai == pytest.approx(0.2, abs=1e-6)
        assert state.sm == pytest.approx(0.22 - 2 * 0.005, abs=1e-6)
        assert state.twso == pytest.approx(20.0, abs=1e-6)

    def test_cumulative_biomass_from_simulation(self):
        result = self._make_result(n_days=3)
        state = FieldState.from_simulation(result)
        # i=2: twlv=40.0, twst=30.0, twrt=16.0
        assert state.twlv == pytest.approx(40.0, abs=1e-6)
        assert state.twst == pytest.approx(30.0, abs=1e-6)
        assert state.twrt == pytest.approx(16.0, abs=1e-6)

    def test_live_state_none_in_batch_result(self):
        result = self._make_result()
        state = FieldState.from_simulation(result)
        assert state.wlv is None
        assert state.wst is None
        assert state.wrt is None
        assert state.wso is None
        assert state.has_live_state is False

    def test_empty_result_returns_partial_state(self):
        result = MagicMock()
        result.daily_output = []
        state = FieldState.from_simulation(result)
        assert state.source == "simulation"
        assert state.current_date is None
        assert state.lai is None


# ═══════════════════════════════════════════════════════════════════════════════
# D. Extended DailyOutput Persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedDailyOutputPersistence:
    """Verify that the new wlv/wst/wrt/wso columns round-trip through the DB."""

    def test_daily_output_has_new_columns(self):
        """DailyOutput ORM model must expose all 4 new column attributes."""
        row = DailyOutput()
        assert hasattr(row, "wlv")
        assert hasattr(row, "wst")
        assert hasattr(row, "wrt")
        assert hasattr(row, "wso")

    def test_new_columns_default_to_none(self):
        """Unset new columns must default to None."""
        row = DailyOutput()
        assert row.wlv is None
        assert row.wst is None
        assert row.wrt is None
        assert row.wso is None

    def test_null_values_persist_correctly(self, test_engine):
        """Batch-mode rows with NULL live-state columns save and reload cleanly."""
        sim_id = uuid.uuid4()
        with Session(test_engine) as db:
            run = SimulationRun(
                id=sim_id, run_type="baseline", status="completed",
                model_name="Wofost72_WLP_FD", model_version="7.2",
                latitude=52.0, longitude=5.5, crop="wheat", variety="apache",
                sowing_date=dt.date(2020, 10, 15),
                use_real_weather=False, use_real_soil=False,
            )
            db.add(run)
            db.flush()
            row = DailyOutput(
                simulation_run_id=sim_id,
                date=dt.date(2020, 10, 15),
                dvs=0.0, lai=0.0, sm=0.22,
                tagp=0.0, twso=0.0,
                twlv=0.0, twst=0.0, twrt=0.0,
                rftra=1.0, tra=0.1, evs=0.05, rd=10.0,
                # New live-state columns — NULL in batch mode
                wlv=None, wst=None, wrt=None, wso=None,
            )
            db.add(row)
            db.commit()

        with Session(test_engine) as db:
            loaded = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == sim_id)
            ).scalars().first()
            assert loaded is not None
            assert loaded.wlv is None
            assert loaded.wst is None
            assert loaded.wrt is None
            assert loaded.wso is None

    def test_live_state_values_persist_correctly(self, test_engine):
        """Live-state values (future step-by-step mode) can be stored and retrieved."""
        sim_id = uuid.uuid4()
        with Session(test_engine) as db:
            run = SimulationRun(
                id=sim_id, run_type="baseline", status="completed",
                model_name="Wofost72_WLP_FD", model_version="7.2",
                latitude=52.0, longitude=5.5, crop="wheat", variety="apache",
                sowing_date=dt.date(2020, 10, 15),
                use_real_weather=False, use_real_soil=False,
            )
            db.add(run)
            db.flush()
            row = DailyOutput(
                simulation_run_id=sim_id,
                date=dt.date(2020, 10, 20),
                dvs=0.05, lai=0.3, sm=0.21,
                tagp=100.0, twso=0.0,
                twlv=60.0, twst=40.0, twrt=30.0,
                rftra=1.0, tra=0.2, evs=0.06, rd=15.0,
                # Simulated step-by-step values
                wlv=55.0, wst=38.0, wrt=28.0, wso=0.0,
            )
            db.add(row)
            db.commit()

        with Session(test_engine) as db:
            loaded = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == sim_id)
            ).scalars().first()
            assert loaded.wlv == pytest.approx(55.0)
            assert loaded.wst == pytest.approx(38.0)
            assert loaded.wrt == pytest.approx(28.0)
            assert loaded.wso == pytest.approx(0.0)

    def test_from_daily_output_round_trips_through_db(self, test_engine):
        """FieldState.from_daily_output() works correctly with a DB-loaded row."""
        sim_id = uuid.uuid4()
        fid = uuid.uuid4()
        with Session(test_engine) as db:
            run = SimulationRun(
                id=sim_id, run_type="baseline", status="completed",
                model_name="Wofost72_WLP_FD", model_version="7.2",
                latitude=52.0, longitude=5.5, crop="wheat", variety="apache",
                sowing_date=dt.date(2020, 10, 15),
                use_real_weather=False, use_real_soil=False,
            )
            db.add(run)
            db.flush()
            db.add(DailyOutput(
                simulation_run_id=sim_id, date=dt.date(2021, 3, 15),
                dvs=1.5, lai=2.0, sm=0.18, tagp=6000.0, twso=2000.0,
                twlv=1500.0, twst=1200.0, twrt=700.0,
                rftra=0.8, tra=0.35, evs=0.04, rd=90.0,
                wlv=None, wst=None, wrt=None, wso=None,
            ))
            db.commit()

        with Session(test_engine) as db:
            row = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == sim_id)
            ).scalars().first()
            state = FieldState.from_daily_output(row, field_id=fid)

        assert state.source == "daily_output"
        assert state.simulation_id == sim_id
        assert state.field_id == fid
        assert state.dvs == pytest.approx(1.5)
        assert state.lai == pytest.approx(2.0)
        assert state.twso == pytest.approx(2000.0)
        assert state.twlv == pytest.approx(1500.0)
        assert state.rftra == pytest.approx(0.8)
        assert state.wlv is None
        assert state.has_live_state is False


# ═══════════════════════════════════════════════════════════════════════════════
# E. API schema includes new fields
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedDailyStateSchema:
    """DailyStateRecord API schema must include all new fields."""

    def test_daily_state_record_has_new_fields(self):
        from backend.app.api.schemas.simulation import DailyStateRecord
        record = DailyStateRecord(
            date=dt.date(2021, 3, 1),
            dvs=1.2, lai=3.0, sm=0.2, tagp=5000.0, twso=1500.0,
            twlv=1800.0, twst=1400.0, twrt=600.0,
            rftra=0.9, tra=0.35, evs=0.05, rd=85.0,
            wlv=None, wst=None, wrt=None, wso=None,
        )
        assert record.twlv == 1800.0
        assert record.twst == 1400.0
        assert record.twrt == 600.0
        assert record.wlv is None
        assert record.wso is None

    def test_daily_state_schema_has_wlv_in_simulate_response(self):
        from backend.app.api.schemas.simulate import DailyState
        state = DailyState(
            date="2021-03-01",
            lai=2.5, sm=0.22, tagp=4000.0, twso=1200.0,
            rftra=0.95, dvs=1.3, tra=0.3, rd=80.0,
            evs=0.04,
            twlv=1500.0, twst=1100.0, twrt=500.0,
            wlv=None, wst=None, wrt=None, wso=None,
        )
        assert state.twlv == 1500.0
        assert state.wlv is None
        assert state.wso is None


# ═══════════════════════════════════════════════════════════════════════════════
# F. No-regression: existing endpoints still work
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoRegression:
    """Verify all existing simulation endpoints continue to work after additions."""

    def test_post_simulate_still_returns_200(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        assert resp.status_code == 200

    def test_post_simulate_still_has_simulation_id(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        assert resp.json()["simulation_id"] is not None

    def test_post_simulate_daily_states_have_new_fields(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        data = resp.json()
        daily = data.get("daily_states", [])
        assert len(daily) > 0
        first = daily[0]
        # New fields must be present (None for batch mode)
        assert "twlv" in first
        assert "twst" in first
        assert "twrt" in first
        assert "wlv" in first
        assert "wst" in first
        assert "wrt" in first
        assert "wso" in first
        assert "evs" in first
        # Batch mode → live-state are None
        assert first["wlv"] is None
        assert first["wso"] is None

    def test_get_simulations_still_works(self, client):
        client.post("/simulate", json=SIMULATE_PAYLOAD)
        resp = client.get("/simulations")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_get_simulation_detail_has_extended_daily_states(self, client):
        post = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = post.json()["simulation_id"]
        resp = client.get(f"/simulations/{sim_id}")
        assert resp.status_code == 200
        daily = resp.json()["daily_states"]
        assert len(daily) > 0
        first = daily[0]
        assert "wlv" in first
        assert "wst" in first
        assert "wrt" in first
        assert "wso" in first
        # Batch mode → all None
        assert first["wlv"] is None

    def test_delete_simulation_still_works(self, client):
        post = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = post.json()["simulation_id"]
        del_resp = client.delete(f"/simulations/{sim_id}")
        assert del_resp.status_code == 204

    def test_field_crud_unchanged(self, client):
        """Field CRUD must not be affected by DailyOutput schema changes."""
        from tests.conftest import FIELD_PAYLOAD
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        assert resp.status_code == 201
        field_id = resp.json()["field_id"]
        resp2 = client.get(f"/fields/{field_id}")
        assert resp2.status_code == 200
        client.delete(f"/fields/{field_id}")
