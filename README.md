# AgriTwin — Agricultural Digital Twin Platform

> **A physics-based crop simulation backend that fuses process models with real-world geospatial data, designed as the foundation for Ensemble Kalman Filter (EnKF) data assimilation.**

---

## What Is This Project?

AgriTwin is a research and engineering platform that builds a **digital twin of a crop field** — a computational model that mirrors the state of a real field in (near) real time. The core idea is:

1. **Simulate** crop growth using WOFOST, a well-validated process-based crop model developed by Wageningen University (Netherlands).
2. **Ground it in reality** by fetching real weather (NASA POWER satellite-derived data) and real soil hydraulic properties (ISRIC SoilGrids v2.0) for any GPS coordinate on Earth.
3. **Correct it continuously** using field or satellite observations via the Ensemble Kalman Filter — a data assimilation algorithm borrowed from weather forecasting that merges model predictions with noisy measurements to produce optimal state estimates.

The immediate deliverable is a production-ready **REST API** (`POST /simulate`) that runs a full crop simulation for any location and crop configuration. The longer-term goal is an **online assimilation service** that ingests Sentinel-2/Landsat LAI retrievals and corrects the model state daily.

---

## Scientific Context

### Why WOFOST?

WOFOST (World Food Studies) is a deterministic, daily-timestep crop growth model. It simulates:

- **Phenological development** — how temperature and daylength drive a crop from sowing through emergence → anthesis → maturity (quantified via the Development Stage, DVS, which runs 0→2).
- **Photosynthesis and biomass production** — radiation interception by the canopy drives gross assimilation (GASS), which after maintenance respiration is partitioned into leaves, stems, roots, and storage organs (grain/seeds) according to DVS-dependent fraction tables.
- **Water balance** — soil moisture dynamics including rainfall, irrigation, evapotranspiration, and drainage. Water stress reduces transpiration (RFTRA < 1) and consequently suppresses photosynthesis.

WOFOST is the crop model underpinning the EU's [MARS crop monitoring system](https://ec.europa.eu/jrc/en/mars) and is extensively validated for European and tropical cereals. It is implemented in Python via the [PCSE library](https://pcse.readthedocs.io/) (Python Crop Simulation Environment) by Wageningen Environmental Research.

### Why Ensemble Kalman Filter?

The EnKF is a sequential Bayesian estimation algorithm. In the context of crop modelling:

- **State vector**: The key WOFOST state variables at day *t* — `[DVS, LAI, TAGP, TWSO, SM, ...]`
- **Observation**: A satellite-derived LAI estimate for the field on day *t* (from Sentinel-2 or MODIS).
- **Update step**: The EnKF generates an ensemble of N perturbed simulations, computes the Kalman gain from the ensemble covariance, and corrects each member's state toward the observation.

This corrects accumulated errors in weather inputs and crop parameters, yielding much better yield forecasts than open-loop simulation alone — particularly for heterogeneous tropical conditions where model parameters are poorly known.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      FastAPI Backend                      │
│  POST /simulate         GET /simulate/crops               │
│  GET  /health                                             │
└────────────────────┬─────────────────────────────────────┘
                     │ SimulationService.run()
         ┌───────────▼───────────┐
         │   Simulation Engine   │  engine.py → run_simulation()
         │   Wofost72_WLP_FD     │  (PCSE water-limited, free drainage)
         └──┬────────┬───────────┘
            │        │
   ┌────────▼──┐  ┌──▼────────────┐
   │  Weather  │  │    Soil       │
   │  Service  │  │   Service     │
   │NASA POWER │  │ SoilGrids v2  │
   │  + cache  │  │  + cache      │
   └───────────┘  └───────────────┘
            │
   ┌────────▼──────────────────────┐
   │      WOFOST Crop Parameters   │
   │  YAML files (Wageningen WUR)  │
   │  wheat, rice, maize, barley…  │
   └───────────────────────────────┘
```

### Data Flow for a Single Simulation Request

```
Client POST /simulate {lat, lon, crop, variety, sowing_date, ...}
  │
  ▼
Pydantic schema validation (dates, crop lowercase, irrigation bounds)
  │
  ▼
