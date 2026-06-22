"""
tests/test_assimilation_api.py
==============================

Integration tests for the EnKF Assimilation REST API endpoints.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import numpy as np


@patch("backend.app.assimilation.services.assimilation_service.forecast_until")
@patch("backend.app.assimilation.services.assimilation_service.enkf_update")
def test_assimilation_run_api_lifecycle(mock_enkf, mock_forecast, client: TestClient):
    """Test full assimilation execution and monitoring status workflow."""
    N = 3
    # State vectors shape (STATE_DIM, N)
    X_f = np.ones((10, N))
    mock_forecast.return_value = (X_f, np.mean(X_f, axis=1))
    mock_enkf.return_value = (X_f, np.ones(10), np.zeros((10, 10)))

    # 1. Create a Field
    field_payload = {
        "name": "Assimilation Test Field",
        "latitude": 52.0,
        "longitude": 5.5,
        "elevation_m": 12.0
    }
    field_resp = client.post("/fields", json=field_payload)
    assert field_resp.status_code == 201, field_resp.text
    field_id = field_resp.json()["field_id"]

    # 2. Create a Simulation Run (baseline)
    sim_payload = {
        "latitude": 52.0,
        "longitude": 5.5,
        "crop": "wheat",
        "variety": "Winter_wheat_101",
        "sowing_date": "2020-10-15",
        "harvest_date": "2020-11-15",
        "use_real_weather": False,
        "use_real_soil": False,
        "field_id": field_id
    }
    sim_resp = client.post("/simulate", json=sim_payload)
    assert sim_resp.status_code == 200, sim_resp.text
    simulation_id = sim_resp.json()["simulation_id"]

    # 3. Insert some fake observations so EnKF has cycles to execute
    obs_payload = {
        "field_id": field_id,
        "variable_name": "LAI",
        "units": "m2/m2",
        "value": 2.5,
        "uncertainty": 0.2,
        "timestamp": "2020-11-01T12:00:00Z",
        "source": "SATELLITE",
        "provider_name": "Sentinel2_L2A",
        "status": "VALID",
        "quality_score": 90,
        "cloud_cover": 0.05
    }
    obs_resp = client.post("/observations", json=obs_payload)
    assert obs_resp.status_code == 201, obs_resp.text

    # 4. Trigger Assimilation Run
    run_payload = {
        "simulation_id": simulation_id,
        "field_id": field_id,
        "ensemble_size": N
    }
    run_resp = client.post("/assimilation/run", json=run_payload)
    assert run_resp.status_code == 200, run_resp.text
    run_data = run_resp.json()

    assert "assimilation_run_id" in run_data
    assert run_data["status"] == "COMPLETED"
    assert run_data["executed_cycles"] == 1
    assert run_data["observations_assimilated"] == 1

    run_id = run_data["assimilation_run_id"]

    # 5. Check status endpoint
    status_resp = client.get(f"/assimilation/status/{simulation_id}")
    assert status_resp.status_code == 200, status_resp.text
    status_data = status_resp.json()

    assert status_data["assimilation_run_id"] == run_id
    assert status_data["latest_assimilation_run"] == run_id
    assert status_data["status"] == "COMPLETED"
    assert status_data["ensemble_size"] == N
    assert status_data["total_cycles"] == 1
    assert status_data["executed_cycles"] == 1
    assert status_data["skipped_cycles"] == 0
    assert status_data["latest_cycle_date"] == "2020-11-01"
    assert status_data["observations_assimilated"] == 1


def test_assimilation_run_not_found(client: TestClient):
    """Test response codes for non-existent field or simulation IDs."""
    fake_id = str(uuid.uuid4())
    run_payload = {
        "simulation_id": fake_id,
        "field_id": fake_id,
        "ensemble_size": 5
    }
    run_resp = client.post("/assimilation/run", json=run_payload)
    assert run_resp.status_code == 404

    status_resp = client.get(f"/assimilation/status/{fake_id}")
    assert status_resp.status_code == 404
