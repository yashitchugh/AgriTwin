# AgriTwin — Agricultural Digital Twin Platform

> **A physics-based crop simulation digital twin platform that fuses process-based models with real-world geospatial data and sequential data assimilation via the Ensemble Kalman Filter (EnKF).**

---

## 🌟 What Is AgriTwin?

AgriTwin is an advanced research and engineering platform designed to build a **real-time digital twin of crop fields**. By combining physical process-based simulation models with incoming real-world satellite and sensor observations, AgriTwin constantly adjusts and aligns its predictions to mirror physical reality.

The core platform workflows include:
1. **Physical Crop Growth Simulation**: Run the WOFOST (World Food Studies) model to track daily phenology, water balance, and dry matter accumulation under water-limited conditions.
2. **Geospatial Grounding**: Automatically fetch daily weather (NASA POWER API) and soil hydraulic properties (ISRIC SoilGrids v2.0) for any GPS coordinate on Earth, with transparent file-based JSON caching.
3. **Deterministic Scenario Sweeps**: Evaluate agricultural strategies (sowing dates, variety selection, irrigation plans) using a comparative Scenario Engine.
4. **Closed-Loop Data Assimilation (EnKF)**: Fuses satellite Leaf Area Index (LAI) and Soil Moisture (SM) observations into the running simulation using the Ensemble Kalman Filter to correct for systematic input biases and forecast drift.

---

## 📐 Logical Architecture Flow

The mental model and data relationship hierarchy of AgriTwin is structured as follows:

```text
Farm (Physical grouping of fields)
  ↓
Field (Geospatial boundary GeoJSON, location & elevation)
  ↓
Observations Ingestion Layer
(Ingests external satellite LAI/SM or soil sensors into the DB)
  ↓
Simulation Layer (WOFOST/PCSE Engine)
(Daily timestep crop growth, water balance, and transpiration modeling)
  ├─► SimulationRun (Open-loop execution memory & DailyOutputs)
  └─► AssimilationRun (Closed-loop EnKF manager & execution diagnostics)
        ↓
    EnsembleManager (N parallel perturbed crop parameters & weather instances)
        ↓
    AssimilationService (Forecasts and executes the EnKF updates)
        ↓
    Filters (EnKF math updating state vectors: LAI, SM, WLV, WST, WSO, WRT)
        ↓
    StateUpdater (Injects corrected states back into the running PCSE engines)
        ↓
    AssimilationState (Persisted historical correction logs per cycle)
  ↓
REST APIs (FastAPI routes exposing simulation, scenario, and assimilation APIs)
```

---

## 📂 Comprehensive Directory & Module Guide

### 1. Geospatial & DB Core (`models/`, `repositories/`, `db/`)
*   **`models/farm.py`**: Maps physical farms. Organizes multiple fields belonging to a single user.
*   **`models/field.py`**: Represents field polygons (using boundary GeoJSON), centroids, area size (ha), and elevation (m). Deleting a field cascade-deletes all associated observations, simulations, and assimilation runs.
*   **`models/simulation_run.py`**: Historical records of baseline, irrigated, or assimilated simulation campaigns. Stores request payload snapshots, aggregate agronomic metrics, and phenological summary dates.
*   **`models/daily_output.py`**: High-frequency daily time series table storing standard WOFOST variables (`LAI`, `DVS`, `TAGP`, `TWSO`, `SM`, `RFTRA`, etc.) for every day of the simulation.
*   **`repositories/`**: Fast SQLAlchemy database access layer for saving, retrieving, and paginating farms, fields, daily outputs, simulations, and assimilation states.

### 2. External Adapters & Data Sources (`data_sources/`, `services/`)
*   **`data_sources/nasa_power_source.py`**: Interacts with the NASA POWER API to query daily weather parameters (solar radiation, minimum/maximum temperature, precipitation, wind speed, vapor pressure) for any global coordinate. Saves raw queries to `.agritwin_cache/` to avoid hitting API rate limits.
*   **`data_sources/soilgrids_source.py`**: Queries the ISRIC SoilGrids v2.0 API to fetch sand, clay, and silt percentages across multiple depth layers.
*   **`services/soil_service.py`**: Maps SoilGrids texture classes to hydraulic parameters (wilting point `SMW`, field capacity `SMFCF`, saturation `SM0`, saturated conductivity `K0`, critical air content `CRAIRC`) using standard pedotransfer equations.
*   **`services/weather_service.py`**: Packages raw cached weather containers and computes temperature/radiation indices (e.g. temperature averages during grain fill).
*   **`data_sources/sensor_source.py`**: Standardized ingestion interface for local IoT soil moisture and temperature sensors.
*   **`data_sources/satellite_source.py`**: Ingests processed satellite scenes from Sentinel-2 or MODIS.

