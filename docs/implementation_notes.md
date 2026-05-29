# implementation_notes.md
# AgriTwin — Implementation Notes for AI Coding Agent

> **Purpose:** This file is optimized for AI-assisted coding (Claude/Cursor/Copilot). It contains authoritative implementation constraints, naming conventions, architecture rules, and explicit warnings to prevent hallucination. Always consult this file before generating code for AgriTwin.

---

## 1. Project Identity

- **Project name:** AgriTwin
- **Purpose:** Agricultural digital twin via physics simulation + data assimilation
- **Phase:** MVP — mechanistic simulation + EnKF only. No AI/ML models yet.
- **Stack:** Python, FastAPI, PCSE, PostgreSQL, Docker, Ubuntu Linux

---

## 2. Architecture Constraints (NEVER violate these)

### 2.1 Model Selection
- **ALWAYS use:** `Wofost72_WLP_FD` from `pcse.models`
- **NEVER use:** `Wofost72_PP`, `Wofost8`, `LINTUL3`, or any other model variant for MVP
- **NEVER use:** nutrient models (N/P/K) in MVP
- **NEVER use:** transformer models (Crossformer, ViT) in MVP

### 2.2 Simulation Execution
- **ALWAYS run WOFOST step-by-step** (`wofost.run(days=1)`) inside EnKF integration — **NEVER** use `run_till_terminate()` if assimilation is needed
- Use `run_till_terminate()` only for: non-assimilated baseline simulations or validation runs
- DVS **must never** be injected or corrected via EnKF (thermal-time driven — injecting it breaks phenology)

### 2.3 Data Sources
- Weather: **NASA POWER API only** (`https://power.larc.nasa.gov/api/temporal/daily/point`)
  - community = `"AG"` (not `"RE"` or `"SB"`)
  - format = `"JSON"`
- Soil: **SoilGrids REST API** (`https://rest.isric.org/soilgrids/v2.0/properties/query`)
- **NEVER** fabricate weather or soil data values

### 2.4 Backend Architecture
- Services are **never** imported directly into routes — always instantiated with DB session
- All simulation logic lives in `services/simulation_service.py`
- Routes only handle request parsing, call services, return responses
- **NEVER** put WOFOST code directly in route handlers

---

## 3. Variable Naming Conventions

### WOFOST / PCSE Variables (use EXACTLY as shown)

| Python var | PCSE get_variable key | Description |
|---|---|---|
| `lai` | `"LAI"` | Leaf Area Index |
| `sm` | `"SM"` | Soil Moisture |
| `tagp` | `"TAGP"` | Total Above-Ground Biomass |
| `twso` | `"TWSO"` | Storage Organ Weight |
| `dvs` | `"DVS"` | Development Stage |
| `tra` | `"TRA"` | Actual Transpiration |
| `rd` | `"RD"` | Rooting Depth |

**Rule:** PCSE `get_variable()` keys are always **UPPERCASE**. DB column names and Python dicts use **lowercase**.

### Weather Variable Mapping

| NASA POWER code | PCSE field | Unit in PCSE |
|---|---|---|
| `T2M_MAX` | `TMAX` | °C |
| `T2M_MIN` | `TMIN` | °C |
| `PRECTOTCORR` | `RAIN` | **cm/day** (divide mm by 10) |
| `ALLSKY_SFC_SW_DWN` | `IRRAD` | **J/m²/day** (multiply MJ by 1e6) |
| `WS2M` | `WIND` | m/s |
| `QV2M` | `VAP` | **kPa** (requires conversion) |

**Critical:** Never pass raw NASA POWER values directly to PCSE. Always apply unit conversions.

### Soil Parameter Names

Use exactly these keys when building PCSE soil dicts:
```python
["SMFCF", "SMW", "SM0", "CRAIRC", "RDMSOL", "SOPE", "KSUB", "K0"]
```

### AgroManagement YAML Keys (exact spelling)

```yaml
AgroManagement:       # Not "Agromanagement" or "agro_management"
CropCalendar:         # Not "CropCalender" or "crop_calendar"
crop_name:            # lowercase, matches PCSE db exactly
variety_name:         # must match PCSE variety key
crop_start_type: sowing    # exactly "sowing" not "Sowing"
crop_end_type: harvest     # exactly "harvest" not "Harvest"
event_signal: irrigate     # exactly "irrigate" not "irrigation"
```

---

## 4. PCSE API — Do Not Hallucinate

