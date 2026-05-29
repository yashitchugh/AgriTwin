# enkf_design.md
# AgriTwin — Ensemble Kalman Filter (EnKF) Design

---

## 1. What is the Ensemble Kalman Filter?

The **Ensemble Kalman Filter (EnKF)** is a Monte Carlo variant of the Kalman Filter designed for nonlinear systems. Instead of tracking a single state estimate, it maintains an **ensemble of N parallel state estimates** (ensemble members), each representing a plausible version of the system state with different uncertainty realizations.

When a new observation arrives:
1. Each ensemble member provides a **forecast** of the observable variable
2. The observation is compared to the ensemble forecast mean
3. All ensemble members are **updated** to be more consistent with the observation
4. The ensemble mean after update = best estimate of true state

---

## 2. Why EnKF Instead of Standard Kalman Filter?

| Property | Standard KF | EnKF |
|---|---|---|
| Assumption | Linear model | Nonlinear model ✅ |
| Error propagation | Analytical covariance | Monte Carlo ensemble ✅ |
| Complexity | Simple | Moderate |
| Applicability to WOFOST | ❌ No (nonlinear) | ✅ Yes |

WOFOST is **highly nonlinear** — LAI growth depends on temperature, radiation, and water stress in complex multiplicative ways. Standard Kalman Filter would require linearization (extended KF), which introduces approximation errors. EnKF avoids this entirely.

---

## 3. Mathematical Formulation

### State Vector

For each ensemble member `i`:
```
x_i = [LAI_i, SM_i, TAGP_i, TWSO_i, DVS_i]ᵀ   (5 × 1 vector)
```

For MVP (LAI-only assimilation), we primarily update `LAI` and `SM` since they are coupled in WOFOST.

### Observation Vector

```
y = [LAI_obs]   (scalar in MVP)
```

With observation noise:
```
y_i = y + ε_i,   ε_i ~ N(0, R)
```
Where `R` is the observation error variance (e.g., R = 0.5² = 0.25 for LAI uncertainty of 0.5 m²/m²).

### Ensemble Matrices

```
X = [x_1 | x_2 | ... | x_N]    (n_states × N matrix)
X_anomaly = X - x̄ ⊗ 1ᵀ        (anomaly matrix, x̄ = ensemble mean)
P ≈ (1/(N-1)) × X_anomaly × X_anomaly.T    (ensemble covariance)
```

### Observation Operator H

H maps state vector to observation space:
```
H = [1, 0, 0, 0, 0]    (selects LAI from state vector)
Hx_i = LAI_i           (predicted observation for member i)
```

### Kalman Gain

```
K = P × Hᵀ × (H × P × Hᵀ + R)⁻¹
```

### Update Step

For each ensemble member `i`:
```
x_i_updated = x_i + K × (y_i - H × x_i)
              forecast   gain   innovation
```

Where `innovation = y_i - H × x_i` = (perturbed observation) - (predicted observation)

---

## 4. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    EnKF Assimilation Cycle                       │
│                                                                  │
│   Baseline WOFOST State (x̄_t)                                   │
│           │                                                      │
│           ▼                                                      │
│   Generate N ensemble members:                                   │
│   x_i = x̄_t + perturbation_i   i = 1..N                        │
│           │                                                      │
│           ▼                                                      │
│   Propagate each ensemble member through WOFOST for Δt days     │
│   x_i_forecast = WOFOST(x_i, weather_i, soil_i)                 │
│           │                                                      │
│           ▼                                                      │
│   Compute ensemble mean and covariance P                         │
│           │                                                      │
│   Observation arrives: y_obs (e.g., LAI from Sentinel-2)        │
│           │                                                      │
│           ▼                                                      │
│   Compute Kalman Gain K                                          │
│           │                                                      │
│           ▼                                                      │
│   Update each member: x_i_analysis = x_i_forecast + K × innov   │
│           │                                                      │
│           ▼                                                      │
│   Ensemble mean after update → inject into main WOFOST          │
│           │                                                      │
│           ▼                                                      │
│   Continue forward simulation with corrected state               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Ensemble Generation

