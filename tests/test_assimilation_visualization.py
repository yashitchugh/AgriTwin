"""
tests/test_assimilation_visualization.py
========================================

Integration tests for the EnKF read-only visualization APIs.
"""

import uuid
import datetime as dt
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from backend.app.models.simulation_run import SimulationRun
from backend.app.models.field import Field
from backend.app.models.farm import Farm
from backend.app.models.assimilation_run import AssimilationRun
from backend.app.models.daily_output import DailyOutput
from backend.app.assimilation.models.assimilation_state import AssimilationState
from backend.app.assimilation.models.observation import Observation


@pytest.fixture
def mock_viz_data(test_engine):
    """Seed test database with simulation, daily outputs, run, states, and observations."""
    farm_id = uuid.uuid4()
    field_id = uuid.uuid4()
    sim_id = uuid.uuid4()
    run_id = uuid.uuid4()
    state_id = uuid.uuid4()
    obs_id = uuid.uuid4()

    with Session(test_engine) as db:
        # 1. Add Farm
        farm = Farm(
            id=farm_id,
            name="Viz Test Farm"
        )
        db.add(farm)

        # 2. Add Field linked to Farm
        field = Field(
            id=field_id,
            farm_id=farm_id,
            name="Viz Test Field",
            latitude=52.0,
            longitude=5.5,
            elevation_m=12.0
        )
        db.add(field)

        # 2. Add Simulation Run
        sim = SimulationRun(
            id=sim_id,
            field_id=field_id,
            run_type="baseline",
            status="completed",
            model_name="Wofost72_WLP_FD",
            model_version="7.2",
            latitude=52.0,
            longitude=5.5,
            crop="wheat",
            variety="apache",
            sowing_date=dt.date(2020, 10, 15),
            harvest_date=dt.date(2020, 11, 15),
            use_real_weather=False,
            use_real_soil=False,
        )
        db.add(sim)

        # 3. Add Daily Outputs (Open-loop)
        day1 = DailyOutput(
            simulation_run_id=sim_id,
            date=dt.date(2020, 11, 1),
            lai=2.0,
            sm=0.3,
            tagp=1000.0,
            twso=100.0,
            rftra=0.9
        )
        day2 = DailyOutput(
            simulation_run_id=sim_id,
            date=dt.date(2020, 11, 2),
            lai=2.2,
            sm=0.28,
            tagp=1100.0,
            twso=120.0,
            rftra=0.88
        )
        db.add(day1)
        db.add(day2)

        # 4. Add Assimilation Run
        run = AssimilationRun(
            id=run_id,
            simulation_id=sim_id,
            status="COMPLETED",
            ensemble_size=5,
            total_cycles=1,
            executed_cycles=1,
            skipped_cycles=0,
            observations_used=1,
            config_json={},
            started_at=dt.datetime.now(dt.timezone.utc),
            completed_at=dt.datetime.now(dt.timezone.utc)
        )
        db.add(run)

        # 5. Add Assimilation State update on 2020-11-01
        # Shows a prior LAI of 1.8 updated to posterior LAI of 2.1 (offset of +0.3)
        state = AssimilationState(
            id=state_id,
            field_id=field_id,
            simulation_run_id=sim_id,
            assimilation_run_id=run_id,
            assimilation_time=dt.datetime(2020, 11, 1, 0, 0, 0, tzinfo=dt.timezone.utc),
            forecast_state_vector={
                "lai": 1.8, "sm": 0.3, "tagp": 1000.0, "twso": 100.0, "rftra": 0.9,
                "twlv": 300.0, "twst": 250.0, "twrt": 80.0, "dvs": 0.5, "rd": 40.0
            },
            updated_state_vector={
                "lai": 2.1, "sm": 0.31, "tagp": 1000.0, "twso": 105.0, "rftra": 0.9,
                "twlv": 300.0, "twst": 250.0, "twrt": 80.0, "dvs": 0.5, "rd": 40.0
            },
            observation_vector={
                "lai": 2.2, "sm": None, "tagp": None, "twso": None, "rftra": None,
                "twlv": None, "twst": None, "twrt": None, "dvs": None, "rd": None
            },
            innovation_vector={
                "lai": 0.4, "sm": None, "tagp": None, "twso": None, "rftra": None,
                "twlv": None, "twst": None, "twrt": None, "dvs": None, "rd": None
            },
            ensemble_mean={},
            ensemble_covariance={},
            kalman_gain={},
            number_of_members=5,
            observation_count=1
        )
        db.add(state)

        # 6. Add Observation
        obs = Observation(
            id=obs_id,
            field_id=field_id,
            timestamp=dt.datetime(2020, 11, 1, 12, 0, 0, tzinfo=dt.timezone.utc),
            variable_name="LAI",
            units="m2/m2",
            value=2.2,
            uncertainty=0.2,
            source="SATELLITE",
            provider_name="Sentinel2_L2A",
            status="VALID",
            quality_score=95
        )
        db.add(obs)

        db.commit()

    return {
        "field_id": field_id,
        "simulation_id": sim_id,
        "run_id": run_id
    }


