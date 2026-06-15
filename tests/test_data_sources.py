"""
tests/test_data_sources.py — Data Source Interface Tests
=========================================================

Tests:
  A. WeatherSource ABC — interface contract
  B. SoilSource ABC — interface contract
  C. NasaPowerWeatherSource (synthetic mode, no real API calls)
  D. SoilGridsSource (mocked, no real API calls)
  E. SatelliteSource stub interface
  F. SensorSource stub interface
  G. boundary_geojson persistence via DB
  H. Field CRUD endpoints with boundary_geojson
  I. No regression — existing tests still pass
"""

import datetime as dt
import uuid
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.app.data_sources.weather_source import WeatherSource
from backend.app.data_sources.soil_source import SoilSource
from backend.app.data_sources.satellite_source import SatelliteSource, SatelliteObservation
from backend.app.data_sources.sensor_source import SensorSource, SensorObservation
from backend.app.data_sources.nasa_power_source import NasaPowerWeatherSource
from backend.app.data_sources.soilgrids_source import SoilGridsSource
from backend.app.models.field import Field
from backend.app.models.farm import Farm

from tests.conftest import FIELD_PAYLOAD, SIMULATE_PAYLOAD


# ── helpers ──────────────────────────────────────────────────────────────────

SAMPLE_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[
        [80.89, 26.79], [80.91, 26.79],
        [80.91, 26.81], [80.89, 26.81],
        [80.89, 26.79],
    ]],
}

SAMPLE_GEOJSON_FEATURE = {
    "type": "Feature",
    "geometry": SAMPLE_GEOJSON,
    "properties": {"name": "Kharif Field"},
}


# ═══════════════════════════════════════════════════════════════════════════════
# A. WeatherSource ABC
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeatherSourceABC:
    """WeatherSource is an ABC — concrete subclasses must implement get_weather()."""

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            WeatherSource()

    def test_concrete_subclass_must_implement_get_weather(self):
        """A concrete subclass that doesn't implement get_weather() raises TypeError."""
        class Incomplete(WeatherSource):
            pass
        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_works_when_implemented(self):
        class MyWeather(WeatherSource):
            def get_weather(self, lat, lon, start, end, *, elevation=10.0):
                return "mock_provider"

        src = MyWeather()
        result = src.get_weather(52.0, 5.5, dt.date(2020, 1, 1), dt.date(2020, 12, 31))
        assert result == "mock_provider"

    def test_get_source_name_default(self):
        class MyWeather(WeatherSource):
            def get_weather(self, lat, lon, start, end, *, elevation=10.0):
                return None
        src = MyWeather()
        assert src.get_source_name() == "MyWeather"

    def test_get_source_name_can_be_overridden(self):
        class MyWeather(WeatherSource):
            def get_weather(self, lat, lon, start, end, *, elevation=10.0):
                return None
            def get_source_name(self):
                return "Custom Weather"
        assert MyWeather().get_source_name() == "Custom Weather"


# ═══════════════════════════════════════════════════════════════════════════════
# B. SoilSource ABC
# ═══════════════════════════════════════════════════════════════════════════════

class TestSoilSourceABC:
    """SoilSource is an ABC — concrete subclasses must implement get_soil()."""

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            SoilSource()

    def test_concrete_subclass_must_implement_get_soil(self):
        class Incomplete(SoilSource):
            pass
        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_works_when_implemented(self):
        class MySoil(SoilSource):
            def get_soil(self, lat, lon, *, rdmsol=120.0, force_update=False):
                return {"SMFCF": 0.30, "SMW": 0.10, "SM0": 0.45,
                        "CRAIRC": 0.06, "RDMSOL": rdmsol,
                        "K0": 10.0, "SOPE": 10.0, "KSUB": 10.0}

        src = MySoil()
        result = src.get_soil(28.6, 77.2)
        assert "SMFCF" in result
        assert "SMW" in result
        assert "SM0" in result
        assert result["SMW"] < result["SMFCF"] < result["SM0"]

    def test_get_source_name_default(self):
        class MySoil(SoilSource):
            def get_soil(self, lat, lon, *, rdmsol=120.0, force_update=False):
                return {}
        assert MySoil().get_source_name() == "MySoil"


