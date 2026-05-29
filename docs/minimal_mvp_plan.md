# minimal_mvp_plan.md
# AgriTwin — Minimal MVP Development Plan

---

## 1. MVP Goals

The MVP must demonstrate:
1. ✅ A farm can be created via API
2. ✅ Weather and soil data are auto-fetched
3. ✅ WOFOST simulation runs for a full crop season
4. ✅ Daily states (LAI, SM, TAGP, TWSO, DVS) are returned and stored
5. ✅ LAI observation can be submitted and EnKF assimilation runs
6. ✅ What-if simulation runs with modified irrigation schedule

---

## 2. What NOT to Build Initially

| Excluded | Reason |
|---|---|
| Crossformer / MMST-ViT AI models | Requires training data, adds complexity |
| Sentinel-2 satellite ingestion | Requires GEE/API access, preprocessing |
| Nutrient (N/P/K) modeling | Adds parameters, use Wofost72_WLP_FD only |
| Multi-field batch processing | Scale later |
| Frontend / dashboard | Build backend API first |
| Authentication (JWT/OAuth) | Add in Phase 2 |
| Advanced SM assimilation | LAI-only EnKF for MVP |
| Real-time streaming | Batch simulation is fine for MVP |
| Celery / task queue | Use synchronous execution first |

---

## 3. Development Order (Phase 1 — MVP)

### Milestone 0: Project Setup
- [ ] Initialize FastAPI app (`main.py`)
- [ ] Set up PostgreSQL database + SQLAlchemy
- [ ] Create all DB tables via `Base.metadata.create_all()`
- [ ] Configure Docker + docker-compose.yml
- [ ] `.env` config with DATABASE_URL

**Target: Running `uvicorn main:app` locally with `/health` returning 200.**

---

### Milestone 1: Farm CRUD

**Goal:** Create a farm, store it in DB, retrieve it.

- [ ] `models/farm.py` — SQLAlchemy Farm model
- [ ] `schemas/farm.py` — FarmCreate, FarmResponse Pydantic models
- [ ] `routes/farms.py` — POST /farms, GET /farms/{id}

**First API target:**
```bash
curl -X POST http://localhost:8000/farms -d '{
  "name": "Test Farm",
  "latitude": 28.6,
  "longitude": 77.2,
  "crop_type": "wheat",
  "variety_name": "Winter_wheat_101",
  "sow_date": "2023-10-15",
  "harvest_date": "2024-06-30"
}'
# Expected: {"id": 1, "name": "Test Farm", ...}
```

---

### Milestone 2: Weather Pipeline

**Goal:** Fetch NASA POWER weather for a location and store in DB.

- [ ] `services/weather_service.py`
  - `fetch_nasa_power(lat, lon, start, end)` → raw JSON
  - `parse_nasa_power_to_pcse(raw, lat, lon)` → WeatherDataProvider
  - `get_or_fetch_weather(lat, lon, start, end, db)` → cache logic
- [ ] `models/weather_record.py`
- [ ] Test: manually call `fetch_nasa_power(28.6, 77.2, date(2023,10,1), date(2024,6,30))`

**Validation:**
```python
wdp = weather_service.get_weather_provider(28.6, 77.2, start, end, db)
print(wdp(date(2023, 11, 1)))  # Should print WeatherDataContainer
print(wdp(date(2023, 11, 1)).TMAX)  # Should print a temperature
```

---

### Milestone 3: Soil Pipeline

**Goal:** Fetch SoilGrids data and map to PCSE soil parameters.

- [ ] `services/soil_service.py`
  - `fetch_soilgrids(lat, lon)` → raw JSON
  - `soilgrids_to_pcse(raw)` → PCSE soil dict
  - `get_soil_params(lat, lon, db)` → cached
- [ ] `models/soil_cache.py`

**Validation:**
```python
soil = soil_service.get_soil_params(28.6, 77.2, db)
assert 0.10 < soil["SMW"] < 0.25
assert soil["SMW"] < soil["SMFCF"] < soil["SM0"]
print(soil)
```

---

### Milestone 4: AgroManagement Builder

**Goal:** Generate valid WOFOST AgroManagement YAML from farm inputs.

- [ ] `services/agro_service.py`
  - `build_agromanagement(crop, variety, sow_date, harvest_date, irrigations)` → dict
- [ ] Test with wheat, no irrigation, then with 3 irrigation events

**Validation:**
```python
agro = agro_service.build_agromanagement(
    "wheat", "Winter_wheat_101",
    date(2023,10,15), date(2024,6,30), []
)
# Load into PCSE and verify no errors
wofost = Wofost72_WLP_FD(params, wdp, agro["AgroManagement"])
```

---

### Milestone 5: First Full WOFOST Simulation

**Goal:** Run WOFOST end-to-end and extract daily states.

- [ ] `services/simulation_service.py` — `run_simulation(farm_id, ...)`
- [ ] `models/simulation_run.py`, `models/daily_state.py`
- [ ] `routes/simulate.py` — `POST /simulate/run`

