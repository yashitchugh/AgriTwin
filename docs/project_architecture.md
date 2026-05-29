# project_architecture.md
# AgriTwin — Complete System Architecture

---

## 1. Project Overview

AgriTwin is a **research-grade agricultural digital twin platform** that continuously simulates the state of a real farm using mechanistic crop models, corrects simulation drift using data assimilation, and (in future phases) further refines predictions using AI residual correction models.

The platform enables:
- Real-time crop growth simulation
- Yield and biomass estimation
- Soil moisture and water stress tracking
- Disease risk proxy estimation
- "What-if" scenario simulations
- Continuous synchronization with real-world observations

---

## 2. Core Design Philosophy

### Why Hybrid Physics + AI?

| Approach | Strength | Weakness |
|---|---|---|
| Pure mechanistic (WOFOST only) | Interpretable, causal, generalizes to unseen conditions | Accumulates error, needs perfect inputs |
| Pure AI/ML | Learns complex patterns from data | Needs huge datasets, black box, no physical constraints |
| **Hybrid (this project)** | Physics provides structure, AI corrects residuals, DA syncs to reality | Higher complexity but best of both worlds |

Mechanistic models like WOFOST are built on decades of agronomic science. They generalize across farms and climates without needing historical farm data. AI models supplement this by learning systematic residual errors that the physics model cannot capture (microclimate effects, cultivar-specific deviations, unmodeled soil dynamics).

---

## 3. Architecture Tiers

```
┌──────────────────────────────────────────────────────────────────────┐
│                        AgriTwin System                               │
│                                                                      │
│  ┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐    │
│  │  Data Layer  │    │ Simulation Layer │    │   AI Layer       │    │
│  │              │    │                 │    │  (Future Phase)  │    │
│  │ NASA POWER   │───▶│ PCSE / WOFOST   │───▶│ Crossformer      │    │
│  │ SoilGrids    │    │ AgroManagement  │    │ MMST-ViT         │    │
│  │ Sentinel-2   │    │ EnKF Assimil.   │    │ Residual Corr.   │    │
│  │ (future)     │    │                 │    │                  │    │
│  └──────────────┘    └─────────────────┘    └──────────────────┘    │
│         │                    │                        │              │
│         ▼                    ▼                        ▼              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    FastAPI Backend                           │   │
│  │   /farms  /simulate  /assimilate  /whatif  /observations     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│                              ▼                                       │
│                    ┌─────────────────┐                               │
│                    │   PostgreSQL DB  │                               │
│                    │ farms | states   │                               │
│                    │ weather | obs    │                               │
│                    └─────────────────┘                               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Simulation Pipeline (Current MVP)

```
User Input (lat, lon, crop, sow_date, soil params)
        │
        ▼
┌───────────────────┐
│  Weather Fetcher  │  ← NASA POWER API → daily PCSE WeatherDataProvider
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  Soil Fetcher     │  ← SoilGrids → PCSE SoilDataProvider (YAML)
└───────────────────┘
        │
        ▼
┌───────────────────────┐
│ AgroManagement Builder│  ← Dynamically generates YAML crop calendar
└───────────────────────┘
        │
        ▼
┌────────────────────────┐
│  WOFOST Simulation Run │  ← Wofost72_WLP_FD step-by-step
│  (day-by-day)          │
└────────────────────────┘
        │
        ▼
┌────────────────────────┐
│  State Extraction      │  ← LAI, SM, TAGP, TWSO, DVS at each day
└────────────────────────┘
        │
        ▼
┌────────────────────────┐
│  EnKF Assimilation     │  ← Correct states using LAI observations
│  (when obs available)  │
└────────────────────────┘
        │
        ▼
┌────────────────────────┐
│  PostgreSQL Storage    │  ← daily_state records per farm per date
└────────────────────────┘
        │
        ▼
        API Response (JSON) → Frontend / Dashboard