WeatherService.get_weather_provider(lat, lon, start, end)
  ├── Check JSON cache (.agritwin_cache/weather/)
  └── NASA POWER API → unit conversion (MJ→J, mm→cm, °C dew→hPa VAP)
                      → Penman-Monteith ET → WeatherDataProvider
  │
  ▼
SoilService.get_soil_params(lat, lon)
  ├── Check JSON cache (.agritwin_cache/soil/)
  └── SoilGrids v2 API → depth-weighted avg (0-30cm)
                        → wv0033/100 → SMFCF (field capacity)
                        → wv1500/100 → SMW (wilting point)
                        → wv0010/100 → SM0 (saturation)
  │
  ▼
YAMLCropDataProvider(crop/variety)  ← local WOFOST_crop_parameters/
  └── get_crop_start_type()  ← auto-detects transplanted crops (DVSI > 0)
                                e.g., rice → crop_start_type="emergence"
  │
  ▼
build_agromanagement(sowing, harvest, irrigation_events → PCSE TimedEvents)
  │
  ▼
Wofost72_WLP_FD(params, weather, agro).run_till_terminate()
  │
  ▼
parse_batch_output() → list[DailyState{lai, sm, tagp, twso, rftra, dvs, ...}]
parse_summary_output() → PhenologicalSummary{dos, doe, doa, dom, doh, ...}
compute_harvest_metrics() → AgronomicMetrics{yield, HI, peak_lai, ...}
  │
  ▼
SimulateResponse → JSON
```

### Logical Architecture & LLM Context

This structural flow acts as the primary mental model and context sequence for understanding the AgriTwin architecture, especially for LLMs participating in further development:

```text
Farm (Physical grouping, Phase 2)
  ↓
Field (Geospatial entity, holds boundary GeoJSON)
  ↓
Observation Sources (Data ingestion layer)
(WeatherSource, SoilSource, SatelliteSource, SensorSource)
  ↓
WOFOST/PCSE (Crop Simulation Layer)
(Process-based deterministic engine)
  ↓