```python
import numpy as np

def generate_ensemble(
    base_state: dict,
    n_members: int = 50,
    perturbation_std: dict = None
) -> np.ndarray:
    """
    Generate ensemble matrix from base state.
    
    Returns:
        X: (n_states × n_members) array
    """
    if perturbation_std is None:
        perturbation_std = {
            "LAI": 0.3,     # ± 0.3 m²/m²
            "SM": 0.03,     # ± 0.03 cm³/cm³
            "TAGP": 200.0,  # ± 200 kg/ha
            "TWSO": 50.0,   # ± 50 kg/ha
            "DVS": 0.0,     # DO NOT perturb DVS (thermal-time driven)
        }
    
    STATE_VARS = ["LAI", "SM", "TAGP", "TWSO", "DVS"]
    n_states = len(STATE_VARS)
    
    X = np.zeros((n_states, n_members))
    
    for i, var in enumerate(STATE_VARS):
        base = base_state.get(var, 0.0) or 0.0
        std = perturbation_std.get(var, 0.0)
        if std > 0:
            X[i, :] = base + np.random.normal(0, std, n_members)
            # Enforce physical bounds
            X[i, :] = np.clip(X[i, :], 0.0, None)  # no negatives
        else:
            X[i, :] = base
    
    return X
```

---

## 6. Prediction Step (Propagate Ensemble)

For the prediction step, each ensemble member's state should be propagated through WOFOST. In the simplified MVP approach (single WOFOST with state injection), we approximate this as adding model noise:

```python
def prediction_step(
    X_analysis: np.ndarray,
    model_noise_std: dict = None
) -> np.ndarray:
    """
    Simplified prediction step: add model error noise to propagated states.
    
    In full EnKF: run N separate WOFOST instances.
    In MVP: run one WOFOST, add perturbations to represent ensemble spread.
    """
    if model_noise_std is None:
        model_noise_std = {
            0: 0.1,  # LAI noise std per day
            1: 0.01, # SM noise std per day
            2: 50.0, # TAGP noise std per day
            3: 10.0, # TWSO noise std per day
            4: 0.0,  # DVS — no noise
        }
    
    n_states, n_members = X_analysis.shape
    X_forecast = X_analysis.copy()
    
    for state_idx, noise_std in model_noise_std.items():
        if noise_std > 0:
            X_forecast[state_idx, :] += np.random.normal(0, noise_std, n_members)
            X_forecast[state_idx, :] = np.clip(X_forecast[state_idx, :], 0.0, None)
    
    return X_forecast
```

---

## 7. Update Step (EnKF Core)

```python
def enkf_update(
    X_forecast: np.ndarray,
    obs_value: float,
    obs_uncertainty: float,
    H: np.ndarray = None
) -> np.ndarray:
    """
    EnKF update step.
    
    Args:
        X_forecast: (n_states × n_members) forecast ensemble matrix
        obs_value: scalar observation (e.g., LAI from satellite)
        obs_uncertainty: observation std deviation (e.g., 0.5 for LAI)
        H: (1 × n_states) observation operator (default: selects LAI = index 0)
    
    Returns:
        X_analysis: (n_states × n_members) updated ensemble matrix
    """
    n_states, n_members = X_forecast.shape
    
    # Default H: observe LAI (first state variable)
    if H is None:
        H = np.zeros((1, n_states))
        H[0, 0] = 1.0  # LAI is state index 0
    
    # Observation noise variance
    R = np.array([[obs_uncertainty ** 2]])
    
    # Ensemble mean
    x_mean = X_forecast.mean(axis=1, keepdims=True)
    
    # Anomaly matrix
    X_anom = X_forecast - x_mean
    
    # Ensemble covariance (sample covariance)
    P = (X_anom @ X_anom.T) / (n_members - 1)
    
    # Kalman gain: K = P Hᵀ (H P Hᵀ + R)⁻¹
    HP = H @ P
    S = HP @ H.T + R              # Innovation covariance
    K = P @ H.T @ np.linalg.inv(S)  # (n_states × 1)
    
    # Perturbed observations: y_i = obs + noise_i
    obs_noise = np.random.normal(0, obs_uncertainty, (1, n_members))
    Y_perturbed = obs_value + obs_noise    # (1 × n_members)
    
    # Predicted observations from ensemble: H x_i
    HX = H @ X_forecast    # (1 × n_members)
    
    # Innovation for each member: y_i - H x_i
    innovation = Y_perturbed - HX    # (1 × n_members)
    
    # Update
    X_analysis = X_forecast + K @ innovation    # (n_states × n_members)
    
    # Physical bounds enforcement
    X_analysis = np.clip(X_analysis, 0.0, None)
    
    return X_analysis
```

