# tests/test_satellite_pipeline.py

import datetime
import math
import uuid
import pytest
from sqlalchemy import select

from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus
from backend.app.assimilation.models.observation_batch import ObservationBatch, BatchProcessingStatus
from backend.app.satellite.processors.vegetation_indices import compute_ndvi, compute_osavi, compute_seli
from backend.app.satellite.processors.lai_estimator import EmpiricalLAIEstimator
from backend.app.satellite.providers.sentinel2_provider import StubSentinel2Provider
from backend.app.satellite.services.lai_observation_service import LAIObservationService
from backend.app.models.field import Field
from backend.app.models.farm import Farm
from tests.test_data_sources import SAMPLE_GEOJSON

# ── 1. Vegetation Index Tests ──────────────────────────────────────────────────

def test_vegetation_indices_normal():
    # NDVI = (0.5 - 0.1) / (0.5 + 0.1) = 0.4 / 0.6 = 0.6667
    assert compute_ndvi(0.1, 0.5) == pytest.approx(0.666666666)
    
    # OSAVI = (1.16 * (0.5 - 0.1)) / (0.5 + 0.1 + 0.16) = 1.16 * 0.4 / 0.76 = 0.464 / 0.76 = 0.6105
    assert compute_osavi(0.1, 0.5) == pytest.approx(0.6105263)
    
    # SeLI = (0.5 - 0.2) / (0.5 + 0.2) = 0.3 / 0.7 = 0.42857
    assert compute_seli(0.2, 0.5) == pytest.approx(0.4285714)

def test_vegetation_indices_edge_cases():
    # Division by zero
    assert math.isnan(compute_ndvi(0.0, 0.0))
    assert math.isnan(compute_osavi(0.0, 0.0, L=0.0))
    assert math.isnan(compute_seli(0.0, 0.0))
    
    # Negative reflectances (physically invalid, should return NaN)
    assert math.isnan(compute_ndvi(-0.1, 0.5))
    assert math.isnan(compute_osavi(0.1, -0.5))
    assert math.isnan(compute_seli(-0.2, 0.5))
    
    # NaN inputs
    assert math.isnan(compute_ndvi(float('nan'), 0.5))
    assert math.isnan(compute_osavi(0.1, float('nan')))
    assert math.isnan(compute_seli(float('nan'), float('nan')))
    
    # None inputs
    assert math.isnan(compute_ndvi(None, 0.5))
    assert math.isnan(compute_osavi(0.1, None))
    assert math.isnan(compute_seli(None, None))

# ── 2. LAI Estimator Tests ─────────────────────────────────────────────────────

def test_empirical_lai_estimator():
    estimator = EmpiricalLAIEstimator()
    
    # NDVI: LAI = 0.1 * exp(4.0 * NDVI)
    # NDVI = 0.5 -> 0.1 * exp(2.0) = 0.1 * 7.389 = 0.7389
    assert estimator.estimate_lai(0.5, "NDVI") == pytest.approx(0.7389056)
    
    # OSAVI: LAI = 0.15 * exp(3.5 * OSAVI)
    # OSAVI = 0.5 -> 0.15 * exp(1.75) = 0.15 * 5.7546 = 0.8632
    assert estimator.estimate_lai(0.5, "OSAVI") == pytest.approx(0.86319)
    
    # SeLI: LAI = 5.0 * SeLI - 0.5
    # SeLI = 0.5 -> 5.0 * 0.5 - 0.5 = 2.0
    assert estimator.estimate_lai(0.5, "SeLI") == pytest.approx(2.0)
    
    # NaN and None inputs
    assert math.isnan(estimator.estimate_lai(float('nan'), "NDVI"))
    assert math.isnan(estimator.estimate_lai(None, "NDVI"))
    
    # Clipping limits [0.0, 8.0]
    # Extremely large index values to trigger clipping to 8.0
    assert estimator.estimate_lai(2.0, "NDVI") == 8.0
    # Negative estimated values to trigger clipping to 0.0
    assert estimator.estimate_lai(-1.0, "SeLI") == 0.0
    
    # Unknown index validation
    with pytest.raises(ValueError):
        estimator.estimate_lai(0.5, "UNKNOWN")

# ── 3. Stub Sentinel-2 Provider Tests ──────────────────────────────────────────

def test_stub_sentinel2_provider():
    provider = StubSentinel2Provider()
    
    # Invalid boundary geometry validation
    with pytest.raises(ValueError):
        provider.get_scenes(None, datetime.date(2024, 5, 1), datetime.date(2024, 5, 10))
    with pytest.raises(ValueError):
        provider.get_scenes({}, datetime.date(2024, 5, 1), datetime.date(2024, 5, 10))
    with pytest.raises(ValueError):
        provider.get_scenes({"invalid": "geojson"}, datetime.date(2024, 5, 1), datetime.date(2024, 5, 10))
        
    # Valid retrieval window
    scenes = provider.get_scenes(SAMPLE_GEOJSON, datetime.date(2024, 5, 1), datetime.date(2024, 5, 15))
    # 5-day nominal revisit: May 1, May 6, May 11
    assert len(scenes) == 3
    assert scenes[0].acquisition_date == datetime.date(2024, 5, 1)
    assert scenes[1].acquisition_date == datetime.date(2024, 5, 6)
    assert scenes[2].acquisition_date == datetime.date(2024, 5, 11)
    
    # Cloud cover rotation cycle: 0.05, 0.15, 0.60
    assert scenes[0].cloud_cover == 0.05
    assert scenes[1].cloud_cover == 0.15
    assert scenes[2].cloud_cover == 0.60

