"""
tests/test_assimilation_workflow.py
===================================

Unified integration tests for the complete seasonal EnKF assimilation workflow.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import numpy as np
from sqlalchemy.orm import Session

from backend.app.models.farm import Farm
from backend.app.models.field import Field
from backend.app.models.simulation_run import SimulationRun
from backend.app.models.assimilation_run import AssimilationRun
from backend.app.assimilation.models.assimilation_state import AssimilationState


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_complete_assimilation_workflow(mock_enkf, mock_forecast, client: TestClient, test_engine):
    """Test full assimilation workflow from loop run to visualization and cascade delete."""
    N = 3
    # State vectors shape (STATE_DIM, N)
    X_f = np.ones((10, N))
    mock_forecast.return_value = (X_f, np.mean(X_f, axis=1))
    mock_enkf.return_value = (X_f, np.ones(10), np.zeros((10, 10)))

    # 1. Setup Farm & Field
    farm_id = uuid.uuid4()
    field_id = uuid.uuid4()
    with Session(test_engine) as db:
        farm = Farm(id=farm_id, name="Workflow Farm")
        field = Field(
            id=field_id,
            farm_id=farm_id,
            name="Workflow Field",
            latitude=52.0,
            longitude=5.5,
            elevation_m=10.0
        )
        db.add(farm)
        db.add(field)
        db.commit()

    # 2. Create Simulation Run (Baseline)
    sim_payload = {
        "latitude": 52.0,
        "longitude": 5.5,
        "crop": "wheat",
        "variety": "Winter_wheat_101",
        "sowing_date": "2020-10-15",
        "harvest_date": "2020-11-15",
        "use_real_weather": False,
        "use_real_soil": False,
        "field_id": str(field_id)
    }
    sim_resp = client.post("/simulate", json=sim_payload)
    assert sim_resp.status_code == 200, sim_resp.text
    sim_id = sim_resp.json()["simulation_id"]

    # 3. Create observation
    obs_payload = {
        "field_id": str(field_id),
        "variable_name": "LAI",
        "units": "m2/m2",
        "value": 2.5,
        "uncertainty": 0.2,
        "timestamp": "2020-11-01T12:00:00Z",
        "source": "SATELLITE",
        "provider_name": "Sentinel2_L2A",
        "status": "VALID",
        "quality_score": 95,
        "cloud_cover": 0.05
    }
    obs_resp = client.post("/observations", json=obs_payload)
    assert obs_resp.status_code == 201, obs_resp.text

    # 4. POST /assimilation/run - trigger seasonal loop
    run_payload = {
        "simulation_id": sim_id,
        "field_id": str(field_id),
        "ensemble_size": N
    }
    run_resp = client.post("/assimilation/run", json=run_payload)
    assert run_resp.status_code == 200, run_resp.text
    run_data = run_resp.json()
    assert run_data["status"] == "COMPLETED"
    run_id = uuid.UUID(run_data["assimilation_run_id"])

    # Verify: POST /assimilation/run creates AssimilationRun row
    with Session(test_engine) as db:
        assim_run = db.get(AssimilationRun, run_id)
        assert assim_run is not None
        assert assim_run.simulation_id == uuid.UUID(sim_id)
        assert assim_run.status == "COMPLETED"

        # Verify: AssimilationState rows belong to AssimilationRun
        states = db.query(AssimilationState).filter(AssimilationState.assimilation_run_id == run_id).all()
        assert len(states) == 1
        assert states[0].assimilation_run_id == run_id
        assert states[0].simulation_run_id == uuid.UUID(sim_id)

    # Verify: GET /assimilation/status/{simulation_id} returns latest run
    status_resp = client.get(f"/assimilation/status/{sim_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["assimilation_run_id"] == str(run_id)
    assert status_data["status"] == "COMPLETED"

    # Verify: GET /assimilation/{simulation_id}/history returns cycles chronologically
    history_resp = client.get(f"/assimilation/{sim_id}/history")
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert len(history_data) == 1
    assert history_data[0]["cycle_date"] == "2020-11-01"
    assert history_data[0]["cycle_number"] == 1

    # Verify: GET /assimilation/{simulation_id}/timeseries contains: open_loop, assimilated, observation
    timeseries_resp = client.get(f"/assimilation/{sim_id}/timeseries")
    assert timeseries_resp.status_code == 200
    timeseries_data = timeseries_resp.json()
    assert "LAI" in timeseries_data
    lai_points = timeseries_data["LAI"]
    assert len(lai_points) > 0
    p = lai_points[0]
    assert "open_loop" in p
    assert "assimilated" in p
    assert "observation" in p

    # Verify: GET /assimilation/{simulation_id}/yield-evolution returns ordered predictions
    yield_resp = client.get(f"/assimilation/{sim_id}/yield-evolution")
    assert yield_resp.status_code == 200
    yield_data = yield_resp.json()
    assert len(yield_data) == 1
    assert "predicted_yield_kg_ha" in yield_data[0]
    assert "date" in yield_data[0]

    # Verify cascade delete: SimulationRun -> AssimilationRun -> AssimilationState
    del_resp = client.delete(f"/simulations/{sim_id}")
    assert del_resp.status_code == 204

    with Session(test_engine) as db:
        # SimulationRun should be deleted
        assert db.get(SimulationRun, uuid.UUID(sim_id)) is None
        # AssimilationRun should be deleted
        assert db.get(AssimilationRun, run_id) is None
        # AssimilationState should be deleted
        remaining_states = db.query(AssimilationState).filter(AssimilationState.assimilation_run_id == run_id).all()
        assert len(remaining_states) == 0