def test_get_history_endpoint(client: TestClient, mock_viz_data: dict):
    sim_id = mock_viz_data["simulation_id"]
    response = client.get(f"/assimilation/{sim_id}/history")
    assert response.status_code == 200, response.text
    data = response.json()

    assert len(data) == 1
    item = data[0]
    assert item["cycle_date"] == "2020-11-01"
    assert "LAI" in item["variables_updated"]
    assert item["observation_vector"]["LAI"] == 2.2
    assert item["prior_state"]["LAI"] == 1.8
    assert item["posterior_state"]["LAI"] == 2.1
    assert item["innovation"]["LAI"] == 0.4
    assert item["quality_score"] == 95.0
    assert item["cycle_number"] == 1


def test_get_timeseries_endpoint(client: TestClient, mock_viz_data: dict):
    sim_id = mock_viz_data["simulation_id"]
    response = client.get(f"/assimilation/{sim_id}/timeseries")
    assert response.status_code == 200, response.text
    data = response.json()

    # Verify keys
    assert "LAI" in data
    assert "SM" in data
    assert "TAGP" in data
    assert "TWSO" in data
    assert "RFTRA" in data

    # Verify LAI points
    lai_points = data["LAI"]
    assert len(lai_points) == 2

    # Point 1 (2020-11-01) - direct update from AssimilationState
    # prior = 1.8, posterior = 2.1 (offset = +0.3)
    p1 = lai_points[0]
    assert p1["date"] == "2020-11-01"
    assert p1["open_loop"] == 2.0
    assert p1["assimilated"] == 2.1
    assert p1["observation"] == 2.2

    # Point 2 (2020-11-02) - offset propagation (+0.3)
    # open_loop = 2.2, offset = +0.3 => assimilated = 2.5
    p2 = lai_points[1]
    assert p2["date"] == "2020-11-02"
    assert p2["open_loop"] == 2.2
    assert p2["assimilated"] == pytest.approx(2.5)
    assert p2["observation"] is None


def test_get_yield_evolution_endpoint(client: TestClient, mock_viz_data: dict):
    sim_id = mock_viz_data["simulation_id"]
    response = client.get(f"/assimilation/{sim_id}/yield-evolution")
    assert response.status_code == 200, response.text
    data = response.json()

    assert len(data) == 1
    assert data[0]["date"] == "2020-11-01"
    assert data[0]["predicted_yield_kg_ha"] == 105.0


def test_visualization_endpoints_not_found(client: TestClient):
    fake_id = uuid.uuid4()
    
    # 404 for nonexistent simulation ID
    assert client.get(f"/assimilation/{fake_id}/history").status_code == 404
    assert client.get(f"/assimilation/{fake_id}/timeseries").status_code == 404
    assert client.get(f"/assimilation/{fake_id}/yield-evolution").status_code == 404