# ═══════════════════════════════════════════════════════════════════════════════
# C. NasaPowerWeatherSource (synthetic mode — no real API)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNasaPowerWeatherSource:
    """Tests for NasaPowerWeatherSource in synthetic mode (use_real=False)."""

    def test_is_weather_source_subclass(self):
        assert issubclass(NasaPowerWeatherSource, WeatherSource)

    def test_instantiation_synthetic(self):
        src = NasaPowerWeatherSource(use_real=False)
        assert src is not None

    def test_get_source_name_synthetic(self):
        src = NasaPowerWeatherSource(use_real=False)
        assert src.get_source_name() == "Synthetic"

    def test_get_source_name_real(self):
        src = NasaPowerWeatherSource(use_real=True)
        assert src.get_source_name() == "NASA POWER"

    def test_get_weather_synthetic_returns_provider(self):
        """Synthetic mode returns a PCSE WeatherDataProvider."""
        from pcse.base import WeatherDataProvider
        src = NasaPowerWeatherSource(use_real=False)
        provider = src.get_weather(
            52.0, 5.5,
            dt.date(2020, 1, 1), dt.date(2020, 12, 31),
        )
        assert isinstance(provider, WeatherDataProvider)

    def test_get_weather_synthetic_covers_date_range(self):
        """Provider generated by synthetic mode covers the full requested period."""
        from pcse.base import WeatherDataProvider
        src = NasaPowerWeatherSource(use_real=False)
        provider = src.get_weather(
            26.8, 80.9,
            dt.date(2020, 6, 1), dt.date(2020, 11, 30),
        )
        # Should be able to request a day in range without KeyError
        day = provider(dt.date(2020, 8, 15))
        assert day is not None
        assert day.LAT == pytest.approx(26.8)

    def test_get_weather_synthetic_uses_correct_lat_lon(self):
        src = NasaPowerWeatherSource(use_real=False)
        provider = src.get_weather(
            28.6, 77.2,
            dt.date(2020, 10, 1), dt.date(2021, 7, 31),
        )
        day = provider(dt.date(2020, 11, 1))
        assert day.LAT == pytest.approx(28.6)
        assert day.LON == pytest.approx(77.2)

    def test_get_weather_real_delegates_to_weather_service(self):
        """use_real=True mode delegates to WeatherService — verify with mock."""
        mock_provider = MagicMock()
        mock_service = MagicMock()
        mock_service.get_weather_provider.return_value = mock_provider

        src = NasaPowerWeatherSource(use_real=True)
        src._weather_service = mock_service  # inject mock

        result = src.get_weather(
            52.0, 5.5,
            dt.date(2020, 10, 1), dt.date(2021, 7, 31),
        )
        mock_service.get_weather_provider.assert_called_once_with(
            52.0, 5.5, dt.date(2020, 10, 1), dt.date(2021, 7, 31),
        )
        assert result is mock_provider

    def test_lazy_service_init_not_called_in_synthetic_mode(self):
        """WeatherService should NOT be instantiated if use_real=False."""
        src = NasaPowerWeatherSource(use_real=False)
        src.get_weather(52.0, 5.5, dt.date(2020, 1, 1), dt.date(2020, 12, 31))
        assert src._weather_service is None


# ═══════════════════════════════════════════════════════════════════════════════
# D. SoilGridsSource (mocked — no real API calls)
# ═══════════════════════════════════════════════════════════════════════════════

MOCK_SOIL_PARAMS = {
    "SMFCF": 0.30, "SMW": 0.10, "SM0": 0.45,
    "CRAIRC": 0.06, "RDMSOL": 120.0,
    "K0": 10.0, "SOPE": 10.0, "KSUB": 10.0,
}


