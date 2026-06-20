"""
tests/test_assimilation_state.py
================================

Unit tests for AssimilationState model and repository.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy.orm import Session

from backend.app.assimilation.models.assimilation_state import AssimilationState
from backend.app.assimilation.repositories.assimilation_state_repository import AssimilationStateRepository
from backend.app.models.field import Field


from backend.app.models.farm import Farm

@pytest.fixture
def mock_farm(test_db: Session) -> Farm:
    farm = Farm(name="Test Farm")
    test_db.add(farm)
    test_db.commit()
    test_db.refresh(farm)
    return farm


@pytest.fixture
def mock_field(test_db: Session, mock_farm: Farm) -> Field:
    """Fixture to provide a Field instance for FK testing."""
    field = Field(
        farm_id=mock_farm.id,
        name="EnKF Test Field",
        latitude=52.0,
        longitude=5.5,
        area_ha=10.0,
    )
    test_db.add(field)
    test_db.commit()
    test_db.refresh(field)
    return field


def test_save_and_retrieve_assimilation_state(test_db: Session, mock_field: Field):
    repo = AssimilationStateRepository(test_db)
    
    state = AssimilationState(
        field_id=mock_field.id,
        assimilation_time=dt.datetime(2024, 6, 15, 12, 0, tzinfo=dt.timezone.utc),
        ensemble_mean={"LAI": 2.5, "SM": 0.25},
        ensemble_covariance={"LAI": [0.1], "SM": [0.01]},
        observation_vector={"LAI": 2.8},
        innovation_vector={"LAI": 0.3},
        kalman_gain={"LAI": [0.5]},
        updated_state_vector={"LAI": 2.65, "SM": 0.25},
        forecast_state_vector={"LAI": 2.5, "SM": 0.25},
        number_of_members=50,
        observation_count=1,
    )
    
    saved_state = repo.save_state(state)
    assert saved_state.id is not None
    assert saved_state.ensemble_mean["LAI"] == 2.5
    
    # Test get_latest
    latest = repo.get_latest(mock_field.id)
    assert latest is not None
    assert latest.id == saved_state.id
    
    # Add an older state
    older_state = AssimilationState(
        field_id=mock_field.id,
        assimilation_time=dt.datetime(2024, 6, 14, 12, 0, tzinfo=dt.timezone.utc),
        ensemble_mean={"LAI": 2.0, "SM": 0.26},
        ensemble_covariance={"LAI": [0.1], "SM": [0.01]},
        observation_vector={"LAI": 2.1},
        innovation_vector={"LAI": 0.1},
        kalman_gain={"LAI": [0.5]},
        updated_state_vector={"LAI": 2.05, "SM": 0.26},
        forecast_state_vector={"LAI": 2.0, "SM": 0.26},
        number_of_members=50,
        observation_count=1,
    )
    repo.save_state(older_state)
    
    # get_latest should still return the one from the 15th
    latest_again = repo.get_latest(mock_field.id)
    assert latest_again is not None
    assert latest_again.id == saved_state.id
    
    # Test get_history
    history = repo.get_history(mock_field.id)
    assert len(history) == 2
    # Should be ordered by assimilation_time ASC
    assert history[0].id == older_state.id
    assert history[1].id == saved_state.id
