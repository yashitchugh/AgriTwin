"""
backend/app/assimilation/forecast/forecast_step.py
==================================================

Executes the forecast step of the Ensemble Kalman Filter (EnKF).
"""

import datetime as dt
import warnings
from typing import Tuple

import numpy as np

from backend.app.assimilation.ensemble.ensemble_manager import EnsembleManager


def forecast_until(
    manager: EnsembleManager, 
    target_date: dt.date
) -> Tuple[np.ndarray, np.ndarray]:
    """Advance the ensemble to target_date and extract the forecast matrices.
    
    Args:
        manager: The initialized EnsembleManager containing N ensemble members.
        target_date: The date to run the simulation forward to.
        
    Returns:
        X_f: The forecast ensemble matrix of shape (state_dim, N).
        x_mean: The ensemble mean vector of shape (state_dim,).
    """
    # 1. Run all ensemble members forward to the target date
    manager.run_until(target_date)
    
    # 2. Extract state vectors
    states = manager.extract_state_vectors()
    
    if not states:
        raise ValueError("EnsembleManager returned empty state vectors. Were members created?")
    
    # 3. Assemble forecast matrix X_f (shape: state_dim x N)
    # Each StateVector.to_numpy() returns a 1D array of shape (state_dim,)
    vectors = [state.to_numpy(fill_value=np.nan) for state in states]
    X_f = np.column_stack(vectors)
    
    # 4. Compute ensemble mean x_mean (shape: state_dim,)
    # Use np.nanmean to ignore undefined variables (e.g., TWSO before emergence).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        x_mean = np.nanmean(X_f, axis=1)
    
    return X_f, x_mean
