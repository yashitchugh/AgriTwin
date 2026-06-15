# AgriTwin — Agricultural Digital Twin Platform

> **A physics-based crop simulation backend that fuses process models with real-world geospatial data, designed as the foundation for Ensemble Kalman Filter (EnKF) data assimilation.**

---

## What Is This Project?

AgriTwin is a research and engineering platform that builds a **digital twin of a crop field** — a computational model that mirrors the state of a real field in (near) real time. The core idea is:

1. **Simulate** crop growth using WOFOST, a well-validated process-based crop model developed by Wageningen University (Netherlands).
2. **Ground it in reality** by fetching real weather (NASA POWER satellite-derived data) and real soil hydraulic properties (ISRIC SoilGrids v2.0) for any GPS coordinate on Earth.
3. **Analyze and Optimize** using the deterministic Scenario Engine to compute what-if variations around a baseline (e.g., varying sowing dates, irrigation levels, and crop varieties).
4. **Correct it continuously** (Phase 3 Planned) using field or satellite observations via the Ensemble Kalman Filter — merging model predictions with noisy measurements to produce optimal state estimates.

---

## Logical Architecture & LLM Context

This structural flow acts as the primary mental model and context sequence for understanding the AgriTwin architecture, especially for LLMs participating in further development:

```text
Farm (Physical grouping)
  ↓
Field (Geospatial entity, holds boundary GeoJSON and location)
  ↓
Observation Sources (Data ingestion layer)
(WeatherSource, SoilSource, SatelliteSource, SensorSource)
  ↓
WOFOST/PCSE (Crop Simulation Layer)
(Process-based deterministic physics engine)
  ↓
FieldState (Digital Twin State)
(Live representation of the field's current variables: LAI, DVS, TAGP, SM)
  ↓
SimulationRun (Historical Memory)
(Database persistence of the entire simulation campaign and DailyOutputs)
  ↓
Scenario Engine (What-If Analysis)
    ScenarioDefinition (Blueprint: parameter to vary, baseline, candidates)
            ↓
       ScenarioRuns (Executions of candidate values reusing the base simulation)
            ↓
    ScenarioComparison (Ranked results, delta metrics, best/lowest finders)
  ↓
REST APIs (FastAPI routes exposing the modules to consumers)
```

---

## Scientific Context

### Why WOFOST?

WOFOST (World Food Studies) is a deterministic, daily-timestep crop growth model. It simulates:
- **Phenological development** — temperature and daylength driving a crop from sowing through emergence → anthesis → maturity (DVS 0→2).
- **Photosynthesis and biomass** — radiation interception drives gross assimilation (GASS), partitioned into leaves, stems, roots, and storage organs.
- **Water balance** — soil moisture dynamics (rainfall, irrigation, ET, drainage). Water stress reduces transpiration (RFTRA < 1) and suppresses photosynthesis.

### Why Ensemble Kalman Filter? (Planned)

The EnKF is a sequential Bayesian estimation algorithm. In crop modelling:
- **State vector**: `[DVS, LAI, TAGP, TWSO, SM, ...]`
- **Observation**: Satellite-derived LAI or soil moisture.
- **Update step**: Corrects accumulated errors in weather inputs and crop parameters, yielding much better yield forecasts than open-loop simulation alone.

---

## System Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Backend                               │
│  /simulate      /simulations      /fields      /scenarios              │
└────────┬──────────────┬─────────────────┬───────────┬──────────────────┘
         │              │                 │           │
         │              │           ┌─────▼─────┐     │
         │              │           │ Database  │     │ (Orchestration)
         │              │           │ (SQLite/  │ ┌───▼───────────────┐
         │              │           │ Postgres) │ │  ScenarioService  │
         │              │           └───▲───────┘ │  (Generators,     │
         │              │               │         │   Comparison)     │
         │      ┌───────▼───────────────▼─┐       └───┬───────────────┘
         │      │     SimulationService   │◄──────────┘
         └─────►│  (Simulation orchestr.) │
                └───────┬────────┬────────┘
                        │        │
   ┌────────▼──┐  ┌─────▼──┐  ┌──▼────────────┐
   │  Weather  │  │  Crop  │  │    Soil       │
   │  Source   │  │ Source │  │   Source      │
   │NASA POWER │  │ YAMLs  │  │ SoilGrids v2  │
   │  + cache  │  │        │  │  + cache      │
   └───────────┘  └────────┘  └───────────────┘
