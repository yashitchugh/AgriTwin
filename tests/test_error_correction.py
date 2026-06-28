import pytest
from uuid import uuid4
from datetime import date, datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.models.farm import Farm
from backend.app.models.field import Field
from backend.app.models.simulation_run import SimulationRun
from backend.app.models.daily_output import DailyOutput
from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus

def test_correct_window_success(client: TestClient, test_db: Session):
    # 1. Create a Farm & Field
    farm = Farm(
        id=uuid4(),
        name="Test Farm EC",
    )
    test_db.add(farm)
    test_db.commit()
    
    field = Field(
        id=uuid4(),
        farm_id=farm.id,
        name="Test Field EC",
        latitude=26.8,
        longitude=75.8,
        area_ha=12.5,
    )
    test_db.add(field)
    
    # 2. Create a SimulationRun
    sim_run = SimulationRun(
        id=uuid4(),
        field_id=field.id,
        crop="wheat",
        variety="apache",
        latitude=26.8,
        longitude=75.8,
        sowing_date=date(2026, 5, 15),
        harvest_date=date(2026, 6, 15),
        status="completed",
        run_type="baseline",
    )
    test_db.add(sim_run)
    test_db.commit()

    # 3. Create DailyOutput records for the 7-day window (2026-06-01 to 2026-06-07)
    # Set WOFOST LAI to 1.0 for all days
    start_date = date(2026, 6, 1)
    for i in range(7):
        dt = date(2026, 6, 1 + i)
        daily = DailyOutput(
            simulation_run_id=sim_run.id,
            date=dt,
            lai=1.0,
            sm=0.2,
            dvs=0.5,
            tagp=1000.0,
            twso=100.0,
            wlv=200.0,
            wst=300.0,
            wrt=100.0,
            wso=100.0
        )
        test_db.add(daily)
    
    # 4. Create raw satellite LAI observations to trigger interpolation
    # Place observations such that interpolated value is around 2.0 (causing a residual of 1.0 > 0.5 threshold)
    obs1 = Observation(
        id=uuid4(),
        field_id=field.id,
        timestamp=datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
        variable_name="LAI",
        units="m2/m2",
        value=2.0,
        uncertainty=0.1,
        source=ObservationSource.SATELLITE,
        provider_name="Sentinel2_L2A",
        status=ObservationStatus.VALID
    )
    obs2 = Observation(
        id=uuid4(),
        field_id=field.id,
        timestamp=datetime(2026, 6, 8, 10, 0, 0, tzinfo=timezone.utc),
        variable_name="LAI",
        units="m2/m2",
        value=2.0,
        uncertainty=0.1,
        source=ObservationSource.SATELLITE,
        provider_name="Sentinel2_L2A",
        status=ObservationStatus.VALID
    )
    test_db.add(obs1)
    test_db.add(obs2)
    test_db.commit()

    # 5. Call the API endpoint
    payload = {
        "simulation_id": str(sim_run.id),
        "field_id": str(field.id),
        "window_start_date": "2026-06-01",
        "window_end_date": "2026-06-07",
        "residual_threshold": 0.5
    }
    
    response = client.post("/interpolate/error-correction/correct-window", json=payload)
    assert response.status_code == 200, response.text
    
    data = response.json()
    assert data["simulation_id"] == str(sim_run.id)
    assert data["anomalies_detected"] > 0
    assert data["anomalies_corrected"] > 0
    
    # 6. Verify that DailyOutput records in the database were updated
    updated_daily = test_db.query(DailyOutput).filter(
        DailyOutput.simulation_run_id == sim_run.id,
        DailyOutput.date == date(2026, 6, 1)
    ).first()
    assert updated_daily is not None
    # Original was 1.0. Interpolated was 2.0. Residual was 1.0 > 0.5.
    # Blending weight for residual > 0.5 (threshold) is 0.2 (model-heavy since it's > threshold).
    # Corrected = (1 - 0.2) * 1.0 + 0.2 * 2.0 = 0.8 + 0.4 = 1.2.
    assert updated_daily.lai == pytest.approx(1.2)
    
def test_correct_window_invalid_days(client: TestClient):
    payload = {
        "simulation_id": str(uuid4()),
        "field_id": str(uuid4()),
        "window_start_date": "2026-06-01",
        "window_end_date": "2026-06-05", # 5 days instead of 7
        "residual_threshold": 0.5
    }
    response = client.post("/interpolate/error-correction/correct-window", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "must be exactly 7 days" in data["message"]