class TestSoilGridsSource:
    """Tests for SoilGridsSource — delegates to SoilService (mocked)."""

    def test_is_soil_source_subclass(self):
        assert issubclass(SoilGridsSource, SoilSource)

    def test_get_source_name(self):
        src = SoilGridsSource()
        assert src.get_source_name() == "SoilGrids v2.0"

    def test_get_soil_delegates_to_soil_service(self):
        """get_soil() must call SoilService.get_soil_params() with correct args."""
        mock_service = MagicMock()
        mock_service.get_soil_params.return_value = MOCK_SOIL_PARAMS.copy()

        src = SoilGridsSource()
        src._soil_service = mock_service  # inject mock

        result = src.get_soil(28.6, 77.2, rdmsol=100.0)

        mock_service.get_soil_params.assert_called_once_with(
            latitude=28.6,
            longitude=77.2,
            rdmsol=100.0,
            force_update=False,
        )
        assert result["SMFCF"] == 0.30
        assert result["SMW"] < result["SMFCF"] < result["SM0"]

    def test_get_soil_force_update_passed_through(self):
        mock_service = MagicMock()
        mock_service.get_soil_params.return_value = MOCK_SOIL_PARAMS.copy()

        src = SoilGridsSource()
        src._soil_service = mock_service

        src.get_soil(52.0, 5.5, force_update=True)
        call_kwargs = mock_service.get_soil_params.call_args.kwargs
        assert call_kwargs["force_update"] is True

    def test_get_soil_default_rdmsol(self):
        mock_service = MagicMock()
        mock_service.get_soil_params.return_value = MOCK_SOIL_PARAMS.copy()

        src = SoilGridsSource()
        src._soil_service = mock_service

        src.get_soil(52.0, 5.5)
        call_kwargs = mock_service.get_soil_params.call_args.kwargs
        assert call_kwargs["rdmsol"] == 120.0

    def test_lazy_service_not_created_at_init(self):
        """SoilService must NOT be instantiated at SoilGridsSource.__init__()."""
        src = SoilGridsSource()
        assert src._soil_service is None


# ═══════════════════════════════════════════════════════════════════════════════
# E. SatelliteSource stub
# ═══════════════════════════════════════════════════════════════════════════════

class TestSatelliteSourceStub:
    """SatelliteSource is a stub ABC — verify the interface contract."""

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            SatelliteSource()

    def test_concrete_subclass_must_implement_get_observations(self):
        class Incomplete(SatelliteSource):
            pass
        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_subclass_works(self):
        class MockSat(SatelliteSource):
            def get_observations(self, lat, lon, start, end, *,
                                  variables=None, boundary_geojson=None):
                return [SatelliteObservation(date=dt.date.today(), variable="LAI", value=2.5)]

        src = MockSat()
        obs = src.get_observations(52.0, 5.5, dt.date(2020, 1, 1), dt.date(2020, 12, 31))
        assert len(obs) == 1
        assert obs[0].variable == "LAI"
        assert obs[0].value == 2.5

    def test_satellite_observation_dataclass(self):
        obs = SatelliteObservation(
            date=dt.date(2021, 4, 15),
            variable="LAI",
            value=3.2,
            uncertainty=0.5,
            source="Sentinel-2 L2A",
            cloud_cover=0.05,
        )
        assert obs.date == dt.date(2021, 4, 15)
        assert obs.variable == "LAI"
        assert obs.cloud_cover == 0.05
        assert obs.field_id is None

    def test_boundary_geojson_passed_to_get_observations(self):
        """boundary_geojson from Field model is threaded through to SatelliteSource."""
        received = {}

        class MockSat(SatelliteSource):
            def get_observations(self, lat, lon, start, end, *,
                                  variables=None, boundary_geojson=None):
                received["geojson"] = boundary_geojson
                return []

        src = MockSat()
        src.get_observations(
            26.8, 80.9,
            dt.date(2020, 6, 1), dt.date(2020, 11, 30),
            boundary_geojson=SAMPLE_GEOJSON,
        )
        assert received["geojson"] == SAMPLE_GEOJSON
        assert received["geojson"]["type"] == "Polygon"


