# database_schema.md
# AgriTwin — PostgreSQL Database Schema

---

## 1. Overview

AgriTwin uses PostgreSQL for persisting all farm configurations, simulation states, weather records, and field observations. The schema is designed for:
- **Temporal queries** — retrieving state time series for a farm
- **Efficient lookups** — indexed by farm_id + date
- **Incremental updates** — daily states appended one row at a time
- **Caching** — weather and soil data stored to avoid repeated API calls

---

## 2. Entity Relationship Diagram

```
farms
  │
  ├──────────────────────────┐
  │                          │
  ▼                          ▼
simulation_runs          observations
  │
  ▼
daily_states

weather_records (independent — keyed by lat/lon/date)
soil_cache      (independent — keyed by lat/lon)
```

---

## 3. Table: farms

Stores farm metadata and soil configuration.

```sql
CREATE TABLE farms (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    latitude        FLOAT NOT NULL,
    longitude       FLOAT NOT NULL,
    crop_type       VARCHAR(100) NOT NULL,        -- e.g., 'wheat'
    variety_name    VARCHAR(255) NOT NULL,         -- e.g., 'Winter_wheat_101'
    sow_date        DATE NOT NULL,
    harvest_date    DATE NOT NULL,
    
    -- Soil parameters (from SoilGrids or user override)
    smfcf           FLOAT NOT NULL DEFAULT 0.32,  -- field capacity cm³/cm³
    smw             FLOAT NOT NULL DEFAULT 0.12,  -- wilting point cm³/cm³
    sm0             FLOAT NOT NULL DEFAULT 0.45,  -- saturation cm³/cm³
    rdmsol          FLOAT NOT NULL DEFAULT 100.0, -- max root depth cm
    crairc          FLOAT NOT NULL DEFAULT 0.06,
    sope            FLOAT NOT NULL DEFAULT 1.47,
    ksub            FLOAT NOT NULL DEFAULT 1.47,
    k0              FLOAT NOT NULL DEFAULT 10.0,
    
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_farms_latlon ON farms(latitude, longitude);
```

---

## 4. Table: simulation_runs

Tracks each simulation execution (baseline or what-if).

```sql
CREATE TABLE simulation_runs (
    id              SERIAL PRIMARY KEY,
    farm_id         INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    run_type        VARCHAR(50) NOT NULL DEFAULT 'baseline',
                    -- 'baseline', 'whatif', 'assimilated'
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
                    -- 'pending', 'running', 'completed', 'failed'
    
    -- What-if metadata
    scenario_json   JSONB,                 -- irrigation overrides, etc.
    parent_run_id   INTEGER REFERENCES simulation_runs(id),
    
    -- Summary outputs
    final_twso      FLOAT,                 -- final grain yield kg/ha
    max_lai         FLOAT,                 -- peak LAI during season
    total_rain      FLOAT,                 -- total rainfall mm
    total_irrigation FLOAT,               -- total irrigation mm
    
    created_at      TIMESTAMP DEFAULT NOW(),
    completed_at    TIMESTAMP
);

CREATE INDEX idx_simruns_farm ON simulation_runs(farm_id);
CREATE INDEX idx_simruns_status ON simulation_runs(status);
```

---

## 5. Table: daily_states

Core time series table — one row per farm per simulation day.

