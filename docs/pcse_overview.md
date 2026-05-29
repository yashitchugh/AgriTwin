# pcse_overview.md
# PCSE & WOFOST — Technical Overview for AgriTwin

---

## 1. What is PCSE?

**PCSE** (Python Crop Simulation Environment) is an open-source Python framework developed by Wageningen University & Research (WUR) for implementing and running crop simulation models.

- GitHub: https://github.com/ajwdewit/pcse
- Docs: https://pcse.readthedocs.io
- Install: `pip install pcse`

PCSE provides:
- A collection of crop simulation models (WOFOST, LINTUL, ALCEPAS, etc.)
- Standard interfaces for weather, soil, and management inputs
- State/rate variable management infrastructure
- Signal-based event system (crop emergence, harvest, etc.)

---

## 2. What is WOFOST?

**WOFOST** (World Food Studies) is a mechanistic, process-based crop growth model that simulates daily crop development and growth based on:
- Solar radiation interception by the crop canopy
- Photosynthesis and respiration
- Transpiration and soil water balance
- Crop phenological development (DVS)
- Biomass partitioning to organs

WOFOST was originally developed in the 1980s by Wageningen University and has been extensively validated across wheat, maize, rice, potato, sugarbeet, and other crops worldwide.

---

## 3. Models Available in PCSE

| Model Class | Description | Use Case |
|---|---|---|
| `Wofost72_PP` | Potential Production | No water/nutrient limits |
| `Wofost72_WLP_FD` | Water-Limited, Freely Draining | **MVP target** — rainfed/irrigated fields |
| `Wofost72_WLP_GW` | Water-Limited, Groundwater influence | High water table soils |
| `LINTUL3` | Simpler radiation-use-efficiency model | Fast, less detailed |
| `Wofost8` | Newer WOFOST generation | More nutrient modeling |

**AgriTwin uses: `Wofost72_WLP_FD`**

---

## 4. Why `Wofost72_WLP_FD`?

- "WLP" = Water-Limited Production → accounts for drought stress
- "FD" = Freely Draining soil → no groundwater table effects
- **Note:** `Wofost72_WLP_FD` is an alias for `Wofost72_WLP_CWB` (line 256 of models.py). Both resolve to the same class using `Wofost72_WLP_CWB.conf` config with `WaterbalanceFD`.
- Most applicable to real-world agricultural fields globally
- Water stress directly affects LAI, biomass, yield
- Simpler than groundwater-coupled variants → fewer uncertain parameters
- Well-documented crop parameter files available in PCSE data directory

---

## 5. Key PCSE Components

### 5.1 Weather Provider

```python
from pcse.input import NASAPowerWeatherDataProvider  # NOT pcse.db (old/wrong path)

weather = NASAPowerWeatherDataProvider(latitude=28.6, longitude=77.2)
# Returns daily WeatherDataContainer objects when called as weather(day)
```

For custom/cached weather:
```python
from pcse.base import WeatherDataProvider, WeatherDataContainer

class CustomWeatherProvider(WeatherDataProvider):
    def __init__(self, weather_records):
        WeatherDataProvider.__init__(self)
        for record in weather_records:
            wdc = WeatherDataContainer(**record)
            self._store_WeatherDataContainer(wdc)
```

### 5.2 Soil Data

Soil is passed as a Python dict or loaded from a YAML/CAB file:
```python
soil_data = {
    "SMFCF": 0.38,   # Field capacity (cm3/cm3)
    "SMW": 0.12,     # Wilting point (cm3/cm3)
    "SM0": 0.48,     # Soil porosity / saturation
    "RDMSOL": 80,    # Maximum rootable depth (cm)
    "SOPE": 1.47,    # Max percolation rate root zone
    "KSUB": 1.47,    # Max percolation rate subsoil
    "CRAIRC": 0.060, # Critical air content
    "K0": 10.0,      # Hydraulic conductivity at saturation
}
```

### 5.3 Crop Parameters

Crop parameters come from PCSE's built-in parameter database:
```python
from pcse.input import YAMLCropDataProvider  # NOT pcse.fileinput
cropd = YAMLCropDataProvider()
cv = cropd.get_crops_varieties()  # dict: {crop: [varieties]}
print(list(cv.keys()))            # list all crops
print(list(cv['wheat']))          # list wheat varieties
cropd.set_active_crop('wheat', 'Winter_wheat_101')  # activate
```

Or load from local files:
```python
cropd = YAMLCropDataProvider(fpath='path/to/WOFOST_crop_parameters')
cropd.set_active_crop('wheat', 'Winter_wheat_101')
```

### 5.4 AgroManagement

```python
from pcse.input import YAMLAgroManagementReader  # NOT pcse.fileinput (deprecated)

agromanagement = YAMLAgroManagementReader('path/to/agro.yaml')
# agromanagement IS a list (subclasses list) — pass directly to Engine
```

Or from inline YAML:
```python
import yaml

agro_yaml_string = """..."""
agro = yaml.safe_load(agro_yaml_string)['AgroManagement']  # extract list
```