### Correct import paths:
```python
from pcse.models import Wofost72_WLP_FD           # ✅ (alias for Wofost72_WLP_CWB)
from pcse.base import ParameterProvider           # ✅
from pcse.base import WeatherDataProvider         # ✅
from pcse.base import WeatherDataContainer        # ✅
from pcse.input import YAMLCropDataProvider       # ✅ (NOT pcse.fileinput — deprecated)
from pcse.input import YAMLAgroManagementReader   # ✅ (NOT pcse.fileinput — deprecated)
from pcse.input import NASAPowerWeatherDataProvider  # ✅ (NOT pcse.db)
from pcse.input import WOFOST72SiteDataProvider   # ✅ (NOT pcse.util)
from pcse.util import reference_ET                # ✅
```

### WRONG imports (will fail or are deprecated):
```python
from pcse import Wofost72_WLP_FD           # ❌
from pcse.crop import Wofost              # ❌
from pcse.weather import NASAPower        # ❌
from pcse.models import WOFOSTModel       # ❌
from pcse.fileinput import ...            # ❌ DEPRECATED — prints warning, use pcse.input
from pcse.db import NASAPowerWeatherDataProvider  # ❌ WRONG MODULE — use pcse.input
```

### Correct WOFOST instantiation:
```python
wofost = Wofost72_WLP_FD(params, wdp, agromanagement)
# params: ParameterProvider
# wdp: WeatherDataProvider instance (not a dict)
# agromanagement: list (the value of agro_dict["AgroManagement"])
```

### Correct state access:
```python
lai = wofost.get_variable("LAI")     # ✅ string key, UPPERCASE
wofost.set_variable("LAI", 3.5)      # ✅ injection
output = wofost.get_output()         # ✅ list of dicts (all days run so far)
summary = wofost.get_summary_output() # ✅ final summary stats
```

### crop data setup:
```python
cropd = YAMLCropDataProvider()
cropd.set_active_crop('wheat', 'Winter_wheat_101')   # ✅ lowercase crop name
# NOT: cropd.set_active_crop('Wheat', ...)           # ❌
```

---

## 5. Scientific Assumptions (MVP)

- Soil is homogeneous across the root zone
- One soil layer (no horizon differentiation)
- No groundwater influence (`WLP_FD` = freely draining)
- No nitrogen stress initially (crop parameters assume non-limiting N)
- Irrigation efficiency = 0.7 (default if not specified)
- Atmospheric CO₂ at 360 ppm (WOFOST default)
- No slope/aspect effects — flat field assumed
- DVS is driven entirely by temperature accumulation (TSUM1, TSUM2 from crop params)
- Disease risk is a post-processing heuristic, not a WOFOST state

---

## 6. EnKF Implementation Rules

- **Ensemble size:** Always N=50 for MVP (configurable via settings)
- **State vector order:** `[LAI, SM, TAGP, TWSO, DVS]` — index 0 to 4
- **NEVER perturb or correct DVS** (index 4) — set perturbation_std to 0.0
- **Always clip ensemble members** to ≥ 0 after update (no negative biomass/LAI)
- **Observation operator H** for LAI: `H = [1, 0, 0, 0, 0]`
- **Observation noise R** for LAI: default `R = 0.5² = 0.25` (variance)
- After EnKF update: **only inject LAI and SM** back to WOFOST, not TAGP/TWSO
- EnKF runs **only** when an observation record exists for that farm+date

---

## 7. Database Rules

- All lat/lon stored for weather cache: **round to 2 decimal places**
  ```python
  lat_key = round(lat, 2)
  ```
- RAIN stored in DB as **cm/day** (PCSE units), not mm/day
- IRRAD stored in DB as **J/m²/day** (PCSE units), not MJ/m²/day
- Always use `ON CONFLICT DO NOTHING` or upsert for weather records (idempotent inserts)
- Never store raw NASA POWER JSON in weather_records — always store converted values
- `daily_states.assimilated = TRUE` whenever EnKF was applied on that day

---

## 8. FastAPI Conventions

- All route files use `router = APIRouter()` (not `app = FastAPI()`)
- Pydantic models: use `class Config: from_attributes = True` for ORM responses
- Database dependency: `db: Session = Depends(get_db)` in all route functions
- Simulation endpoints use **regular `def`** (not `async def`) — WOFOST is CPU-bound
- Weather fetch endpoints can use **`async def`** — I/O-bound
- Error types: raise `HTTPException(status_code=..., detail=...)` in routes, raise custom exceptions in services

---

## 9. Coding Priorities

When generating code for AgriTwin, prioritize in this order:

1. **Correctness first** — WOFOST must produce scientifically valid outputs
2. **Integration** — PCSE, FastAPI, PostgreSQL must work together correctly
3. **Simplicity** — MVP code should be readable and debuggable
4. **Performance** — only optimize after MVP is validated

---

## 10. Explicit Warnings (AI Agent Must Read)

### WARNING 1: AgroManagement format
PCSE expects the AgroManagement as a **Python list** extracted from the YAML:
```python
# CORRECT:
agro_list = agro_dict["AgroManagement"]   # this is a list
wofost = Wofost72_WLP_FD(params, wdp, agro_list)

# WRONG:
wofost = Wofost72_WLP_FD(params, wdp, agro_dict)  # passing full dict fails
```

### WARNING 2: RAIN units
NASA POWER `PRECTOTCORR` is in mm/day. PCSE expects cm/day.
**ALWAYS divide by 10.** Forgetting this gives 10x rainfall — completely wrong water balance.

### WARNING 3: IRRAD units
NASA POWER `ALLSKY_SFC_SW_DWN` is in MJ/m²/day. PCSE expects J/m²/day.
**ALWAYS multiply by 1,000,000 (1e6).** Forgetting gives 10⁶× less radiation — crop will not grow.

### WARNING 4: Crop name case
PCSE crop names are **always lowercase** in the YAMLCropDataProvider:
- `"wheat"` ✅ not `"Wheat"` ❌
- `"maize"` ✅ not `"Maize"` ❌

### WARNING 5: DVS injection
**NEVER** write code that calls `wofost.set_variable("DVS", ...)`.
DVS is thermally integrated — external injection breaks the entire phenological lifecycle.

### WARNING 6: SoilGrids conversion factors
SoilGrids stores values as integers × conversion_factor:
```python
# actual_value = raw_integer × conversion_factor
# e.g., wv0033 raw=284, factor=0.1 → actual=28.4 cm³/100cm³ → /100 = 0.284 cm³/cm³
```
Always use the `conversion_factor` from the API response metadata, not hardcoded values.

### WARNING 7: SM physical ordering
Before passing soil to WOFOST, always verify:
```python
assert soil["SMW"] < soil["SMFCF"] < soil["SM0"]
```
WOFOST will crash or produce nonsense if this ordering is violated.

### WARNING 8: ParameterProvider sitedata
Use `WOFOST72SiteDataProvider` for validated site parameters:
```python
from pcse.input import WOFOST72SiteDataProvider
sitedata = WOFOST72SiteDataProvider(WAV=10)  # WAV is the only required parameter
```
Default values are provided for IFUNRN(0), NOTINF(0), SSI(0), SSMAX(0), SMLIM(0.4).
**WARNING:** `RDMSOL` belongs in `soildata` dict, NOT in sitedata. WOFOST72SiteDataProvider
will raise an error if unknown parameters like RDMSOL are passed to it.

---

## 11. File Responsibilities Summary

| File | Owns | Does NOT own |
|---|---|---|
| `routes/simulate.py` | HTTP parsing, response | WOFOST logic |
| `services/simulation_service.py` | WOFOST orchestration | DB schema |
| `services/weather_service.py` | NASA POWER fetch + parsing | Soil or crop data |
| `services/soil_service.py` | SoilGrids fetch + mapping | Weather or WOFOST |
| `services/agro_service.py` | AgroManagement YAML builder | Simulation running |
| `services/assimilation_service.py` | EnKF math | WOFOST direct I/O |
| `models/*.py` | SQLAlchemy ORM | Business logic |
| `schemas/*.py` | Pydantic validation | DB access |
| `core/database.py` | DB session | Application logic |

---

## 12. Quick Reference: Adding a New Crop

1. Verify crop exists in PCSE:
   ```python
   from pcse.input import YAMLCropDataProvider
   cropd = YAMLCropDataProvider()
   cv = cropd.get_crops_varieties()  # returns dict: {crop_name: [variety_names]}
   print(list(cv.keys()))            # list all crop names
   ```
2. Get valid varieties for a crop:
   ```python
   print(list(cv['maize']))          # list varieties for maize
   ```
3. Activate a crop before use:
   ```python
   cropd.set_active_crop('maize', 'Grain_maize_201')
   ```
4. Use exact `crop_name` and `variety_name` strings in FarmCreate request
5. Set appropriate `harvest_date` (maize needs longer season than wheat)
6. Adjust `max_duration` in AgroManagement if needed

**Note:** Methods `get_cropnames()` and `get_varieties()` do NOT exist.
Use `get_crops_varieties()` which returns a dict of crops → varieties.