# ── 4. Service Ingestion & Deduplication Tests ────────────────────────────────

def test_lai_observation_service_ingestion(test_db):
    # Setup test farm and field models
    farm_id = uuid.uuid4()
    field_id = uuid.uuid4()
    test_db.add(Farm(id=farm_id, name="Test Satellite Farm"))
    test_db.flush()
    field = Field(
        id=field_id,
        farm_id=farm_id,
        name="Satellite Field A",
        latitude=26.8,
        longitude=80.9,
        boundary_geojson=SAMPLE_GEOJSON,
    )
    test_db.add(field)
    test_db.commit()
    
    from backend.app.assimilation.repositories.observation_repository import ObservationRepository
    obs_repo = ObservationRepository(test_db)
    provider = StubSentinel2Provider()
    estimator = EmpiricalLAIEstimator()
    service = LAIObservationService(obs_repo, provider, estimator)
    
    start_date = datetime.date(2024, 5, 1)
    end_date = datetime.date(2024, 5, 15)
    
    # 1. Ingest observations (max_cloud_cover=0.2 triggers filtering of scene 2)
    scenes = service.ingest_lai_observations(
        field_id=field_id,
        start_date=start_date,
        end_date=end_date,
        index_name="NDVI",
        max_cloud_cover=0.2,
        uncertainty=0.25,
    )
    
    # Verify return list filters cloud cover
    assert len(scenes) == 2
    assert scenes[0].ndvi is not None
    assert scenes[0].estimated_lai is not None
    
    # Verify DB observations are correctly stored
    stmt = select(Observation).where(Observation.field_id == field_id)
    obs_list = test_db.execute(stmt).scalars().all()
    assert len(obs_list) == 2
    for obs in obs_list:
        assert obs.variable_name == "LAI"
        assert obs.source == ObservationSource.SATELLITE
        assert obs.uncertainty == 0.25
        assert obs.units == "m2/m2"
        assert obs.status == ObservationStatus.VALID
        
    # Verify Batch record is created and matches counts
    stmt_batch = select(ObservationBatch).where(ObservationBatch.field_id == field_id)
    batches = test_db.execute(stmt_batch).scalars().all()
    assert len(batches) == 1
    assert batches[0].number_of_observations == 2
    assert batches[0].processing_status == BatchProcessingStatus.SUCCESS
    
    # 2. Ingest again (deduplication check)
    scenes_2 = service.ingest_lai_observations(
        field_id=field_id,
        start_date=start_date,
        end_date=end_date,
        index_name="NDVI",
        max_cloud_cover=0.2,
        uncertainty=0.40,  # update uncertainty to 0.40
    )
    assert len(scenes_2) == 2
    
    # Verify no new rows were inserted, but existing rows were updated in place
    test_db.expire_all()
    obs_list_2 = test_db.execute(stmt).scalars().all()
    assert len(obs_list_2) == 2  # still exactly 2 rows!
    for obs in obs_list_2:
        assert obs.uncertainty == 0.40  # updated!
        
    # Verify second batch is stored
    batches_2 = test_db.execute(stmt_batch).scalars().all()
    assert len(batches_2) == 2

# ── 5. Router API Route Tests ─────────────────────────────────────────────────

def test_lai_api_endpoint(client):
    # Setup test field via API
    from tests.conftest import FIELD_PAYLOAD
    field_payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON}
    resp = client.post("/fields", json=field_payload)
    assert resp.status_code == 201
    field_id = resp.json()["field_id"]
    
    # Valid API request
    api_resp = client.get(
        "/satellite/lai",
        params={
            "field_id": field_id,
            "start_date": "2024-05-01",
            "end_date": "2024-05-15",
            "index_name": "NDVI",
            "max_cloud_cover": 0.2,
            "uncertainty": 0.35,
        }
    )
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert len(data) == 2
    for item in data:
        assert "acquisition_date" in item
        assert "cloud_cover" in item
        assert "ndvi" in item
        assert "estimated_lai" in item
        assert "quality_score" in item
        assert item["cloud_cover"] <= 0.2
        
    # Invalid field ID
    bad_fid = str(uuid.uuid4())
    api_resp_bad_field = client.get(
        "/satellite/lai",
        params={
            "field_id": bad_fid,
            "start_date": "2024-05-01",
            "end_date": "2024-05-15",
        }
    )
    assert api_resp_bad_field.status_code == 400
    assert "not found" in api_resp_bad_field.json()["detail"].lower()
    
    # Unsupported index name
    api_resp_bad_index = client.get(
        "/satellite/lai",
        params={
            "field_id": field_id,
            "start_date": "2024-05-01",
            "end_date": "2024-05-15",
            "index_name": "INVALID_INDEX",
        }
    )
    assert api_resp_bad_index.status_code == 400
    assert "unsupported vegetation index" in api_resp_bad_index.json()["detail"].lower()