```sql
CREATE TABLE daily_states (
    id              SERIAL PRIMARY KEY,
    farm_id         INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    simulation_run_id INTEGER NOT NULL REFERENCES simulation_runs(id),
    date            DATE NOT NULL,
    
    -- Core state variables
    lai             FLOAT,     -- Leaf Area Index (m²/m²)
    sm              FLOAT,     -- Soil Moisture (cm³/cm³)
    tagp            FLOAT,     -- Total Above-Ground Biomass (kg/ha)
    twso            FLOAT,     -- Storage Organ Weight / Yield proxy (kg/ha)
    dvs             FLOAT,     -- Development Stage (0–2)
    
    -- Diagnostic variables
    tra             FLOAT,     -- Actual Transpiration (cm/day)
    rd              FLOAT,     -- Rooting Depth (cm)
    wlv             FLOAT,     -- Leaf Weight (kg/ha)
    wst             FLOAT,     -- Stem Weight (kg/ha)
    
    -- Derived indicators
    disease_risk    FLOAT,     -- 0–1 score (computed externally)
    water_stress    FLOAT,     -- 0–1 stress factor
    
    -- Assimilation metadata
    assimilated     BOOLEAN DEFAULT FALSE,   -- was EnKF applied this day?
    lai_corrected   FLOAT,                  -- EnKF-corrected LAI (before/after)
    
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Primary lookup index: farm states over time
CREATE INDEX idx_daily_states_farm_date 
    ON daily_states(farm_id, date);

-- For simulation run queries
CREATE INDEX idx_daily_states_run 
    ON daily_states(simulation_run_id);

-- For latest state queries
CREATE INDEX idx_daily_states_farm_date_desc 
    ON daily_states(farm_id, date DESC);

-- Unique constraint: one state per farm per run per day
CREATE UNIQUE INDEX idx_daily_states_unique 
    ON daily_states(farm_id, simulation_run_id, date);
```

---

## 6. Table: weather_records

Caches daily weather data from NASA POWER. Shared across farms at similar locations.

```sql
CREATE TABLE weather_records (
    id          SERIAL PRIMARY KEY,
    latitude    FLOAT NOT NULL,
    longitude   FLOAT NOT NULL,
    date        DATE NOT NULL,
    
    -- Core weather variables (PCSE units)
    tmax        FLOAT,     -- Max temperature (°C)
    tmin        FLOAT,     -- Min temperature (°C)
    rain        FLOAT,     -- Rainfall (cm/day)
    irrad       FLOAT,     -- Solar radiation (J/m²/day)
    wind        FLOAT,     -- Wind speed (m/s)
    vap         FLOAT,     -- Vapour pressure (kPa)
    
    -- Reference ET
    e0          FLOAT,     -- Penman open water ET (cm/day)
    es0         FLOAT,     -- Penman bare soil ET (cm/day)
    et0         FLOAT,     -- Penman-Monteith reference ET (cm/day)
    
    -- Metadata
    source      VARCHAR(50) DEFAULT 'NASA_POWER',
    created_at  TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_weather_latlon_date UNIQUE (latitude, longitude, date)
);

-- Spatial + temporal lookup
CREATE INDEX idx_weather_latlon_date 
    ON weather_records(latitude, longitude, date);

-- Date range queries
CREATE INDEX idx_weather_date 
    ON weather_records(date);
```

**Note:** Lat/lon are stored as rounded to 2 decimal places to group nearby farms to the same weather grid point:
```python
lat_key = round(lat, 2)
lon_key = round(lon, 2)
```

---

## 7. Table: observations

Stores field or satellite LAI (and future variables) observations.

```sql
CREATE TABLE observations (
    id              SERIAL PRIMARY KEY,
    farm_id         INTEGER NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
    obs_date        DATE NOT NULL,
    
    -- Observed variables (nullable — only fill what is known)
    lai_observed    FLOAT,          -- LAI from satellite/field (m²/m²)
    sm_observed     FLOAT,          -- Soil moisture (cm³/cm³)
    ndvi_observed   FLOAT,          -- NDVI (optional, for reference)
    
    -- Uncertainty estimates
    uncertainty_lai FLOAT DEFAULT 0.5,   -- std dev of LAI observation
    uncertainty_sm  FLOAT DEFAULT 0.05,
    
    -- Data source
    source          VARCHAR(100) DEFAULT 'field',
                    -- 'field', 'sentinel2', 'drone', 'modis'
    
    -- Assimilation status
    assimilated     BOOLEAN DEFAULT FALSE,
    assimilated_at  TIMESTAMP,
    
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_obs_farm_date ON observations(farm_id, obs_date);
CREATE INDEX idx_obs_assimilated ON observations(assimilated) WHERE assimilated = FALSE;
```

---

## 8. Table: soil_cache

