# AgriTwin Developer & LLM Context Specification

This document provides a complete, high-fidelity explanation of the AgriTwin repository's architecture, database design, and key system invariants. Use this file as context for any LLM agent to ensure zero hallucinations and smooth continuity of development.

---

## 🚀 1. Project Overview & Current Status
AgriTwin is a Python/FastAPI Agricultural Digital Twin platform. It uses the Wageningen **PCSE/WOFOST 7.2 (Water-Limited)** simulation engine to model crop development day-by-day. To prevent drift, it integrates satellite/sensor observations via an **Ensemble Kalman Filter (EnKF)** sequential assimilation loop.

**Current Status (June 2026)**:
*   **Database persistence**: Implemented using SQLAlchemy 2.0 + Alembic migrations.
*   **Weather/Soil providers**: Fully implemented with local file caching (`.agritwin_cache/`) for NASA POWER (weather) and SoilGrids (soil properties).
*   **Scenario Sweep Engine**: Operates deterministically to compare sowing dates, crop varieties, and irrigation events.
*   **Sequential EnKF Assimilation**: Completed. Successfully perturbs crop/soil parameters, builds ensembles, carries out mathematical updates on observations, registers cycle metrics, and injects updated variables back into the running PCSE model instances.
*   **Read-only APIs & Demos**: Implemented endpoints for status, step-by-step history, and daily comparative timeseries. The `run_demo.py` automated script successfully runs this entire workflow.
*   **Verification**: All **290 unit and integration tests** pass successfully.

---

## 📂 2. Detailed Module & File Architecture