### 3. Satellite Processing Pipeline (`satellite/`)
*   **`satellite/providers/sentinel2_provider.py`**: Queries or synthetically generates Sentinel-2 scenes for a given field boundary and date range. Automatically applies cloud-masking algorithms.
*   **`satellite/processors/vegetation_indices.py`**: Computes NDVI (Normalized Difference Vegetation Index) and EVI (Enhanced Vegetation Index) from raw spectral bands.
*   **`satellite/processors/lai_estimator.py`**: Converts vegetation index curves into Leaf Area Index (LAI) estimates.
*   **`satellite/services/lai_observation_service.py`**: Runs the Sentinel-2 query, masks clouds, extracts LAI estimations, and ingests them into the database as field observations.
*   **`satellite/api/routes.py`**: Exposes the `GET /satellite/lai` ingestion endpoint.

### 4. WOFOST Simulation Engine (`simulation/`)
*   **`simulation/engine.py`**: High-level execution class that integrates weather providers, crop parameter files, and soil properties to instantiate the PCSE WOFOST engine.
*   **`simulation/agromanagement.py`**: Compiles crop sowing parameters and timed irrigation events. Corrects the rice transplanting exception (setting `crop_start_type="emergence"` if the transplanting development stage `DVSI` is greater than 0).
*   **`simulation/crop_provider.py` & `soil_provider.py`**: Reads standard crop parameter files (retrieved from `external_repos/WOFOST_crop_parameters`) and generates custom soil parameters.
*   **`simulation/output_parser.py`**: Converts raw arrays of PCSE dictionary outputs into agronomic summaries and phenological stages.

### 5. Scenario Sweeper Engine (`scenario/`)
*   **`scenario/generators/sowing_date_generator.py`**: Generates a set of sowing dates around a baseline (e.g., in weekly shifts) to find the optimal planting window.
*   **`scenario/generators/variety_generator.py`**: Generates sweeps across available crop varieties (e.g., IR64 vs other rice variety parameters).
*   **`scenario/generators/irrigation_generator.py`**: Generates deficit, timed, or critical-stage irrigation options.
*   **`scenario/services/comparison_engine.py`**: Evaluates simulation outputs from all candidate strategies. Ranks them based on yield (`TWSO`) and Water Use Efficiency (WUE - kg of yield produced per mm of water applied).
*   **`scenario/api/`**: Exposes scenario sweep endpoints (`POST /scenarios/sowing-date`, `POST /scenarios/irrigation`, etc.).

### 6. Ensemble Kalman Filter Data Assimilation (`assimilation/`)
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

## ⚙️ Running Locally

### 1. Environment Setup
```bash
# Clone the repository and navigate to the directory
cd /home/vini/Arena/AgriTwin

# Activate the virtual environment
source venv/bin/activate

# Apply database migrations
alembic upgrade head
```

### 2. Start the FastAPI API Server
The server runs with reload enabled on port 8000:
```bash
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```
Interactive docs are then available at: [http://localhost:8000/docs](http://localhost:8000/docs) (Swagger UI).

### 3. Run the Verification Tests
To run all 290 unit and integration tests:
```bash
pytest
```

---

## 🎓 Demonstrating EnKF Assimilation (Demo Script)

We have provided a fully automated demonstration script `run_demo.py` that sets up a full rice crop campaign, ingests synthetic satellite observations, runs an open-loop simulation, and then performs a closed-loop EnKF assimilation (25 members) to show yield convergence.

Run the demonstration script:
```bash
python3 run_demo.py
```

### Script Execution Steps:
1. **Step 1 & 2**: Registers a demo farm and field (Rice IR64, Lucknow, India) and ingests 20 valid Sentinel-2 observations over the season.
2. **Step 3**: Executes the baseline open-loop simulation (yield: **7271.7 kg/ha**).
3. **Step 4**: Triggers the closed-loop EnKF assimilation loop.
4. **Step 5 & 6**: Displays status details and step-by-step cycle history containing prior state, posterior state, innovation, and quality score.
5. **Step 7 & 8**: Prints the convergence of predicted yield (`TWSO`) per cycle (converging to **5774.36 kg/ha**) and outputs a comparison time series of open-loop vs. EnKF vs. observations.

---

## 📡 API Reference Quickstart

### 1. Run Baseline Crop Simulation
```bash
curl -X POST http://localhost:8000/simulate \
     -H 'Content-Type: application/json' \
     -d '{
       "latitude": 26.8,
       "longitude": 80.9,
       "crop": "rice",
       "variety": "Rice_IR64",
       "sowing_date": "2020-06-20",
       "harvest_date": "2020-11-10",
       "use_real_weather": true,
       "use_real_soil": true
     }'
```

### 2. Trigger EnKF Assimilation Run
```bash
curl -X POST http://localhost:8000/assimilation/run \
     -H 'Content-Type: application/json' \
     -d '{
       "simulation_id": "YOUR_BASELINE_SIMULATION_UUID",
       "field_id": "YOUR_FIELD_UUID",
       "ensemble_size": 25
     }'
```

### 3. Fetch Assimilation Cycle History
Returns the chronological audit trail of update dates, priors, posteriors, innovations, and quality metrics:
```bash
curl -X GET http://localhost:8000/assimilation/YOUR_BASELINE_SIMULATION_UUID/history
```

### 4. Fetch Timeseries Comparison
Retrieves daily comparison data points mapping open-loop state, EnKF-assimilated state, and satellite observations:
```bash
curl -X GET http://localhost:8000/assimilation/YOUR_BASELINE_SIMULATION_UUID/timeseries
```
