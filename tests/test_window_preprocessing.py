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

def test_generate_windows_success(client: TestClient, test_db: Session):
    # 1. Create a Farm & Field
    farm = Farm(
        id=uuid4(),
        name="Test Farm WP",
    )
    test_db.add(farm)
    test_db.commit()
    
    field = Field(
        id=uuid4(),
        farm_id=farm.id,
        name="Test Field WP",
        latitude=52.0,
        longitude=5.5,
        area_ha=10.0,
    )
    test_db.add(field)
    
    # 2. Create a SimulationRun
    sim_run = SimulationRun(
        id=uuid4(),
        field_id=field.id,
        crop="wheat",
        variety="apache",
        latitude=52.0,
        longitude=5.5,
        sowing_date=date(2026, 5, 15),
        harvest_date=date(2026, 6, 15),
        status="completed",
        run_type="baseline",
    )
    test_db.add(sim_run)
    test_db.commit()

    # 3. Create 15 DailyOutput records
    for i in range(15):
        dt = date(2026, 5, 15 + i)
        daily = DailyOutput(
            simulation_run_id=sim_run.id,
            date=dt,
            lai=1.0 + i * 0.1,
            sm=0.2 + i * 0.01,
            dvs=0.1 + i * 0.05,
            tagp=100.0 + i * 50.0,
            twso=0.0,
            wlv=20.0 + i * 10.0,
            wst=30.0 + i * 15.0,
            wrt=10.0 + i * 5.0,
            wso=0.0
        )
        test_db.add(daily)
        
    # 4. Create raw satellite LAI observations
    obs1 = Observation(
        id=uuid4(),
        field_id=field.id,
        timestamp=datetime(2026, 5, 15, 10, 0, 0, tzinfo=timezone.utc),
        variable_name="LAI",
        units="m2/m2",
        value=1.5,
        uncertainty=0.1,
        source=ObservationSource.SATELLITE,
        provider_name="Sentinel2_L2A",
        status=ObservationStatus.VALID
    )
    obs2 = Observation(
        id=uuid4(),
        field_id=field.id,
        timestamp=datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc),
        variable_name="LAI",
        units="m2/m2",
        value=3.5,
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
        "window_size": 7,
        "stride": 1,
        "normalize": True
    }
    
    response = client.post("/interpolate/preprocess/generate-windows", json=payload)
    assert response.status_code == 200, response.text
    
    data = response.json()
    assert data["simulation_id"] == str(sim_run.id)
    # 15 days, window_size 7, stride 1 -> 15 - 7 - 1 = 7 windows
    assert data["total_windows_generated"] == 7
    assert len(data["features_used"]) > 0
    assert "LAI" in data["features_used"]
    assert "SAND" in data["features_used"]
    assert len(data["normalization_scalers"]) > 0

def test_generate_windows_no_data(client: TestClient):
    payload = {
        "simulation_id": str(uuid4()),
        "field_id": str(uuid4()),
        "window_size": 7,
        "stride": 1,
        "normalize": True
    }
    response = client.post("/interpolate/preprocess/generate-windows", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["total_windows_generated"] == 0
    assert "No daily data found" in data["message"]