```

---

## 5. Why WOFOST?

- WOFOST (World Food Studies) is a well-validated, mechanistic crop growth model developed by Wageningen University.
- It simulates **daily** crop growth based on radiation, temperature, water, and soil conditions.
- `Wofost72_WLP_FD` specifically handles **water-limited production** with **freely draining soils** — the most practically useful mode for rainfed or irrigated fields.
- PCSE (Python Crop Simulation Environment) provides a clean Python API for WOFOST.
- It has published crop parameter files for wheat, maize, rice, potato, sunflower, etc.
- Extensive scientific validation literature available.

**Alternatives considered:** DSSAT (complex, Fortran), AquaCrop (less detailed), ORYZA (rice only).

---

## 6. Why Ensemble Kalman Filter (EnKF)?

The WOFOST model accumulates errors over time because:
- Input weather data has uncertainty
- Soil parameters are estimated, not measured
- Crop parameters have cultivar-specific deviations

EnKF corrects the simulation by:
1. Running **N parallel simulations** (ensemble members) with slightly different initial conditions / parameters
2. When an observation arrives (e.g., satellite LAI), it **updates all ensemble member states** to be consistent with the observation
3. The corrected ensemble mean becomes the best estimate of true farm state

EnKF is preferred over standard Kalman Filter because WOFOST is **nonlinear** — standard KF assumes linearity.

---

## 7. What-If Simulation

A "what-if" simulation runs a separate WOFOST instance with **modified management inputs** (e.g., different irrigation schedule, different sowing date) and compares the resulting state trajectory with the baseline.

```
Baseline state (assimilated) → Clone WOFOST state
                                     │
              ┌──────────────────────┘
              │  Apply hypothetical management
              │  (e.g., irrigate +20mm on day 50)
              ▼
         Modified WOFOST run (forward-only, no assimilation)
              │
              ▼
         Compare: TWSO_modified vs TWSO_baseline
                  LAI_modified  vs LAI_baseline
```

What-if simulations always branch from the **last assimilated state**, not from initial conditions, to ensure they reflect current farm reality.

---

## 8. State Synchronization Explanation

Digital twin "synchronization" means the simulated state matches the real farm state at any given point in time.

Without assimilation:
```
Real LAI:      1.2 → 2.1 → 3.5 → 4.2
Simulated LAI: 1.2 → 1.9 → 3.0 → 3.6   ← drift accumulates
```

With EnKF assimilation (observation at day 30):
```
Real LAI:      1.2 → 2.1 → [obs=3.5] → 4.2
Simulated LAI: 1.2 → 1.9 →  [corrected=3.48] → 4.19  ← synchronized
```

The EnKF update pulls the simulated state toward the observation, weighted by the relative uncertainty of the model vs the sensor.

---

## 9. Future AI Integration Plan

**Phase 2 (After MVP):**
- Train a Crossformer or MMST-ViT model on residuals: `real_state - WOFOST_state`
- AI model learns spatiotemporal patterns in these residuals
- At inference: `final_state = WOFOST_state + AI_residual_correction`

**Phase 3:**
- Sentinel-2 satellite image ingestion
- LAI, NDVI, EVI retrieval
- Soil moisture remote sensing integration
- Multi-field cross-farm learning

---

## 10. Modular Backend Design

```
agritwin/
├── api/
│   ├── routes/
│   │   ├── farms.py
│   │   ├── simulate.py
│   │   ├── assimilate.py
│   │   ├── observations.py
│   │   └── whatif.py
│   └── schemas/
│       ├── farm_schema.py
│       ├── simulate_schema.py
│       └── observation_schema.py
├── services/
│   ├── weather_service.py      # NASA POWER fetching + caching
│   ├── soil_service.py         # SoilGrids fetching
│   ├── agro_service.py         # AgroManagement YAML builder
│   ├── simulation_service.py   # WOFOST orchestration
│   ├── assimilation_service.py # EnKF logic
│   └── whatif_service.py       # What-if branching
├── models/
│   ├── farm.py                 # SQLAlchemy ORM
│   ├── simulation_run.py
│   ├── daily_state.py
│   ├── weather_record.py
│   └── observation.py
├── core/
│   ├── config.py               # env vars, settings
│   ├── database.py             # DB session management
│   └── logging.py
└── main.py                     # FastAPI app entry point
```

---

## 11. Data Flow Summary

```
POST /simulate
     │
     ├─▶ weather_service.fetch(lat, lon, start, end)
     │        └─▶ cache check → NASA POWER API → parse → WeatherDataProvider
     │
     ├─▶ soil_service.fetch(lat, lon)
     │        └─▶ SoilGrids API → map to PCSE soil dict
     │
     ├─▶ agro_service.build(crop, sow_date, irrigations)
     │        └─▶ Generate AgroManagement YAML string
     │
     ├─▶ simulation_service.run(weather, soil, agro, crop_params)
     │        └─▶ WOFOST step-by-step → extract daily states
     │
     └─▶ DB: INSERT daily_state records
              RETURN simulation_id + summary stats
```