```

---

## Current Status (v0.2 — June 2026)

### ✅ Completed & Working

| Component | Status | Notes |
|-----------|--------|-------|
| FastAPI application | ✅ Running | Core foundation |
| SQL Database Persistence | ✅ Working | SQLAlchemy + Alembic Migrations |
| `Farm` & `Field` Models | ✅ Working | Geolocation & boundary storage |
| **Observation Sources** | ✅ Working | Unified abstract API for ingestion |
| NASA POWER weather ingestion | ✅ Working | Real daily weather globally |
| SoilGrids v2 soil ingestion | ✅ Working | SMW, SMFCF, SM0 globally |
| Weather + soil JSON caching | ✅ Working | Avoids redundant API calls |
| WOFOST simulation engine | ✅ Working | Wofost72_WLP_FD (water-limited) |
| Synthetic weather fallback | ✅ Working | Offline/testing mode |
| Irrigation events | ✅ Working | Timed PCSE AgroManagement events |
| Multi-crop support | ✅ Working | wheat, rice, maize, barley, soybean, … |
| Transplanted rice fix | ✅ Fixed | `DVSI>0` → `crop_start_type="emergence"` |
| Pydantic v2 schemas | ✅ Working | Full validation |
| **Digital Twin Abstraction** | ✅ Working | `FieldState` unifies simulation output |
| **Scenario Engine** | ✅ Working | Sowing Date, Irrigation, and Variety deterministic sweeps with Comparison Engine |
| Unit test suite | ✅ Running | `pytest tests/` (160+ passing tests) |

### 🔬 Scientifically Validated Examples

| Scenario | Result | Notes |
|----------|--------|-------|
| Apache wheat, Netherlands (52°N) | TWSO = 8,394 kg/ha, HI = 0.47 | Realistic for NW Europe |
| Apache wheat, Delhi (28.6°N) | TWSO = 0 kg/ha | **Correct** — heat stress kills grain fill (TMPFTB→0 at 35°C in April-May) |
| Delhi wheat + irrigation | TAGP: 2506→5919 kg/ha | Irrigation fixes water stress, NOT heat stress |
| Rice IR64, Lucknow (26.8°N) | TWSO = 7,272 kg/ha, HI = 0.50 | Realistic Kharif rice yield |

### ⏳ In Progress / Planned

| Phase | Feature | Status |
|-------|---------|--------|
| Phase 2 | Celery + Redis — async long-running simulations | 🔲 Planned |
| Phase 3 | **Ensemble Kalman Filter data assimilation** | 🔲 Designed, not implemented |
| Phase 3 | `POST /assimilate` — inject observation, update ensemble | 🔲 Planned |
| Phase 4 | Sentinel-2 / MODIS LAI ingestion pipelines | 🔲 Planned |
| Phase 5 | AI residual models (correct WOFOST systematic bias) | 🔲 Planned |

---

## API Quick Reference

### Run a Single Simulation

```bash
curl -X POST http://localhost:8000/simulate \
     -H 'Content-Type: application/json' \
     -d '{
       "latitude": 52.0,
       "longitude": 5.5,
       "crop": "wheat",
       "variety": "apache",
       "sowing_date": "2020-10-15",
       "harvest_date": "2021-08-01",
       "use_real_weather": true,
       "use_real_soil": true
     }'
```

### Run an Irrigation Scenario Sweep

Automatically tests multiple irrigation strategies (Rainfed, 2-event, 4-event, 6-event) to find the most efficient schedule.

```bash
curl -X POST http://localhost:8000/scenarios/irrigation \
     -H 'Content-Type: application/json' \
     -d '{
       "latitude": 28.6,
       "longitude": 77.2,
       "crop": "wheat",
       "variety": "apache",
       "sowing_date": "2020-10-15",
       ...
     }'
```

---

## Running Locally

```bash
# 1. Activate virtual environment
cd /home/vini/Arena/AgriTwin
source venv/bin/activate

# 2. Run Database Migrations (Creates SQLite DB if not exists)
alembic upgrade head

# 3. Start the API server
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 4. Open interactive docs
# → http://localhost:8000/docs   (Swagger UI)

# 5. Run tests
pytest tests/ -v
```

---

## Repository Structure

```
AgriTwin/
├── backend/app/
│   ├── main.py                     ← FastAPI app factory
│   ├── api/                        ← Route definitions & Pydantic schemas
│   ├── data_sources/               ← Interfaces for Weather, Soil, Satellite
│   ├── db/                         ← SQLAlchemy Base, Session, and Mixins
│   ├── models/                     ← ORM Definitions (Farm, Field, SimulationRun)
│   ├── scenario/                   ← Scenario Engine (Generators, Runners, Comparison)
│   ├── services/                   ← Application core (SimulationService)
│   ├── twin/                       ← Digital Twin abstractions (FieldState)
│   └── simulation/                 ← Core WOFOST wrappers & PCSE runners
├── external_repos/
│   └── WOFOST_crop_parameters/     ← Official WUR crop parameter YAMLs
├── tests/                          ← Extensive integration and unit tests
├── alembic/                        ← Database migration scripts
├── .agritwin_cache/                ← API JSON caches (Weather, Soil)
└── docs/                           ← Design documents
```

---
*Built with PCSE 5.x · FastAPI 0.100+ · SQLAlchemy 2.0 · Alembic · Pydantic v2 · Python 3.10*
