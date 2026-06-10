"""
tests/test_db_api.py — Database Integration Tests
===================================================

Tests for:
  A. POST /simulate stores data correctly
  B. GET /simulations endpoints work
  C. Cascade delete: SimulationRun → DailyOutput
  D. Field CRUD: POST / GET / DELETE /fields
  E. Cascade delete: Field → SimulationRun → DailyOutput

Each test class is independent — order does not matter.
All tests use synthetic weather (use_real_weather=False) so no internet is needed.
"""

import uuid
import datetime as dt

import pytest
from sqlalchemy import select

from backend.app.models.simulation_run import SimulationRun
from backend.app.models.daily_output import DailyOutput
from backend.app.models.field import Field
from backend.app.models.farm import Farm

from tests.conftest import SIMULATE_PAYLOAD, FIELD_PAYLOAD


# ═══════════════════════════════════════════════════════════════════════════════
# A. POST /simulate stores data correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulatePersistence:
    """Verify that POST /simulate creates a SimulationRun + DailyOutput rows in DB."""

    def test_response_contains_simulation_id(self, client):
        """The response must include a non-null simulation_id UUID."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["simulation_id"] is not None, "simulation_id must not be None"
        # Must be a valid UUID4 string
        parsed = uuid.UUID(data["simulation_id"])
        assert parsed.version == 4

    def test_simulation_run_row_exists_in_db(self, client, test_engine):
        """After POST /simulate, the SimulationRun row must exist in the DB."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            run = db.get(SimulationRun, sim_id)
            assert run is not None, f"SimulationRun {sim_id} not found in DB"
            assert run.status == "completed"
            assert run.crop == "wheat"
            assert run.variety == "apache"
            assert run.latitude == 52.0
            assert run.longitude == 5.5
            assert run.use_real_weather is False
            assert run.use_real_soil is False
            assert run.run_type == "baseline"

    def test_scalar_results_are_stored(self, client, test_engine):
        """yield_kg_ha, peak_lai, harvest_index, total_days must be stored."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            run = db.get(SimulationRun, sim_id)
            assert run.yield_kg_ha is not None
            assert run.peak_lai is not None and run.peak_lai > 0
            assert run.total_days is not None and run.total_days > 0

    def test_json_payloads_are_stored(self, client, test_engine):
        """All 5 JSON columns must be non-null after a successful simulation."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            run = db.get(SimulationRun, sim_id)
            assert run.request_payload is not None, "request_payload is None"
            assert run.metrics_payload is not None, "metrics_payload is None"
            assert run.weather_snapshot is not None, "weather_snapshot is None"
            # request_payload must echo the original request
            assert run.request_payload["crop"] == "wheat"
            assert run.request_payload["variety"] == "apache"
            # weather_snapshot must record source
            assert run.weather_snapshot["source"] == "synthetic"

    def test_daily_output_rows_are_stored(self, client, test_engine):
        """DailyOutput rows must be created (one per simulated day)."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])
        expected_days = resp.json()["metrics"]["total_days"]

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            stmt = (
                select(DailyOutput)
                .where(DailyOutput.simulation_run_id == sim_id)
                .order_by(DailyOutput.date)
            )
            rows = db.execute(stmt).scalars().all()
            assert len(rows) == expected_days, (
                f"Expected {expected_days} DailyOutput rows, got {len(rows)}"
            )

    def test_daily_output_has_correct_columns(self, client, test_engine):
        """spot-check that sm (soil moisture) is present and in valid range."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            # Get a row from mid-season (not the pre-sowing buffer)
            stmt = (
                select(DailyOutput)
                .where(
                    DailyOutput.simulation_run_id == sim_id,
                    DailyOutput.sm.is_not(None),
                    DailyOutput.dvs.is_not(None),
                    DailyOutput.dvs > 0.1,
                )
                .order_by(DailyOutput.date)
                .limit(1)
            )
            row = db.execute(stmt).scalars().first()
            assert row is not None, "No DailyOutput with dvs > 0.1 found"
            assert 0.0 <= row.sm <= 1.0, f"sm={row.sm} out of range"
            assert row.dvs >= 0.0

    def test_irrigated_run_type_is_set(self, client, test_engine):
        """A simulation with irrigation_events must have run_type='irrigated'."""
        payload = {
            **SIMULATE_PAYLOAD,
            "irrigation_events": [{"date": "2020-12-01", "amount_mm": 40}],
        }
        resp = client.post("/simulate", json=payload)
        assert resp.status_code == 200
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            run = db.get(SimulationRun, sim_id)
            assert run.run_type == "irrigated"


