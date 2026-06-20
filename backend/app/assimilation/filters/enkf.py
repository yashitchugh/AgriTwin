"""
backend/app/assimilation/filters/enkf.py
========================================

Core mathematical implementation of the Ensemble Kalman Filter (EnKF).
"""

import numpy as np


def enkf_update(
    X_f: np.ndarray,
    y: np.ndarray,
    R: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Execute the stochastic EnKF update step.
    
    Handles partial observations by dynamically constructing the observation
    operator H based on non-NaN values in the observation vector `y` and
    the forecast ensemble `X_f`.
    
    Args:
        X_f: Forecast ensemble matrix of shape (n, N).
             n = state dimension, N = ensemble size.
             NaN values indicate uninitialized state variables.
        y:   Observation vector of shape (n,).
             Missing observations must be set to np.nan.
        R:   Observation error covariance matrix of shape (n, n).
             
    Returns:
        X_a: Updated analysis ensemble matrix of shape (n, N).
        d:   Innovation vector (y - H*x_mean) of shape (n,). Unobserved variables are NaN.
        K:   Kalman Gain matrix mapped to full state space shape (n, n).
    """
    n, N = X_f.shape
    
    # 1. Identify valid observation indices
    # Must not be NaN in y, and must not have any NaNs in the corresponding row of X_f
    # This prevents math errors if an observed variable hasn't emerged in the simulation yet.
    valid_y = ~np.isnan(y)
    valid_X = ~np.isnan(X_f).any(axis=1)
    
    obs_idx = np.where(valid_y & valid_X)[0]
    
    if len(obs_idx) == 0:
        # No valid observations to assimilate; return forecast as analysis
        return X_f.copy(), np.full(n, np.nan), np.zeros((n, n))
        
    m = len(obs_idx)
    
    # 2. Extract reduced observation vector and covariance
    y_red = y[obs_idx]
    R_red = R[np.ix_(obs_idx, obs_idx)]
    
    # 3. Forecast mean and anomalies
    x_mean = np.nanmean(X_f, axis=1)
    A = X_f - x_mean[:, np.newaxis]
    
    # 4. Apply observation operator H (by slicing valid rows)
    HX_f = X_f[obs_idx, :]
    Hx_mean = x_mean[obs_idx]
    HA = A[obs_idx, :]
    
    # 5. Perturb observations (Stochastic EnKF requirement)
    V = np.random.multivariate_normal(np.zeros(m), R_red, size=N).T
    Y = y_red[:, np.newaxis] + V
    
    # 6. Compute Innovation matrix
    D = Y - HX_f
    
    # Compute mean innovation for tracking
    d_red = y_red - Hx_mean
    d_full = np.full(n, np.nan)
    d_full[obs_idx] = d_red
    
    # 7. Compute covariances
    PHt = (1.0 / (N - 1)) * A @ HA.T
    S = (1.0 / (N - 1)) * HA @ HA.T + R_red
    
    # 8. Compute Kalman Gain K
    K_red = PHt @ np.linalg.inv(S)
    
    K_full = np.zeros((n, n))
    K_full[:, obs_idx] = K_red
    
    # 9. Update ensemble
    X_a = X_f + K_red @ D
    
    return X_a, d_full, K_full