# ═══════════════════════════════════════════════════════════════════════════════
# F. SensorSource stub
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensorSourceStub:
    """SensorSource is a stub ABC — verify the interface contract."""

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            SensorSource()

    def test_concrete_subclass_works(self):
        class MockSensor(SensorSource):
            def get_observations(self, lat, lon, start, end, *,
                                  variables=None, field_id=None):
                return [SensorObservation(
                    timestamp=dt.datetime(2020, 7, 1, 8, 0),
                    variable="SM", value=0.24, depth_cm=10.0,
                )]

        src = MockSensor()
        obs = src.get_observations(26.8, 80.9, dt.date(2020, 6, 1), dt.date(2020, 11, 30))
        assert len(obs) == 1
        assert obs[0].variable == "SM"
        assert obs[0].value == pytest.approx(0.24)

    def test_sensor_observation_quality_flag_default(self):
        obs = SensorObservation(
            timestamp=dt.datetime(2020, 8, 1, 6, 0),
            variable="SM", value=0.22,
        )
        assert obs.quality_flag == 0

    def test_sensor_observation_all_fields(self):
        fid = uuid.uuid4()
        obs = SensorObservation(
            timestamp=dt.datetime(2020, 9, 15, 10, 30),
            variable="TRA", value=0.35,
            uncertainty=0.02, depth_cm=5.0,
            sensor_id="PROBE-001", field_id=fid, quality_flag=1,
        )
        assert obs.sensor_id == "PROBE-001"
        assert obs.field_id == fid
        assert obs.quality_flag == 1


# ═══════════════════════════════════════════════════════════════════════════════
# G. boundary_geojson persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestBoundaryGeoJsonPersistence:
    """Verify boundary_geojson round-trips through the database correctly."""

    def test_field_model_has_boundary_geojson_attribute(self):
        f = Field()
        assert hasattr(f, "boundary_geojson")

    def test_boundary_geojson_defaults_to_none(self):
        f = Field()
        assert f.boundary_geojson is None

    def test_none_boundary_persists_and_reloads(self, test_engine):
        farm_id = uuid.uuid4()
        field_id = uuid.uuid4()
        with Session(test_engine) as db:
            db.add(Farm(id=farm_id, name="Test Farm GJ"))
            db.flush()
            db.add(Field(
                id=field_id, farm_id=farm_id,
                name="No Boundary Field",
                latitude=26.8, longitude=80.9,
                boundary_geojson=None,
            ))
            db.commit()

        with Session(test_engine) as db:
            loaded = db.get(Field, field_id)
            assert loaded.boundary_geojson is None

    def test_polygon_geojson_persists_and_reloads(self, test_engine):
        farm_id = uuid.uuid4()
        field_id = uuid.uuid4()
        with Session(test_engine) as db:
            db.add(Farm(id=farm_id, name="Farm GJ Polygon"))
            db.flush()
            db.add(Field(
                id=field_id, farm_id=farm_id,
                name="Polygon Boundary Field",
                latitude=26.8, longitude=80.9,
                boundary_geojson=SAMPLE_GEOJSON,
            ))
            db.commit()

        with Session(test_engine) as db:
            loaded = db.get(Field, field_id)
            assert loaded.boundary_geojson is not None
            assert loaded.boundary_geojson["type"] == "Polygon"
            assert "coordinates" in loaded.boundary_geojson

    def test_feature_geojson_persists_and_reloads(self, test_engine):
        farm_id = uuid.uuid4()
        field_id = uuid.uuid4()
        with Session(test_engine) as db:
            db.add(Farm(id=farm_id, name="Farm GJ Feature"))
            db.flush()
            db.add(Field(
                id=field_id, farm_id=farm_id,
                name="Feature Boundary Field",
                latitude=26.8, longitude=80.9,
                boundary_geojson=SAMPLE_GEOJSON_FEATURE,
            ))
            db.commit()

        with Session(test_engine) as db:
            loaded = db.get(Field, field_id)
            assert loaded.boundary_geojson["type"] == "Feature"
            assert loaded.boundary_geojson["geometry"]["type"] == "Polygon"
            assert loaded.boundary_geojson["properties"]["name"] == "Kharif Field"

    def test_complex_geojson_with_properties_preserved(self, test_engine):
        """Arbitrary JSON structure is stored and returned without modification."""
        complex_geojson = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[77.1, 28.5], [77.3, 28.5], [77.3, 28.7], [77.1, 28.7], [77.1, 28.5]]]
            },
            "properties": {
                "area_ha": 4.5,
                "soil_type": "sandy loam",
                "irrigation": True,
                "elevation_range": [210, 225],
            }
        }
        farm_id = uuid.uuid4()
        field_id = uuid.uuid4()
        with Session(test_engine) as db:
            db.add(Farm(id=farm_id, name="Farm Complex GJ"))
            db.flush()
            db.add(Field(
                id=field_id, farm_id=farm_id,
                name="Complex GeoJSON Field",
                latitude=28.6, longitude=77.2,
                boundary_geojson=complex_geojson,
            ))
            db.commit()

        with Session(test_engine) as db:
            loaded = db.get(Field, field_id)
            gj = loaded.boundary_geojson
            assert gj["properties"]["soil_type"] == "sandy loam"
            assert gj["properties"]["irrigation"] is True
            assert gj["properties"]["elevation_range"] == [210, 225]