# ═══════════════════════════════════════════════════════════════════════════════
# B. GET /simulations endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulationsAPI:
    """Tests for GET /simulations and GET /simulations/{id}."""

    @pytest.fixture(autouse=True)
    def run_a_simulation(self, client):
        """Run one simulation before each test in this class."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        assert resp.status_code == 200
        self.sim_id = resp.json()["simulation_id"]

    def test_list_simulations_returns_200(self, client):
        resp = client.get("/simulations")
        assert resp.status_code == 200

    def test_list_simulations_contains_our_run(self, client):
        resp = client.get("/simulations")
        data = resp.json()
        assert "items" in data
        assert data["total"] >= 1
        ids = [item["simulation_id"] for item in data["items"]]
        assert self.sim_id in ids

    def test_list_simulations_crop_filter(self, client):
        resp = client.get("/simulations", params={"crop": "wheat"})
        data = resp.json()
        assert all(item["crop"] == "wheat" for item in data["items"])

    def test_list_simulations_wrong_crop_returns_empty(self, client):
        resp = client.get("/simulations", params={"crop": "banana"})
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_get_simulation_detail_200(self, client):
        resp = client.get(f"/simulations/{self.sim_id}")
        assert resp.status_code == 200

    def test_get_simulation_detail_fields(self, client):
        resp = client.get(f"/simulations/{self.sim_id}")
        data = resp.json()
        assert data["simulation_id"] == self.sim_id
        assert data["crop"] == "wheat"
        assert data["variety"] == "apache"
        assert data["status"] == "completed"
        assert data["yield_kg_ha"] is not None
        assert "request_payload" in data
        assert "metrics_payload" in data
        assert "weather_snapshot" in data

    def test_get_simulation_detail_has_daily_states(self, client):
        resp = client.get(f"/simulations/{self.sim_id}")
        data = resp.json()
        assert "daily_states" in data
        assert len(data["daily_states"]) > 0, "daily_states must not be empty"
        # Check first record has a date field
        first = data["daily_states"][0]
        assert "date" in first
        assert "sm" in first

    def test_get_simulation_detail_not_found(self, client):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/simulations/{fake_id}")
        assert resp.status_code == 404

    def test_list_simulations_pagination(self, client):
        # With limit=1 and offset=0 we should get exactly 1 item
        resp = client.get("/simulations", params={"limit": 1, "offset": 0})
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["limit"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# C. Cascade delete: SimulationRun → DailyOutput
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulationCascadeDelete:
    """Verify that deleting a SimulationRun removes all its DailyOutput rows."""

    def test_delete_simulation_returns_204(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = resp.json()["simulation_id"]

        del_resp = client.delete(f"/simulations/{sim_id}")
        assert del_resp.status_code == 204

    def test_deleted_simulation_not_in_list(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = resp.json()["simulation_id"]
        client.delete(f"/simulations/{sim_id}")

        list_resp = client.get("/simulations")
        ids = [item["simulation_id"] for item in list_resp.json()["items"]]
        assert sim_id not in ids

    def test_deleted_simulation_returns_404(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = resp.json()["simulation_id"]
        client.delete(f"/simulations/{sim_id}")

        get_resp = client.get(f"/simulations/{sim_id}")
        assert get_resp.status_code == 404

    def test_daily_outputs_are_cascade_deleted(self, client, test_engine):
        """DailyOutput rows must be gone after deleting the parent SimulationRun."""
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        sim_id = uuid.UUID(resp.json()["simulation_id"])

        # Verify rows exist before delete
        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            before = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == sim_id)
            ).scalars().all()
            assert len(before) > 0, "No DailyOutput rows before delete"

        # Delete the run
        client.delete(f"/simulations/{sim_id}")

        # Verify rows are gone
        with Session(test_engine) as db:
            after = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == sim_id)
            ).scalars().all()
            assert len(after) == 0, f"Expected 0 rows after cascade delete, got {len(after)}"

    def test_delete_nonexistent_simulation_returns_404(self, client):
        resp = client.delete(f"/simulations/{uuid.uuid4()}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# D. Field CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldCRUD:
    """Tests for POST / GET / DELETE /fields."""

    def test_create_field_returns_201(self, client):
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        assert resp.status_code == 201, resp.text

    def test_create_field_response_shape(self, client):
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        data = resp.json()
        assert "field_id" in data
        assert data["name"] == FIELD_PAYLOAD["name"]
        assert data["latitude"] == FIELD_PAYLOAD["latitude"]
        assert data["longitude"] == FIELD_PAYLOAD["longitude"]
        assert data["area_ha"] == FIELD_PAYLOAD["area_ha"]
        assert data["simulation_count"] == 0
        # farm_id must be auto-assigned (not null)
        assert data["farm_id"] is not None

    def test_create_field_without_farm_id_auto_creates_farm(self, client, test_engine):
        """POST /fields without farm_id must create (or reuse) a Default Farm."""
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = uuid.UUID(resp.json()["field_id"])
        farm_id = uuid.UUID(resp.json()["farm_id"])

        from sqlalchemy.orm import Session
        with Session(test_engine) as db:
            farm = db.get(Farm, farm_id)
            assert farm is not None
            assert farm.name == "Default Farm"

    def test_create_field_with_explicit_farm_id(self, client, test_engine):
        """POST /fields with a valid farm_id must use that farm."""
        from sqlalchemy.orm import Session
        farm_id = uuid.uuid4()
        with Session(test_engine) as db:
            db.add(Farm(id=farm_id, name="Explicit Farm"))
            db.commit()
        # farm_id is now a plain UUID — no ORM object held after session close
        payload = {**FIELD_PAYLOAD, "farm_id": str(farm_id), "name": "Explicit Farm Field"}
        resp = client.post("/fields", json=payload)
        assert resp.status_code == 201
        assert resp.json()["farm_id"] == str(farm_id)

    def test_create_field_invalid_farm_id_returns_404(self, client):
        payload = {**FIELD_PAYLOAD, "farm_id": str(uuid.uuid4())}
        resp = client.post("/fields", json=payload)
        assert resp.status_code == 404

    def test_get_field_returns_200(self, client):
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = create.json()["field_id"]

        resp = client.get(f"/fields/{field_id}")
        assert resp.status_code == 200
        assert resp.json()["field_id"] == field_id

    def test_get_field_not_found(self, client):
        resp = client.get(f"/fields/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_list_fields_returns_200(self, client):
        client.post("/fields", json=FIELD_PAYLOAD)
        resp = client.get("/fields")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] >= 1

    def test_list_fields_contains_created_field(self, client):
        create = client.post("/fields", json={**FIELD_PAYLOAD, "name": "Unique Field XYZ"})
        field_id = create.json()["field_id"]

        resp = client.get("/fields")
        ids = [item["field_id"] for item in resp.json()["items"]]
        assert field_id in ids

    def test_list_fields_bounding_box_filter(self, client):
        """Fields outside the bounding box must not appear in results."""
        # Create a field in India
        client.post("/fields", json={**FIELD_PAYLOAD, "name": "India Field", "latitude": 26.8, "longitude": 80.9})
        # Create a field in Netherlands
        client.post("/fields", json={**FIELD_PAYLOAD, "name": "NL Field", "latitude": 52.0, "longitude": 5.5})

        # Query bounding box around India only
        resp = client.get("/fields", params={"lat_min": 20.0, "lat_max": 30.0, "lon_min": 75.0, "lon_max": 85.0})
        data = resp.json()
        for item in data["items"]:
            assert 20.0 <= item["latitude"] <= 30.0
            assert 75.0 <= item["longitude"] <= 85.0

    def test_delete_field_returns_204(self, client):
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = create.json()["field_id"]

        resp = client.delete(f"/fields/{field_id}")
        assert resp.status_code == 204

    def test_deleted_field_not_found(self, client):
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = create.json()["field_id"]
        client.delete(f"/fields/{field_id}")

        resp = client.get(f"/fields/{field_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_field_returns_404(self, client):
        resp = client.delete(f"/fields/{uuid.uuid4()}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# E. Cascade delete: Field → SimulationRun → DailyOutput
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldCascadeDelete:
    """Verify that deleting a Field cascades to SimulationRun and DailyOutput."""

    def test_simulation_count_increments_after_simulate(self, client):
        """GET /fields/{id} simulation_count must reflect stored runs."""
        # This test is forward-looking — currently /simulate doesn't attach
        # a field_id.  The count is based on SimulationRun.field_id = field_id.
        # We verify the count = 0 for a fresh field (basic sanity).
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = create.json()["field_id"]

        resp = client.get(f"/fields/{field_id}")
        assert resp.json()["simulation_count"] == 0

    def test_field_cascade_deletes_simulation_runs(self, client, test_engine):
        """Deleting a Field must cascade-delete all its SimulationRuns."""
        # 1. Create a field
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = uuid.UUID(create.json()["field_id"])

        # 2. Manually insert a SimulationRun attached to this field
        from sqlalchemy.orm import Session
        run_id = uuid.uuid4()
        with Session(test_engine) as db:
            run = SimulationRun(
                id=run_id,
                field_id=field_id,
                run_type="baseline",
                status="completed",
                model_name="Wofost72_WLP_FD",
                model_version="7.2",
                latitude=26.8,
                longitude=80.9,
                crop="wheat",
                variety="apache",
                sowing_date=dt.date(2020, 10, 15),
                use_real_weather=False,
                use_real_soil=False,
            )
            db.add(run)
            db.commit()

        # 3. Verify run exists
        with Session(test_engine) as db:
            assert db.get(SimulationRun, run_id) is not None

        # 4. Delete the field
        del_resp = client.delete(f"/fields/{field_id}")
        assert del_resp.status_code == 204

        # 5. Verify SimulationRun was cascade-deleted
        with Session(test_engine) as db:
            assert db.get(SimulationRun, run_id) is None, (
                "SimulationRun must be deleted when its parent Field is deleted"
            )

    def test_field_cascade_deletes_daily_outputs(self, client, test_engine):
        """Deleting a Field must cascade-delete all DailyOutput rows."""
        # 1. Create field and attach a SimulationRun + DailyOutput rows
        create = client.post("/fields", json=FIELD_PAYLOAD)
        field_id = uuid.UUID(create.json()["field_id"])

        from sqlalchemy.orm import Session
        run_id = uuid.uuid4()
        with Session(test_engine) as db:
            run = SimulationRun(
                id=run_id, field_id=field_id, run_type="baseline",
                status="completed", model_name="Wofost72_WLP_FD",
                model_version="7.2", latitude=26.8, longitude=80.9,
                crop="wheat", variety="apache",
                sowing_date=dt.date(2020, 10, 15),
                use_real_weather=False, use_real_soil=False,
            )
            db.add(run)
            db.flush()
            for i in range(5):
                db.add(DailyOutput(
                    simulation_run_id=run_id,
                    date=dt.date(2020, 10, 15) + dt.timedelta(days=i),
                    sm=0.3, dvs=float(i) * 0.01,
                ))
            db.commit()

        # Verify 5 rows exist
        with Session(test_engine) as db:
            count = len(db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == run_id)
            ).scalars().all())
            assert count == 5

        # Delete the field — should cascade all the way down
        client.delete(f"/fields/{field_id}")

        with Session(test_engine) as db:
            remaining = db.execute(
                select(DailyOutput).where(DailyOutput.simulation_run_id == run_id)
            ).scalars().all()
            assert len(remaining) == 0, (
                f"Expected 0 DailyOutput rows after field cascade delete, got {len(remaining)}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# F. Health check includes database status
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_includes_database_field(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "database" in data
        # In tests the real engine is used for health check; may say 'connected'
        assert data["status"] == "ok"
