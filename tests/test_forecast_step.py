"""
tests/test_forecast_step.py
===========================

Unit tests for the forecast step of the EnKF pipeline.
"""

import datetime as dt
import numpy as np

from backend.app.assimilation.ensemble.ensemble_manager import EnsembleManager
from backend.app.assimilation.forecast.forecast_step import forecast_until
from backend.app.assimilation.state.state_vector import STATE_DIM


def test_forecast_until():
    sow_date = dt.date(2020, 10, 15)
    harvest_date = dt.date(2021, 7, 30)
    
    manager = EnsembleManager(sow_date=sow_date, harvest_date=harvest_date)
    manager.create_ensemble(n=3)
    
    target_date = dt.date(2020, 10, 25)
    
    X_f, x_mean = forecast_until(manager, target_date)
    
    # Check shape
    assert X_f.shape == (STATE_DIM, 3)
    assert x_mean.shape == (STATE_DIM,)
    
    # Check that members actually reached target date
    for member in manager.members:
        assert member.current_date == target_date
        
    # SM (Soil Moisture, index 1) should be populated for all members
    # Ensure no NaNs in SM mean
    assert not np.isnan(x_mean[1])
    
    # Check that ensemble isn't perfectly identical (since we perturbed parameters)
    # SM shouldn't be identical across all 3 members
    sm_row = X_f[1, :]
    assert np.var(sm_row) > 0.0
