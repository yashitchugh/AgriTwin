"""
tests/test_scenario_api.py
===========================

Integration tests for the Scenario API endpoints.
"""

import datetime
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def sample_payload():
    return {
        "latitude": 28.6,
        "longitude": 77.2,
        "crop": "wheat",
        "variety": "apache",
        "sowing_date": "2020-10-15",
        "harvest_date": "2021-06-01",
        "use_real_weather": False,
        "use_real_soil": False
    }

def test_run_sowing_date_scenario(client: TestClient, sample_payload: dict):
    # Call the endpoint
    response = client.post(
        "/scenarios/sowing-date?offsets=-15,0,15",
        json=sample_payload
    )
    
    assert response.status_code == 201, response.text
    data = response.json()
    
    # Assert ScenarioComparison format
    assert "best_yield_simulation" in data
    assert "lowest_water_use_simulation" in data
    assert "lowest_stress_simulation" in data
    assert "ranked_runs" in data
    
    # Check that we have exactly 3 runs in the ranked list (for the 3 offsets)
    ranked_runs = data["ranked_runs"]
    assert len(ranked_runs) == 3
    
    # Check properties of the runs
    for run in ranked_runs:
        assert "scenario_run_id" in run
        assert "yield_kg_ha" in run
        assert run["yield_kg_ha"] is not None # Assuming the baseline crop doesn't fail

def test_run_irrigation_scenario(client: TestClient, sample_payload: dict):
    response = client.post(
        "/scenarios/irrigation",
        json=sample_payload
    )
    
    assert response.status_code == 201, response.text
    data = response.json()
    
    assert "best_yield_simulation" in data
    assert "ranked_runs" in data
    
    # 4 default tiers
    assert len(data["ranked_runs"]) == 4

def test_run_variety_scenario(client: TestClient, sample_payload: dict):
    # sample_payload has crop='wheat', variety='apache'
    response = client.post(
        "/scenarios/variety",
        json=sample_payload
    )
    
    assert response.status_code == 201, response.text
    data = response.json()
    
    assert "best_yield_simulation" in data
    assert "ranked_runs" in data
    
    # there are many varieties of wheat, more than 1
    assert len(data["ranked_runs"]) > 1

def test_invalid_offsets(client: TestClient, sample_payload: dict):
    response = client.post(
        "/scenarios/sowing-date?offsets=abc",
        json=sample_payload
    )
    
    assert response.status_code == 400
    assert "integers" in response.json()["detail"]
