"""
tests/test_ensemble.py
======================

Unit tests for the Data Assimilation ensemble infrastructure.
"""

import datetime as dt
import pytest

from backend.app.assimilation.ensemble.ensemble_manager import EnsembleManager
from backend.app.assimilation.ensemble.ensemble_member import EnsembleMember
from backend.app.assimilation.state.state_vector import StateVector

def test_ensemble_manager_initialization():
    manager = EnsembleManager()
    assert manager.members == []
    
def test_create_ensemble():
    manager = EnsembleManager()
    manager.create_ensemble(n=5)
    
    assert len(manager.members) == 5
    for member in manager.members:
        assert isinstance(member, EnsembleMember)
        assert "SLATB" in member.perturbed_parameters
        assert "SPAN" in member.perturbed_parameters
        assert member.wofost is not None

def test_run_until_and_extract_states():
    manager = EnsembleManager(sow_date=dt.date(2020, 10, 15), harvest_date=dt.date(2021, 7, 30))
    manager.create_ensemble(n=3)
    
    # Run forward 5 days
    target_date = dt.date(2020, 10, 20)
    manager.run_until(target_date)
    
    # Check that members reached target date
    for member in manager.members:
        assert member.current_date == target_date
        
    # Extract state vectors
    states = manager.extract_state_vectors()
    assert len(states) == 3
    for state in states:
        assert isinstance(state, StateVector)
        assert state.date == target_date
        # Ensure at least soil moisture is initialized
        assert state.sm is not None