# ═══════════════════════════════════════════════════════════════════════════════
# H. Field CRUD endpoints with boundary_geojson
# ═══════════════════════════════════════════════════════════════════════════════

class TestFieldCRUDWithBoundary:
    """Verify POST/GET /fields works correctly with boundary_geojson."""

    def test_post_field_without_boundary_returns_null(self, client):
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        assert resp.status_code == 201
        assert resp.json()["boundary_geojson"] is None

    def test_post_field_with_polygon_boundary(self, client):
        payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON}
        resp = client.post("/fields", json=payload)
        assert resp.status_code == 201
        gj = resp.json()["boundary_geojson"]
        assert gj is not None
        assert gj["type"] == "Polygon"
        assert "coordinates" in gj

    def test_post_field_with_feature_boundary(self, client):
        payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON_FEATURE}
        resp = client.post("/fields", json=payload)
        assert resp.status_code == 201
        gj = resp.json()["boundary_geojson"]
        assert gj["type"] == "Feature"

    def test_get_field_returns_boundary_geojson(self, client):
        payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON}
        post = client.post("/fields", json=payload)
        field_id = post.json()["field_id"]

        get_resp = client.get(f"/fields/{field_id}")
        assert get_resp.status_code == 200
        gj = get_resp.json()["boundary_geojson"]
        assert gj["type"] == "Polygon"

    def test_list_fields_includes_boundary_geojson(self, client):
        payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON,
                   "name": "Listed Boundary Field"}
        client.post("/fields", json=payload)
        resp = client.get("/fields")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1
        # At least one field has boundary_geojson (the one we just created)
        with_boundary = [f for f in items if f["boundary_geojson"] is not None]
        assert len(with_boundary) >= 1

    def test_boundary_geojson_in_response_schema(self, client):
        """FieldResponse schema must include boundary_geojson key even when None."""
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        body = resp.json()
        assert "boundary_geojson" in body

    def test_delete_field_with_boundary_works(self, client):
        payload = {**FIELD_PAYLOAD, "boundary_geojson": SAMPLE_GEOJSON}
        post = client.post("/fields", json=payload)
        field_id = post.json()["field_id"]
        del_resp = client.delete(f"/fields/{field_id}")
        assert del_resp.status_code == 204
        assert client.get(f"/fields/{field_id}").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# I. No regression
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoRegression:
    """All existing endpoint behaviour must be unaffected by these changes."""

    def test_post_simulate_still_works(self, client):
        resp = client.post("/simulate", json=SIMULATE_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_field_crud_without_boundary_unchanged(self, client):
        resp = client.post("/fields", json=FIELD_PAYLOAD)
        assert resp.status_code == 201
        fid = resp.json()["field_id"]
        assert client.get(f"/fields/{fid}").status_code == 200
        assert client.delete(f"/fields/{fid}").status_code == 204

    def test_get_simulations_still_works(self, client):
        client.post("/simulate", json=SIMULATE_PAYLOAD)
        resp = client.get("/simulations")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_health_check_still_works(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("healthy", "degraded", "ok")
