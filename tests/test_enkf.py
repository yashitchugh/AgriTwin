"""
tests/test_enkf.py
==================

Unit tests for the stochastic Ensemble Kalman Filter mathematical implementation.
"""

import numpy as np
import pytest

from backend.app.assimilation.filters.enkf import enkf_update

def test_enkf_update_full_observation():
    n = 3  # state dimension
    N = 1000  # large ensemble to test statistical properties
    
    # Generate ensemble
    X_f = np.random.randn(n, N) * 2.0  # Forecast variance ~ 4.0
    
    # True state
    x_true = np.array([5.0, -2.0, 10.0])
    
    # Observation with noise
    R = np.eye(n) * 1.0  # Obs variance = 1.0
    y = x_true + np.random.multivariate_normal(np.zeros(n), R)
    
    X_a, d, K = enkf_update(X_f, y, R)
    
    # Shape checks
    assert X_a.shape == (n, N)
    assert d.shape == (n,)
    assert K.shape == (n, n)
    
    # Since R is much smaller than forecast variance, analysis mean should pull strongly toward y
    x_a_mean = np.mean(X_a, axis=1)
    
    # Check that x_a_mean is closer to y than the original forecast mean (~0.0)
    dist_forecast_to_y = np.linalg.norm(np.mean(X_f, axis=1) - y)
    dist_analysis_to_y = np.linalg.norm(x_a_mean - y)
    assert dist_analysis_to_y < dist_forecast_to_y

def test_enkf_update_partial_observation():
    n = 3
    N = 100
    X_f = np.random.randn(n, N)
    
    # Only observe the first variable
    y = np.array([5.0, np.nan, np.nan])
    R = np.eye(n)
    
    X_a, d, K = enkf_update(X_f, y, R)
    
    # Innovation should only exist for the observed variable
    assert not np.isnan(d[0])
    assert np.isnan(d[1])
    assert np.isnan(d[2])
    
    # K should only be non-zero in the first column
    assert np.any(K[:, 0] != 0.0)
    assert np.all(K[:, 1] == 0.0)
    assert np.all(K[:, 2] == 0.0)
    
def test_enkf_update_with_missing_state():
    n = 3
    N = 100
    X_f = np.random.randn(n, N)
    # The third variable is entirely missing (e.g. TWSO before emergence)
    X_f[2, :] = np.nan
    
    y = np.array([1.0, 2.0, 3.0])
    R = np.eye(n)
    
    X_a, d, K = enkf_update(X_f, y, R)
    
    # The valid observations should only be 0 and 1, because X_f[2] is NaN
    assert not np.isnan(d[0])
    assert not np.isnan(d[1])
    assert np.isnan(d[2])  # Despite y having a value, it shouldn't be assimilated
    
    # The third variable in X_a should remain NaN
    assert np.all(np.isnan(X_a[2, :]))

def test_enkf_update_no_valid_observations():
    n = 3
    N = 10
    X_f = np.random.randn(n, N)
    y = np.array([np.nan, np.nan, np.nan])
    R = np.eye(n)
    
    X_a, d, K = enkf_update(X_f, y, R)
    
    # Analysis should be exactly forecast
    np.testing.assert_array_equal(X_a, X_f)
    assert np.all(np.isnan(d))
    assert np.all(K == 0.0)