---

## 8. Full EnKF Assimilation Service

```python
# services/assimilation_service.py

import numpy as np
from pcse.models import Wofost72_WLP_FD

STATE_VARS = ["LAI", "SM", "TAGP", "TWSO", "DVS"]
STATE_INDEX = {v: i for i, v in enumerate(STATE_VARS)}

class AssimilationService:
    
    def __init__(self, n_members: int = 50):
        self.n_members = n_members
    
    def assimilate(
        self,
        wofost: Wofost72_WLP_FD,
        state: dict,          # current WOFOST state dict
        observation: dict     # {"LAI": 3.2, "uncertainty": 0.5}
    ) -> dict:
        """
        Run one EnKF update cycle and return corrected state dict.
        Injects corrected LAI and SM back into WOFOST.
        """
        
        # 1. Extract observed variable
        obs_var = "LAI"
        obs_value = observation["LAI"]
        obs_uncertainty = observation.get("uncertainty", 0.5)
        
        # 2. Generate ensemble from current state
        X = generate_ensemble(state, self.n_members)
        
        # 3. Prediction step (add model noise)
        X_forecast = prediction_step(X)
        
        # 4. Update step
        H = np.zeros((1, len(STATE_VARS)))
        H[0, STATE_INDEX[obs_var]] = 1.0
        
        X_analysis = enkf_update(X_forecast, obs_value, obs_uncertainty, H)
        
        # 5. Ensemble mean = best estimate
        x_mean = X_analysis.mean(axis=1)
        
        # 6. Build corrected state dict
        corrected_state = state.copy()
        for var, idx in STATE_INDEX.items():
            if var != "DVS":  # Never correct DVS
                corrected_state[var.lower()] = float(x_mean[idx])
        
        # 7. Inject corrected LAI and SM into WOFOST
        try:
            wofost.set_variable("LAI", corrected_state["lai"])
            wofost.set_variable("SM", corrected_state["sm"])
        except Exception as e:
            logger.warning(f"State injection failed: {e}")
        
        return corrected_state
```

---

## 9. Injecting Corrected States into WOFOST

```python
def inject_state(wofost, corrected_state: dict):
    """
    Inject EnKF-corrected states back into WOFOST.
    
    Only inject variables that WOFOST accepts externally.
    DO NOT inject DVS — it's thermally driven and resetting it
    will break phenological consistency.
    """
    INJECTABLE = ["LAI", "SM"]
    
    for var in INJECTABLE:
        val = corrected_state.get(var.lower())
        if val is not None and val >= 0:
            try:
                wofost.set_variable(var, val)
            except Exception as e:
                logger.warning(f"Cannot inject {var}: {e}")
```

---

## 10. EnKF Parameters (Tunable)

| Parameter | Default | Description |
|---|---|---|
| `N` (ensemble size) | 50 | More members = better covariance estimate, slower |
| `R_LAI` | 0.25 (std=0.5) | LAI observation error variance |
| LAI perturbation std | 0.3 | Initial ensemble spread for LAI |
| SM perturbation std | 0.03 | Initial ensemble spread for SM |
| Model noise LAI std | 0.1/day | Process noise added during prediction |
| Model noise SM std | 0.01/day | Process noise for SM |

---

## 11. Pseudo-Code Summary

```
FOR each simulation day:
    wofost.run(1 day)
    state = extract_state(wofost)
    
    IF observation available on this day:
        X = generate_ensemble(state, N=50)
        X_f = add_model_noise(X)
        X_a = enkf_update(X_f, obs_LAI, obs_uncertainty)
        x_corrected = mean(X_a)
        inject_to_wofost(wofost, x_corrected)
        state = x_corrected
    
    store_state(state, db)
```

---

## 12. Common EnKF Pitfalls

| Problem | Cause | Fix |
|---|---|---|
| Filter divergence | R too small (overconfident obs) | Increase R |
| Ensemble collapse | N too small or no model noise | Increase N, add process noise |
| Negative LAI after update | No physical bounds | `np.clip(X_analysis, 0.0, None)` |
| DVS gets reset incorrectly | Injecting DVS | Never inject DVS |
| Large jump in TAGP | Ensemble spread too wide | Reduce TAGP perturbation std |