### A. Core Database & Persistence (`backend/app/models/`, `repositories/`, `db/`)
*   **`models/farm.py`**: Model for physical farm entities. Farms hold metadata and serve as parent groupings for fields.
*   **`models/field.py`**: Holds field boundaries (GeoJSON polygons), elevations (m), centroids (latitude, longitude), and areas (ha). Deleting a field cascade-deletes all associated observations, simulations, and EnKF runs.
*   **`models/simulation_run.py`**: Historical record of simulation campaigns. Stores configuration inputs, phenological summaries, and scalar metrics.
*   **`models/daily_output.py`**: Houses daily high-frequency simulation timeseries outputs. Holds variables: DVS, LAI, TAGP, TWSO, SM, RFTRA, etc.
*   **`models/assimilation_run.py`**: Represents the execution parent of an EnKF loop. Tracks status (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`), and diagnostic parameters (`ensemble_size`, `total_cycles`, `executed_cycles`, `skipped_cycles`, `observations_used`).
*   **`assimilation/models/assimilation_state.py`**: Holds structural JSON matrices of prior, posterior, and observation state vectors per assimilation cycle. Logs innovations and observation quality scores.
*   **`repositories/`**: Houses SQL query services (`field_repository.py`, `simulation_repository.py`, `daily_output_repository.py`, `assimilation_run_repository.py`, `assimilation_state_repository.py`). Handles CRUD operations, database flushes, and cascade purging.

### B. Spatial Ingestion & Adapters (`backend/app/data_sources/`, `services/`)
*   **`data_sources/nasa_power_source.py`**: Interacts with the NASA POWER weather database to fetch daily solar radiation, temperature, rainfall, wind, and humidity. Implements a filesystem JSON cache at `.agritwin_cache/`.
*   **`data_sources/soilgrids_source.py`**: Queries the ISRIC SoilGrids REST API to download sand, clay, and silt fractions at specific field centroids.
*   **`services/soil_service.py`**: Resolves SoilGrids raw data to parameterize WOFOST hydraulic parameters: saturation capacity (`SM0`), field capacity (`SMFCF`), wilting point (`SMW`), and hydraulic conductivity (`K0`) using pedotransfer equations.
*   **`services/weather_service.py`**: Parses weather packets, validates coordinate ranges, and computes crop campaign statistics.
*   **`data_sources/sensor_source.py`**: Handles incoming soil moisture and temperature telemetry from physical IoT sensors.
*   **`data_sources/satellite_source.py`**: Handles Sentinel-2 and MODIS imagery metadata.

### C. Satellite Estimation Pipeline (`backend/app/satellite/`)
*   **`satellite/providers/sentinel2_provider.py`**: Synthesizes or queries Sentinel-2 band imagery for coordinates. Masks clouds using reflectance index bounds.
*   **`satellite/processors/vegetation_indices.py`**: Calculates Normalized Difference Vegetation Index (NDVI) and Enhanced Vegetation Index (EVI) arrays.
*   **`satellite/processors/lai_estimator.py`**: Uses semi-empirical scaling models to translate NDVI/EVI curves to green Leaf Area Index (LAI).
*   **`satellite/services/lai_observation_service.py`**: Runs the Sentinel provider, processes bands, estimates LAI, filters out invalid scenes, and registers them in the database as observations.
*   **`satellite/api/routes.py`**: Exposes the ingestion REST endpoint `GET /satellite/lai`.

### D. WOFOST PCSE Simulation Engine (`backend/app/simulation/`)
*   **`simulation/engine.py`**: Compiles parameter providers and executes the physical WOFOST engine.
*   **`simulation/agromanagement.py`**: Parses agromanagement calendar dictionaries. Corrects the rice transplanting exception (setting `crop_start_type="emergence"` if the transplanting development stage `DVSI` is greater than 0).
*   **`simulation/crop_provider.py` & `soil_provider.py`**: Reads standard crop parameter files (retrieved from `external_repos/WOFOST_crop_parameters`) and generates custom soil parameters.
*   **`simulation/output_parser.py`**: Converts raw arrays of PCSE dictionary outputs into agronomic summaries and phenological stages.

### E. Scenario Optimization Sweeper (`backend/app/scenario/`)
*   **`scenario/generators/sowing_date_generator.py`**: Generates a set of sowing dates around a baseline (e.g., in weekly shifts) to find the optimal planting window.
*   **`scenario/generators/variety_generator.py`**: Generates sweeps across available crop varieties.
*   **`scenario/generators/irrigation_generator.py`**: Generates deficit, timed, or critical-stage irrigation options.
*   **`scenario/services/comparison_engine.py`**: Evaluates simulation outputs from all candidate strategies. Ranks them based on yield (`TWSO`) and Water Use Efficiency (WUE - kg of yield produced per mm of water applied).
*   **`scenario/api/`**: Exposes scenario sweep endpoints (`POST /scenarios/sowing-date`, `POST /scenarios/irrigation`, etc.).

### F. Ensemble Kalman Filter Data Assimilation (`backend/app/assimilation/`)
*   **`assimilation/ensemble/ensemble_manager.py`**: Manages $N$ parallel WOFOST runs. Adds stochastic Gaussian noise to crop parameters (`SLATB`, `SPAN`, `TSUM1`, `TSUM2`) and soil moisture bounds (`SMFCF`, `SMW`). Uses `PerturbedWeatherProvider` to add daily noise to temperature, rainfall, and solar radiation.
*   **`assimilation/state/state_vector.py`**: Defines the EnKF state vector layout. Houses physical variables: `LAI`, `SM` (soil moisture), `WLV` (leaves), `WST` (stems), `WRT` (roots), and `WSO` (storage organs). Converts dict variables to matrices and back.
*   **`assimilation/filters/enkf.py`**: The EnKF mathematical core. Calculates the forecast ensemble covariance ($P^f$), the Kalman Gain ($K$), and updates the state variables of all ensemble members using the observation vector and observation noise covariance ($R$).
*   **`assimilation/updater/state_updater.py`**: Corrects internal physical variables of active WOFOST engines (such as green leaf age class partitions and water balance routing) to match the EnKF updated state vector, preventing model crashes.
*   **`assimilation/services/assimilation_service.py`**: Runs the sequential forecast-assimilation loop over a crop season. Performs Quality Control (QC) filters (Z-score outlier detection, minimum quality index thresholds, and source isolation) and persists `AssimilationState` logs.
*   **`assimilation/services/assimilation_visualization_service.py`**: Compiles visual comparison assets:
    *   *History*: Detailed audit trails of state changes (prior vs posterior) and innovation values.
    *   *Timeseries*: Combines open-loop daily values, observations, and assimilated curves, using a Zero-Order Hold (ZOH) offset projection to predict the corrected development curve.
    *   *Yield Evolution*: Compiles predicted yield (`TWSO`) convergence across successive assimilation steps.
*   **`assimilation/api/assimilation_routes.py`**: Exposes the EnKF API endpoint routes (`POST /assimilation/run`, status checks, history, timeseries, and yield evolution).

---

## 🗄️ 3. Database Schema Layout

```mermaid
erDiagram
    farms ||--o{ fields : "has"
    fields ||--o{ observations : "receives"
    fields ||--o{ simulation_runs : "has"
    simulation_runs ||--o{ daily_outputs : "generates"
    simulation_runs ||--o{ assimilation_runs : "has"
    assimilation_runs ||--o{ assimilation_states : "records"
```

---

## ⚠️ 4. Crucial Architecture Invariants & Rules

When writing code or modifying this repository, **you must respect the following invariants**:

### A. The 14-Day Pre-Season Buffer Invariant
WOFOST simulation campaigns begin **14 days before the sowing date** to initialize soil water balances.
*   **Invariant**: Weather data MUST exist starting from `sowing_date - 14 days`.
*   **Ensemble Manager**: When building ensembles in `EnsembleManager`, the `start_date` passed to `create_weather_provider` must be `sow_date - timedelta(days=14)`. Passing `sow_date` directly will cause `WeatherDataProviderError` because the weather data bounds won't cover the pre-sowing period.

### B. FastAPI SQLite Concurrency Commits
Because FastAPI runs dependency yield blocks (`get_db`) asynchronously after returning the HTTP response, returning a simulation ID before committing the session leads to a race condition where the client requests assimilation using an ID that SQLite hasn't finished writing.
*   **Invariant**: Call `db.commit()` inside POST route handlers (e.g. `/simulate`, `/fields`) *before* returning the JSON response.

### C. Response Validation Contracts
*   `POST /fields` returns `FieldResponse` where the primary ID field is named `field_id` (not `id`).
*   `POST /simulate` returns `SimulateResponse` containing nested `metrics` (e.g. `final_twso_kg_ha`, `peak_lai`) and `summary` (e.g. `doe`, `doh`) blocks.

### D. EnKF State Perturbation Bounds
To keep ensemble members physically plausible and prevent PCSE engine crashes:
*   Crop parameters are perturbed by up to 10% standard deviation.
*   Soil moisture constraints MUST be strictly enforced: `SMW < SMFCF < SM0` (wilting point < field capacity < saturation). `SMFCF` and `SMW` must remain bounded away from `SM0` by at least `0.02`.

### E. Zero-Order Hold Offset Propagation
Because EnKF corrections are applied at discrete observation dates, the comparative daily timeseries API does not re-simulate. Instead, it computes the correction offset (posterior - prior) at the assimilation date and propagates it forward using a **Zero-Order Hold (ZOH)** offset until the next cycle or the season end. This creates a smooth comparative curve between open-loop and assimilated variables.

---

## 💻 5. Standard Setup & Commands

Always use the virtual environment for operations:
```bash
# Activate environment
source venv/bin/activate

# Execute migrations
alembic upgrade head

# Run tests
pytest

# Start development server
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