FieldState (Digital Twin State)
(Live representation of the field's current variables)
  ↓
SimulationRun (Historical Memory)
(Database persistence of the entire simulation campaign)
  ↓
Scenario Engine (What-If Analysis)
    ScenarioDefinition (Blueprint: parameter to vary, baseline, candidates)
            ↓
       ScenarioRuns (Executions of candidate values)
            ↓
    ScenarioComparison (Ranked results, delta metrics, best/lowest finders)
  ↓
REST APIs (FastAPI routes exposing the modules)
```

---

## Current Status (v0.1 — June 2026)

### ✅ Completed & Working

| Component | Status | Notes |
|-----------|--------|-------|
| FastAPI application | ✅ Running | `uvicorn backend.app.main:app --reload` |
| `POST /simulate` endpoint | ✅ Working | Full request/response with validation |
| `GET /simulate/crops` | ✅ Working | Lists all available crops and varieties |
| `GET /health` | ✅ Working | Liveness probe |
| NASA POWER weather ingestion | ✅ Working | Real daily weather, any global location |
| SoilGrids v2 soil ingestion | ✅ Working | SMW, SMFCF, SM0 for any global location |
| Weather + soil JSON caching | ✅ Working | Avoids redundant API calls |
| WOFOST simulation engine | ✅ Working | Wofost72_WLP_FD (water-limited) |
| Synthetic weather fallback | ✅ Working | Offline/testing mode |
| **Irrigation events** | ✅ Working | Timed PCSE AgroManagement events |
| Multi-crop support | ✅ Working | wheat, rice, maize, barley, soybean, … |
| Transplanted rice fix | ✅ Fixed | `DVSI>0` → `crop_start_type="emergence"` |
| Pydantic v2 schemas | ✅ Working | Full validation with user-friendly errors |
| Unit test suite | ✅ Running | `pytest tests/` |
| Swagger/ReDoc docs | ✅ Auto-generated | `localhost:8000/docs` |
| **Observation Sources Interface** | ✅ Working | Interfaces for Weather, Soil, Satellite, and Sensor sources |
| **Scenario Engine** | ✅ Working | Sowing Date, Irrigation, and Variety deterministic sweeps with comparison endpoints |

### 🔬 Scientifically Validated

| Scenario | Result | Notes |
|----------|--------|-------|
| Apache wheat, Netherlands (52°N) | TWSO = 8,394 kg/ha, HI = 0.47 | Realistic for NW Europe |
| Apache wheat, Delhi (28.6°N) | TWSO = 0 kg/ha | **Correct** — heat stress kills grain fill (TMPFTB→0 at 35°C in April-May) |
| Delhi wheat + irrigation | TAGP: 2506→5919 kg/ha | Irrigation fixes water stress, NOT heat stress |
| Rice IR64, Lucknow (26.8°N) | TWSO = 7,272 kg/ha, HI = 0.50 | Realistic Kharif rice yield |
| Rice IR64 without DVSI fix | `ZeroDivisionError: float division by zero` | Fixed by `crop_start_type="emergence"` |

### ⏳ In Progress / Planned

| Phase | Feature | Status |
|-------|---------|--------|
| Phase 2 | PostgreSQL — farm/field/run storage | 🔲 Not started |
| Phase 2 | Celery + Redis — async long-running simulations | 🔲 Not started |
| Phase 3 | **Ensemble Kalman Filter data assimilation** | 🔲 Designed, not implemented |
| Phase 3 | `POST /assimilate` — inject observation, update ensemble | 🔲 Not started |
| Phase 4 | Sentinel-2 / MODIS LAI ingestion | 🔲 Not started |
| Phase 4 | `POST /observations` — store field/satellite observations | 🔲 Not started |
| Phase 5 | AI residual models (correct WOFOST systematic bias) | 🔲 Not started |

---

## Repository Structure

```
AgriTwin/
├── backend/app/
│   ├── main.py                     ← FastAPI app factory, CORS, router mounting
│   ├── core/
│   │   ├── config.py               ← Centralized settings (env vars)
│   │   └── exceptions.py           ← Custom exception hierarchy
│   ├── api/
│   │   ├── routes/simulate.py      ← POST /simulate, GET /simulate/crops
│   │   └── schemas/simulate.py     ← Pydantic request/response models
│   ├── services/
│   │   ├── simulation_service.py   ← Orchestrates full simulation pipeline
│   │   ├── weather_service.py      ← NASA POWER API + JSON caching
│   │   └── soil_service.py         ← SoilGrids v2 API + JSON caching
│   └── simulation/
│       ├── engine.py               ← run_simulation() — core WOFOST runner
│       ├── agromanagement.py       ← AgroManagement YAML builder + transplant detection
│       ├── crop_provider.py        ← YAMLCropDataProvider wrapper
│       ├── soil_provider.py        ← Soil dict validator (SMW < SMFCF < SM0)
│       ├── site_provider.py        ← WOFOST72SiteDataProvider (WAV)
│       ├── weather_provider.py     ← Synthetic + NASA POWER factory
│       └── output_parser.py        ← PCSE raw output → normalized API dicts
├── external_repos/
│   └── WOFOST_crop_parameters/     ← Official WUR crop parameter YAMLs
│       ├── wheat.yaml              ← Includes apache, Winter_wheat_101, …
│       ├── rice.yaml               ← Rice_IR64, IR72, IR8A, …
│       ├── maize.yaml
│       └── …
├── tests/
│   ├── conftest.py
│   └── test_irrigation.py          ← Irrigation regression tests
├── .agritwin_cache/
│   ├── weather/                    ← NASA POWER JSON cache (keyed by lat/lon/dates)
│   └── soil/                       ← SoilGrids JSON cache (keyed by lat/lon)
└── docs/                           ← Design documents
```

---

## Key Design Decisions

### 1. Custom Weather Caching (not PCSE's built-in provider)
PCSE's `NASAPowerWeatherDataProvider` uses pickle caching and requests unbounded date ranges, causing errors on future dates. Our `WeatherService` uses bounded JSON caching with explicit date ranges and a 5-day delay buffer, making it production-safe.

### 2. Transplanted Rice → `crop_start_type = "emergence"`
IRRI rice varieties (IR64, IR72, etc.) have `DVSI > 0` because they model transplanted seedlings, not direct-seeded crops. If `crop_start_type = "sowing"` is used, PCSE's phenology divides by `TSUMEM = 0` → `ZeroDivisionError`. The engine auto-detects `DVSI > 0` and switches to `"emergence"` mode.

### 3. Why Apache Wheat Gives Zero Yield in Delhi
Apache is a French variety calibrated for Central European climates. Its `TMPFTB` response function zeros out photosynthesis at daytime temperatures ≥ 35°C. Delhi's grain-filling window (April–May) has average TMAX of 39–43°C → AMAX ≈ 0 → zero grain fill. This is **scientifically correct** — Apache cannot produce grain in Delhi's climate. The fix for Indian conditions requires a heat-tolerant variety with a modified `TMPFTB`.

### 4. Soil Ordering Guard
SoilGrids occasionally returns `wv0033 ≈ wv1500` for coarse-textured soils. A `_validate_and_fix()` step enforces `SMW < SMFCF < SM0` with minimum 2% separation, preventing PCSE waterbalance crashes.

---

## API Quick Reference

### Run a Simulation

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
       "use_real_soil": true,
       "irrigation_events": [
         {"date": "2021-02-01", "amount_mm": 40},
         {"date": "2021-03-15", "amount_mm": 50}
       ]
     }'
```

### Response Structure

```json
{
  "status": "success",
  "message": "Simulation completed successfully. 305 days simulated, yield = 8394 kg/ha.",
  "metrics": {
    "total_days": 305,
    "peak_lai": 5.751,
    "final_dvs": 2.0,
    "final_tagp_kg_ha": 17802.9,
    "final_twso_kg_ha": 8394.4,
    "harvest_index": 0.472
  },
  "summary": {
    "dos": "2020-10-15", "doe": "2020-10-27",
    "doa": "2021-06-13", "dom": null, "doh": "2021-08-01",
    "laimax": 5.75, "tagp": 17802.9, "twso": 8394.4
  },
  "daily_states": [
    {"date": "2020-10-01", "lai": null, "sm": 0.22, "tagp": null,
     "twso": null, "rftra": null, "dvs": null, "tra": null, "rd": null},
    ...
  ]
}
```

### List Available Crops

```bash
curl http://localhost:8000/simulate/crops
# Returns: {"crops": {"wheat": ["apache", "Winter_wheat_101", ...], "rice": [...], ...}}
```

---

## Running Locally

```bash
# 1. Activate virtual environment
cd /home/vini/Arena/AgriTwin
source venv/bin/activate

# 2. Start the API server
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

# 3. Open interactive docs
# → http://localhost:8000/docs   (Swagger UI)
# → http://localhost:8000/redoc  (ReDoc)

# 4. Run tests
pytest tests/ -v
```

---

## External Dependencies

| Service | Usage | Caching |
|---------|-------|---------|
| [NASA POWER API](https://power.larc.nasa.gov/) | Daily weather (radiation, T, rain, wind, humidity) for any location globally | JSON, 90-day TTL |
| [ISRIC SoilGrids v2](https://www.isric.org/explore/soilgrids) | Soil water retention (field capacity, wilting point, saturation) at 0–30 cm depth | JSON, permanent |
| [PCSE / WOFOST](https://pcse.readthedocs.io/) | Crop growth model engine | — |
| [WUR Crop Parameters](https://github.com/ajwdewit/WOFOST_crop_parameters) | Official WOFOST YAML parameter files | Local clone |

---

## EnKF Design Preview (Phase 3)

The simulation engine is architected for EnKF from the start:

- `engine.py` exposes `step_by_step=True` mode → `wofost.run(days=1)` loop
- `wofost.get_variable(key)` / `wofost.set_variable(key, val)` — state read/write hooks
- `DailyState` schema has documented extension points: `assimilated: bool`, `observation_lai: float`

The planned EnKF loop:
```
for each day t:
    for each ensemble member k:
        member[k].run(days=1)              # advance ensemble
    if observation[t] available:
        K = compute_kalman_gain(ensemble)  # covariance × H^T × (H cov H^T + R)^-1
        for each member k:
            member[k].set_variable('LAI', member[k].LAI + K × (obs - H × member[k].state))
```

---

## Repository

**GitHub:** [github.com/masteranany23/AgriTwin](https://github.com/masteranany23/AgriTwin)

**Branch:** `main`  
**Last commit:** `add irrigation event feature in api`

---

*Built with PCSE 5.x · FastAPI 0.100+ · Pydantic v2 · Python 3.10*