Caches SoilGrids results to avoid re-fetching.

```sql
CREATE TABLE soil_cache (
    id          SERIAL PRIMARY KEY,
    latitude    FLOAT NOT NULL,
    longitude   FLOAT NOT NULL,
    
    -- PCSE soil parameters
    smfcf       FLOAT NOT NULL,
    smw         FLOAT NOT NULL,
    sm0         FLOAT NOT NULL,
    rdmsol      FLOAT NOT NULL,
    crairc      FLOAT NOT NULL,
    sope        FLOAT NOT NULL,
    ksub        FLOAT NOT NULL,
    k0          FLOAT NOT NULL,
    
    -- Raw SoilGrids response (for debugging)
    raw_json    JSONB,
    
    created_at  TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_soil_latlon UNIQUE (latitude, longitude)
);
```

---

## 9. SQLAlchemy ORM Models

### models/farm.py
```python
from sqlalchemy import Column, Integer, Float, String, Date, DateTime, func
from core.database import Base

class Farm(Base):
    __tablename__ = "farms"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    crop_type = Column(String(100), nullable=False)
    variety_name = Column(String(255), nullable=False)
    sow_date = Column(Date, nullable=False)
    harvest_date = Column(Date, nullable=False)
    smfcf = Column(Float, default=0.32)
    smw = Column(Float, default=0.12)
    sm0 = Column(Float, default=0.45)
    rdmsol = Column(Float, default=100.0)
    crairc = Column(Float, default=0.06)
    sope = Column(Float, default=1.47)
    ksub = Column(Float, default=1.47)
    k0 = Column(Float, default=10.0)
    created_at = Column(DateTime, default=func.now())
```

### models/daily_state.py
```python
from sqlalchemy import Column, Integer, Float, Date, Boolean, DateTime, ForeignKey, func

class DailyState(Base):
    __tablename__ = "daily_states"
    
    id = Column(Integer, primary_key=True)
    farm_id = Column(Integer, ForeignKey("farms.id"), nullable=False, index=True)
    simulation_run_id = Column(Integer, ForeignKey("simulation_runs.id"), nullable=False)
    date = Column(Date, nullable=False)
    lai = Column(Float)
    sm = Column(Float)
    tagp = Column(Float)
    twso = Column(Float)
    dvs = Column(Float)
    tra = Column(Float)
    rd = Column(Float)
    wlv = Column(Float)
    wst = Column(Float)
    disease_risk = Column(Float)
    water_stress = Column(Float)
    assimilated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
```

---

## 10. Important Temporal Queries

### Get latest state for a farm:
```sql
SELECT * FROM daily_states
WHERE farm_id = 1
ORDER BY date DESC
LIMIT 1;
```

### Get full season state trajectory:
```sql
SELECT date, lai, sm, tagp, twso, dvs
FROM daily_states
WHERE farm_id = 1
  AND simulation_run_id = 5
ORDER BY date ASC;
```

### Get unassimilated observations:
```sql
SELECT * FROM observations
WHERE farm_id = 1
  AND assimilated = FALSE
  AND lai_observed IS NOT NULL
ORDER BY obs_date ASC;
```

### Check weather cache completeness:
```sql
SELECT COUNT(*) FROM weather_records
WHERE latitude = 28.62
  AND longitude = 77.21
  AND date BETWEEN '2023-10-01' AND '2024-06-30';
-- Should return 273 rows for a full season
```

### Yield comparison across what-if scenarios:
```sql
SELECT run_type, scenario_json, final_twso
FROM simulation_runs
WHERE farm_id = 1
ORDER BY final_twso DESC;
```

---

## 11. Indexing Recommendations

| Table | Index | Reason |
|---|---|---|
| `daily_states` | `(farm_id, date)` | Most frequent query pattern |
| `daily_states` | `(simulation_run_id)` | Fetch all days for a run |
| `weather_records` | `(latitude, longitude, date)` | Cache lookup |
| `observations` | `(farm_id, obs_date)` | Find obs for assimilation |
| `observations` | `assimilated = FALSE` | Partial index for pending obs |
