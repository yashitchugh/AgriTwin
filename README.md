# AgriTwin — Agricultural Digital Twin Platform

> **A physics-based crop simulation digital twin platform that fuses process-based models with real-world geospatial data and sequential data assimilation via the Ensemble Kalman Filter (EnKF).**

---

## 🌟 What Is AgriTwin?

AgriTwin is an advanced research and engineering platform designed to build a **real-time digital twin of crop fields**. By combining physical process-based simulation models with incoming real-world satellite and sensor observations, AgriTwin constantly adjusts and aligns its predictions to mirror physical reality.

The core platform workflows include:
1. **Physical Crop Growth Simulation**: Run the WOFOST (World Food Studies) model to track daily phenology, water balance, and dry matter accumulation under water-limited conditions.
2. **Geospatial Grounding**: Automatically fetch daily weather (NASA POWER API) and soil hydraulic properties (ISRIC SoilGrids v2.0) for any GPS coordinate on Earth, with transparent file-based JSON caching.
3. **Deterministic Scenario sweeps**: Evaluate agricultural strategies (sowing dates, variety selection, irrigation plans) using a comparative Scenario Engine.
4. **Closed-Loop Data Assimilation (EnKF)**: Fuses satellite Leaf Area Index (LAI) and Soil Moisture (SM) observations into the running simulation using the Ensemble Kalman Filter to correct for systematic input biases and forecast drift.

---

## 📐 Logical Architecture Flow

The mental model and data relationship hierarchy of AgriTwin is structured as follows:

```text
Farm (Physical grouping)
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

## 📂 Core Module Directory Breakdown

```
AgriTwin/
├── backend/app/
│   ├── main.py                     ← FastAPI application assembly
│   ├── api/                        ← Core REST routers and schemas
│   │   ├── routes/                 ← fields, simulate, scenarios endpoints
│   │   └── schemas/                ← Pydantic requests & response contracts
│   ├── data_sources/               ← Geospatial API integrations (NASA Power, SoilGrids)
│   ├── db/                         ← Database session, engine, & SQLAlchemy base setup
│   ├── models/                     ← Core DB schemas (Farm, Field, SimulationRun, DailyOutput)
│   ├── scenario/                   ← Scenario Engine (Generators, runners, and comparison)
│   ├── services/                   ← Application orchestration (SimulationService, WeatherService)
│   ├── twin/                       ← Digital twin representation and FieldState schemas
│   ├── simulation/                 ← WOFOST wrappers, Agromanagement builder, weather providers
│   └── assimilation/               ← EnKF Sequential Assimilation Engine
│       ├── api/                    ← EnKF execution and visualization endpoints
│       ├── ensemble/               ← EnsembleManager and perturbed weather/parameters
│       ├── filters/                ← EnKF mathematical update algorithms
│       ├── forecast/               ← Step-by-step state forwarding logic
│       ├── models/                 ← AssimilationRun and AssimilationState models
│       ├── repositories/           ← DB queries for EnKF runs and states
│       ├── schemas/                ← Request payload and visualization schemas
│       ├── services/               ← EnKF loop orchestrator & Visualization compiler
│       ├── state/                  ← StateVector mapping to/from matrices
│       └── updater/                ← PCSE engine state variable injection logic
├── external_repos/
│   └── WOFOST_crop_parameters/     ← Official WUR crop parameter databases (YAMLs)
├── tests/                          ← End-to-end integration and unit tests (290 tests passing)
├── alembic/                        ← DB schema migrations
└── .agritwin_cache/                ← Cached weather and soil JSON payloads
```

### Module Descriptions

*   **`simulation/`**: Interfaces with the Python Crop Simulation Environment (PCSE). Sets up custom parameters, site parameters, timed events (such as irrigation), and runs crop simulations.
*   **`assimilation/ensemble/`**: Perturbs crop parameters (`SLATB`, `SPAN`, `TSUM1`, `TSUM2`) and soil moisture bounds (`SMFCF`, `SMW`) using a Gaussian distribution. Wraps the baseline weather provider in `PerturbedWeatherProvider` to add random temperature/radiation/rain noise for each member.
*   **`assimilation/filters/`**: Computes the covariance of the forecast ensemble, calculates the Kalman Gain based on observation uncertainty, and performs the state update equation for all ensemble members.
*   **`assimilation/state/`**: Maps variable dictionaries to raw state vector arrays. Handles state vectors: `LAI` (Leaf Area Index), `SM` (Soil Moisture), `WLV` (Weight of Leaves), `WST` (Weight of Stems), `WRT` (Weight of Roots), and `WSO` (Weight of Storage Organs/Yield).
*   **`assimilation/updater/`**: Re-evaluates internal WOFOST structural variables (e.g. green leaf area age classes, water balance compartments) so that after the state vector is updated, the engine's physical constraints are satisfied and the model runs stably.
*   **`assimilation/services/`**: Coordinates the forecast-assimilation loop. Discovers assimilation dates, applies Quality Control (QC) filters (Outlier Z-score gating, source filtering, cloud cover thresholds), calls EnKF, persists `AssimilationState` records, and updates the `AssimilationRun` database statistics.

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

## 🎓 Demonstrating EnKF Assimilation (Professor Script)

We have provided a fully automated demonstration script `run_demo_for_professor.py` that sets up a full rice crop campaign, ingests synthetic satellite observations, runs an open-loop simulation, and then performs a closed-loop EnKF assimilation (25 members) to show yield convergence.

Run the demonstration script:
```bash
python3 run_demo_for_professor.py
```

### Script Execution Steps:
1. **Step 1 & 2**: Registers a demo farm and field (Rice IR64, Lucknow, India) and ingests 20 valid Sentinel-2 observations over the season.
2. **Step 3**: Executes the baseline open-loop simulation (yield: **7271.7 kg/ha**).
3. **Step 4**: Triggers the closed-loop EnKF assimilation loop.
4. **Step 5 & 6**: Displays status details and step-by-step cycle history containing prior state, posterior state, innovation, and quality score.
5. **Step 7 & 8**: Prints the convergence of predicted yield (`TWSO`) per cycle (converging to **6133.5 kg/ha**) and outputs a comparison time series of open-loop vs. EnKF vs. observations.

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