**First simulation target:**
```bash
curl -X POST http://localhost:8000/simulate/run -d '{
  "farm_id": 1,
  "irrigation_events": []
}'
# Expected: {"simulation_id": 1, "daily_states": [...270 days of states...]}
```

**Validation checks:**
- [ ] DVS starts at 0.0, reaches 2.0 at end (or close)
- [ ] LAI rises to 3–5, then declines
- [ ] TWSO increases from 0 after DVS > 1.0
- [ ] SM fluctuates with rainfall events
- [ ] No `None` values for LAI, DVS at any step

---

### Milestone 6: Observations API

**Goal:** Submit a LAI observation for a farm on a specific date.

- [ ] `models/observation.py`
- [ ] `schemas/observation.py`
- [ ] `routes/observations.py` — `POST /observations`

```bash
curl -X POST http://localhost:8000/observations -d '{
  "farm_id": 1,
  "obs_date": "2024-01-15",
  "lai_observed": 3.2,
  "uncertainty_lai": 0.5,
  "source": "sentinel2"
}'
```

---

### Milestone 7: EnKF Assimilation

**Goal:** When observation exists on a simulation day, run EnKF and correct the state.

- [ ] `services/assimilation_service.py`
  - `generate_ensemble()`, `enkf_update()`, `inject_state()`
- [ ] Integrate into `simulation_service.py` day-by-day loop
- [ ] `routes/assimilate.py` — `POST /assimilate/run` (trigger re-simulation with assimilation)

**Validation:**
```python
# Before assimilation: LAI = 2.8
# Observation: LAI = 3.2
# After assimilation: LAI should be between 2.8 and 3.2
```

---

### Milestone 8: What-If Simulation

**Goal:** Run a scenario with modified irrigation schedule, compare to baseline.

- [ ] `services/whatif_service.py`
- [ ] `routes/whatif.py` — `POST /whatif/run`

```bash
curl -X POST http://localhost:8000/whatif/run -d '{
  "farm_id": 1,
  "irrigation_events": [
    {"date": "2023-12-01", "amount": 40, "efficiency": 0.7},
    {"date": "2024-02-15", "amount": 35, "efficiency": 0.7}
  ]
}'
```

---

## 4. Testing Strategy

### Unit Tests (per service)

```
tests/
├── test_weather_service.py      # mock NASA POWER, test parsing
├── test_soil_service.py         # mock SoilGrids, test mapping
├── test_agro_service.py         # test YAML generation
├── test_simulation_service.py   # integration test with real PCSE
├── test_enkf.py                 # unit test update step
└── test_api_routes.py           # FastAPI TestClient
```

### Simulation Validation Tests

```python
def test_wheat_simulation_basic():
    # Run wheat for full season
    states = run_simulation(farm_id=1)
    
    # DVS checks
    assert states[0]["dvs"] == 0.0, "DVS should start at 0"
    assert states[-1]["dvs"] >= 1.8, "DVS should reach near maturity"
    
    # LAI checks
    max_lai = max(s["lai"] for s in states)
    assert 2.0 < max_lai < 8.0, f"Max LAI unrealistic: {max_lai}"
    
    # TWSO check
    final_twso = states[-1]["twso"]
    assert 3000 < final_twso < 12000, f"Yield unrealistic: {final_twso} kg/ha"
    
    # SM check
    for s in states:
        if s["sm"] is not None:
            assert 0.05 < s["sm"] < 0.55, f"SM out of bounds: {s['sm']}"
```

### EnKF Sanity Test

```python
def test_enkf_update_pulls_toward_observation():
    base_state = {"lai": 2.8, "sm": 0.30, "tagp": 3000, "twso": 0, "dvs": 0.8}
    obs = {"LAI": 3.5, "uncertainty": 0.5}
    
    corrected = enkf_service.assimilate(wofost_mock, base_state, obs)
    
    # Corrected LAI should be between forecast and observation
    assert 2.8 < corrected["lai"] < 3.5
    assert corrected["lai"] > base_state["lai"]  # pulled toward obs
```

---

## 5. Debugging Priorities

1. **WOFOST fails to initialize** → Check crop name/variety string matches PCSE exactly
2. **WOFOST produces all-None states** → Check weather provider date coverage
3. **DVS never reaches 2.0** → Check `max_duration` in AgroManagement YAML
4. **SM stays constant** → Check soil SMFCF/SMW/SM0 ordering
5. **EnKF gives negative LAI** → Add `np.clip()` after update
6. **NASA POWER returns 400** → Check date format (YYYYMMDD, not YYYY-MM-DD)
7. **SoilGrids returns 404** → Check lat/lon are within valid range (-90 to 90)

---

## 6. Validation Strategy

**Scientific validation (qualitative, for MVP):**
- Compare LAI curve shape to published WOFOST outputs for the crop
- Verify DVS trajectory matches known phenology
- Check yield range against FAOSTAT regional averages
- Verify EnKF LAI correction converges (doesn't oscillate)

**Reference yields for sanity check:**
| Crop | Expected TWSO at harvest |
|---|---|
| Wheat (South Asia) | 3,000–6,000 kg/ha |
| Maize (irrigated) | 6,000–12,000 kg/ha |
| Rice | 4,000–8,000 kg/ha |