---

## 6. Running WOFOST — Full Example

```python
from pcse.models import Wofost72_WLP_FD
from pcse.input import YAMLCropDataProvider, YAMLAgroManagementReader  # NOT pcse.fileinput
from pcse.input import NASAPowerWeatherDataProvider  # NOT pcse.db
import yaml, io

# 1. Weather
wdp = NASAPowerWeatherDataProvider(latitude=28.6, longitude=77.2)

# 2. Soil
soil_params = ParameterProvider(soildata={
    "SMFCF": 0.38, "SMW": 0.12, "SM0": 0.48,
    "RDMSOL": 80, "SOPE": 1.47, "KSUB": 1.47,
    "CRAIRC": 0.06, "K0": 10.0
})

# 3. Crop parameters
cropd = YAMLCropDataProvider()
cropd.set_active_crop('wheat', 'Winter_wheat_101')

# 4. AgroManagement
agromanagement = yaml.safe_load(open('agro_wheat.yaml'))

# 5. Site parameters (WAV is required)
from pcse.input import WOFOST72SiteDataProvider
sitedata = WOFOST72SiteDataProvider(WAV=10)

# 6. Combined parameter provider
from pcse.base import ParameterProvider
params = ParameterProvider(cropdata=cropd, soildata=soil_params, sitedata=sitedata)

# 7. Run model
wofost = Wofost72_WLP_FD(params, wdp, agromanagement)
wofost.run_till_terminate()

# 8. Extract output
output = wofost.get_output()
# output is a list of dicts, one per day:
# [{'day': date(2023,11,1), 'LAI': 0.21, 'SM': 0.35, 'TAGP': 105.2, ...}, ...]
```

---

## 7. Simulation Lifecycle

```
Initialize model (day 0 = sowing date)
        │
        ▼
Day-by-day stepping:
  wofost.run(days=1)  →  internal rate calculation → state update
        │
        ▼  [repeat until harvest signal or end date]
        │
        ▼
wofost.get_output()  →  list of daily state dicts
wofost.get_summary_output()  →  final summary (TWSO, max LAI, etc.)
```

---

## 8. State Variables (WOFOST outputs)

| Variable | Description | Units |
|---|---|---|
| `LAI` | Leaf Area Index | m²/m² |
| `SM` | Soil Moisture | cm³/cm³ |
| `TAGP` | Total Above-Ground Biomass | kg/ha |
| `TWSO` | Total Weight Storage Organs (yield proxy) | kg/ha |
| `DVS` | Development Stage | unitless (0–2) |
| `TRA` | Transpiration Actual | cm/day |
| `RD` | Rooting Depth | cm |
| `TRAN` | Transpiration from N stress | cm/day |
| `WRT` | Weight Root Total | kg/ha |
| `WST` | Weight Stem Total | kg/ha |
| `WLV` | Weight Leaf Total | kg/ha |

---

## 9. Running Step-by-Step (Important for EnKF)

For assimilation, you **cannot** run `run_till_terminate()`. Instead:

```python
wofost = Wofost72_WLP_FD(params, wdp, agromanagement)

# Run one day at a time
for day in simulation_days:
    wofost.run(days=1)
    state = wofost.get_variable('LAI')
    sm = wofost.get_variable('SM')
    # → pass to EnKF update if observation available
    # → inject corrected state back if needed
```

### State injection (for EnKF):

```python
# After EnKF update, inject corrected LAI back:
wofost.set_variable('LAI', corrected_lai)
wofost.set_variable('SM', corrected_sm)
```

**Warning:** Not all internal states can be freely injected. Check WOFOST source for which variables accept external setting. LAI and SM are safe.

---

## 10. PCSE Data Directory Structure

PCSE ships with crop parameter YAML files. Find them:
```python
import pcse
import os
data_dir = os.path.join(os.path.dirname(pcse.__file__), 'db', 'pcse')
```

Key files:
- `crop/` — YAML files for wheat, maize, rice, etc.
- `soil/` — Example soil files
- `agro/` — Example AgroManagement YAML files

---

## 11. Important Classes Reference

| Class | Module | Purpose |
|---|---|---|
| `Wofost72_WLP_FD` | `pcse.models` | Main simulation model (alias for Wofost72_WLP_CWB) |
| `NASAPowerWeatherDataProvider` | `pcse.input` | Auto-fetch NASA weather |
| `YAMLCropDataProvider` | `pcse.input` | Load crop parameters |
| `YAMLAgroManagementReader` | `pcse.input` | Load AgroManagement YAML |
| `WOFOST72SiteDataProvider` | `pcse.input` | Validated site parameters |
| `DummySoilDataProvider` | `pcse.input` | Dummy soil for potential production |
| `ParameterProvider` | `pcse.base` | Combine crop+soil+site params |
| `WeatherDataProvider` | `pcse.base` | Base class for custom weather |
| `WeatherDataContainer` | `pcse.base` | Single day weather record |
