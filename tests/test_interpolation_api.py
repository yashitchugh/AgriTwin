import pytest
from fastapi.testclient import TestClient

def test_interpolate_linear(client: TestClient):
    payload = {
        "observation_dates": ["2026-06-01", "2026-06-05", "2026-06-10"],
        "observation_values": [1.0, 2.0, 1.5],
        "target_dates": ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06", "2026-06-07", "2026-06-08", "2026-06-09", "2026-06-10"],
        "method": "linear",
        "max_allowed_gap_days": 10
    }
    response = client.post("/interpolate/fill-gaps", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["method_used"] == "Linear"
    assert len(data["interpolated_dates"]) == 10
    assert len(data["interpolated_values"]) == 10
    # Values should be interpolated
    assert data["interpolated_values"][0] == 1.0
    assert data["interpolated_values"][4] == 2.0
    assert data["interpolated_values"][9] == 1.5
    # The intermediate values should be linear
    assert data["interpolated_values"][2] == 1.5  # halfway between 1 and 2

def test_interpolate_cubic_spline(client: TestClient):
    payload = {
        "observation_dates": ["2026-06-01", "2026-06-05", "2026-06-10"],
        "observation_values": [1.0, 2.0, 1.5],
        "target_dates": ["2026-06-01", "2026-06-05", "2026-06-10"],
        "method": "cubic_spline",
        "max_allowed_gap_days": 10
    }
    response = client.post("/interpolate/fill-gaps", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["method_used"] == "Cubic Spline"
    assert data["interpolated_values"] == [1.0, 2.0, 1.5]

def test_interpolate_savgol(client: TestClient):
    payload = {
        "observation_dates": ["2026-06-01", "2026-06-03", "2026-06-05", "2026-06-07", "2026-06-09", "2026-06-11"],
        "observation_values": [1.0, 1.2, 1.5, 1.4, 1.8, 2.0],
        "target_dates": ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06", "2026-06-07", "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"],
        "method": "savgol",
        "max_allowed_gap_days": 10
    }
    response = client.post("/interpolate/fill-gaps", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["method_used"] == "Savitzky-Golay"
    assert len(data["interpolated_values"]) == 11

def test_interpolate_cloud_gap_trigger(client: TestClient):
    # A gap of 11 days (June 1 to June 12) with max_allowed_gap_days = 10
    payload = {
        "observation_dates": ["2026-06-01", "2026-06-12"],
        "observation_values": [1.0, 2.0],
        "target_dates": ["2026-06-01", "2026-06-05", "2026-06-12"],
        "method": "linear",
        "max_allowed_gap_days": 10
    }
    response = client.post("/interpolate/fill-gaps", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "large gaps detected" in data["message"].lower()
    
    # Check if there is a gap risk in quality flags
    gap_flag = next((q for q in data["quality_flags"] if q.get("risk") == "high"), None)
    assert gap_flag is not None
    assert gap_flag["gap_days"] == 11
    assert "Skipping interpolation" in gap_flag["action"]

def test_interpolate_insufficient_points(client: TestClient):
    payload = {
        "observation_dates": ["2026-06-01"],
        "observation_values": [1.0],
        "target_dates": ["2026-06-01", "2026-06-02"],
        "method": "linear",
        "max_allowed_gap_days": 10
    }
    response = client.post("/interpolate/fill-gaps", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["method_used"] == "none"
    assert "Need at least 2 satellite observations" in data["message"]
    assert data["interpolated_values"] == [None, None]
