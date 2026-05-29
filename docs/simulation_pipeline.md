# simulation_pipeline.md
# AgriTwin — Complete Simulation Pipeline

---

## 1. Overview

The simulation pipeline is the core of AgriTwin. It orchestrates:
1. Data fetching (weather, soil)
2. AgroManagement generation
3. WOFOST initialization and step-by-step execution
4. State extraction at each daily timestep
5. Optional EnKF assimilation when observations exist
6. State persistence to PostgreSQL

```
POST /simulate/run
        │
        ▼
┌──────────────────────┐
│ 1. Load Farm Config  │  lat, lon, crop, sow_date, soil_params
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 2. Fetch Weather     │  NASA POWER → PCSE WeatherDataProvider
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 3. Fetch Soil        │  SoilGrids → PCSE soil dict
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 4. Build AgroMgmt    │  Dynamic YAML → crop calendar + irrigation
└──────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 5. Init WOFOST       │  Wofost72_WLP_FD(params, weather, agro)
└──────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────┐
│ 6. Day-by-Day Loop                           │
│    for each simulation day:                  │
│      wofost.run(days=1)                      │
│      state = extract_state(wofost, day)      │
│      if observation_available(day):          │
│          state = enkf.update(state, obs)     │
│          inject_state(wofost, state)         │
│      store_state(state, db)                  │
└──────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────┐
│ 7. Return Results    │  JSON list of daily states
└──────────────────────┘
```

---

## 2. Step-by-Step Execution

### Step 1: Load Farm Configuration

```python
from models.farm import Farm

def _get_farm(self, farm_id: int) -> Farm:
    farm = self.db.query(Farm).filter(Farm.id == farm_id).first()
    if not farm:
        raise FarmNotFoundError(f"Farm {farm_id} not found")
    return farm
```

### Step 2: Fetch Weather

```python
weather_provider = self.weather_svc.get_weather_provider(
    lat=farm.latitude,
    lon=farm.longitude,
    start_date=farm.sow_date - timedelta(days=14),  # 2 weeks pre-sowing
    end_date=farm.harvest_date,
    db=self.db
)
```

### Step 3: Build Soil Parameters

```python
soil_params = {
    "SMFCF": farm.smfcf,
    "SMW": farm.smw,
    "SM0": farm.sm0,
    "RDMSOL": farm.rdmsol,
    "CRAIRC": 0.060,
    "SOPE": 1.47,
    "KSUB": 1.47,
    "K0": 10.0
}
```

### Step 4: Build AgroManagement

```python
agro_dict = self.agro_svc.build_agromanagement(
    crop_name=farm.crop_type,
    variety_name=farm.variety_name,
    sow_date=farm.sow_date,
    harvest_date=farm.harvest_date,
    irrigation_events=irrigation_events
)
```

### Step 5: Initialize WOFOST

```python
from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider
from pcse.input import YAMLCropDataProvider  # NOT pcse.fileinput (deprecated)
from pcse.input import WOFOST72SiteDataProvider

cropd = YAMLCropDataProvider()
cropd.set_active_crop(farm.crop_type, farm.variety_name)

sitedata = WOFOST72SiteDataProvider(WAV=10)  # WAV is required; RDMSOL goes in soildata

params = ParameterProvider(
    cropdata=cropd,
    soildata=soil_params,
    sitedata=sitedata
)

wofost = Wofost72_WLP_FD(params, weather_provider, agro_dict["AgroManagement"])
```

**Site data defaults:**

| Parameter | Default | Meaning |
|---|---|---|
| `IFUNRN` | 0 | Fraction of precipitation not infiltrating (0 = all infiltrates) |
| `NOTINF` | 0 | Maximum ponding before runoff (cm) |
| `SSI` | 0 | Initial surface storage (cm) |
| `SSMAX` | 0 | Maximum surface storage (cm) |
| `WAV` | 10 | Initial available water in root zone (cm) |

### Step 6: Day-by-Day Simulation Loop

```python
from datetime import date, timedelta

def run_day_by_day(
    wofost,
    start_date: date,
    end_date: date,
    observations: dict,  # {date: {"LAI": value, "uncertainty": 0.5}}
    enkf_service,
    db: Session
) -> list[dict]:
    
    daily_states = []
    current_date = start_date
    
    while current_date <= end_date:
        try:
            wofost.run(days=1)
        except StopIteration:
            # Model signaled end of crop (harvest or death)
            break
        
        # Extract state
        state = extract_state(wofost, current_date)
        
        # EnKF assimilation if observation available
        if current_date in observations:
            obs = observations[current_date]
            corrected_state = enkf_service.assimilate(
                wofost=wofost,
                state=state,
                observation=obs
            )
            state = corrected_state
            state["assimilated"] = True
        else:
            state["assimilated"] = False
        
        daily_states.append(state)
        current_date += timedelta(days=1)
    
    return daily_states
```

---

## 3. State Extraction

```python
TRACKED_VARIABLES = {
    "LAI": "lai",
    "SM": "sm",
    "TAGP": "tagp",
    "TWSO": "twso",
    "DVS": "dvs",
    "TRA": "tra",
    "RD": "rd",
    "WLV": "wlv",
    "WST": "wst",
}

def extract_state(wofost, current_date: date) -> dict:
    """Extract all tracked variables from WOFOST for a given day."""
    state = {"date": current_date.isoformat()}
    
    for pcse_var, field_name in TRACKED_VARIABLES.items():
        try:
            val = wofost.get_variable(pcse_var)
            state[field_name] = float(val) if val is not None else None
        except Exception:
            state[field_name] = None
    
    return state
```

---

## 4. State Persistence to PostgreSQL

```python
from models.daily_state import DailyState

def store_states(
    farm_id: int,
    simulation_run_id: int,
    states: list[dict],
    db: Session
):
    for s in states:
        record = DailyState(
            farm_id=farm_id,
            simulation_run_id=simulation_run_id,
            date=date.fromisoformat(s["date"]),
            lai=s.get("lai"),
            sm=s.get("sm"),
            tagp=s.get("tagp"),
            twso=s.get("twso"),
            dvs=s.get("dvs"),
            tra=s.get("tra"),
            rd=s.get("rd"),
            assimilated=s.get("assimilated", False)
        )
        db.add(record)
    db.commit()
```

---

## 5. Complete simulation_service.py Skeleton

```python
# services/simulation_service.py

from datetime import date, timedelta
from sqlalchemy.orm import Session
from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider
from pcse.input import YAMLCropDataProvider  # NOT pcse.fileinput (deprecated)
from pcse.input import WOFOST72SiteDataProvider
import logging

logger = logging.getLogger(__name__)

class SimulationService:
    
    def __init__(self, db: Session):
        self.db = db
        self.weather_svc = WeatherService()
        self.soil_svc = SoilService()
        self.agro_svc = AgroService()
    
    def run_simulation(
        self,
        farm_id: int,
        irrigation_events: list = None,
        start_date: date = None,
        end_date: date = None
    ) -> dict:
        
        logger.info(f"Starting simulation for farm {farm_id}")
        
        # 1. Load farm
        farm = self._get_farm(farm_id)
        sim_start = start_date or farm.sow_date
        sim_end = end_date or farm.harvest_date
        
        # 2. Weather
        weather_provider = self.weather_svc.get_weather_provider(
            lat=farm.latitude, lon=farm.longitude,
            start_date=sim_start - timedelta(days=14),
            end_date=sim_end, db=self.db
        )
        
        # 3. Soil
        soil_params = self._build_soil_params(farm)
        
        # 4. AgroManagement
        agro_dict = self.agro_svc.build_agromanagement(
            crop_name=farm.crop_type,
            variety_name=farm.variety_name,
            sow_date=farm.sow_date,
            harvest_date=farm.harvest_date,
            irrigation_events=irrigation_events or []
        )
        
        # 5. Initialize WOFOST
        wofost = self._init_wofost(farm, soil_params, weather_provider, agro_dict)
        
        # 6. Load observations for this farm
        observations = self._load_observations(farm_id, sim_start, sim_end)
        
        # 7. Run day-by-day
        states = self._run_day_by_day(wofost, sim_start, sim_end, observations)
        
        # 8. Create simulation run record
        run_id = self._create_run_record(farm_id, sim_start, sim_end)
        
        # 9. Store states
        self.store_states(farm_id, run_id, states, self.db)
        
        logger.info(f"Simulation complete for farm {farm_id}: {len(states)} days")
        return {"simulation_id": run_id, "farm_id": farm_id, "daily_states": states}
    
    def _init_wofost(self, farm, soil_params, weather_provider, agro_dict):
        cropd = YAMLCropDataProvider()
        cropd.set_active_crop(farm.crop_type, farm.variety_name)
        
        sitedata = WOFOST72SiteDataProvider(WAV=10)  # RDMSOL goes in soil_params, NOT here
        
        params = ParameterProvider(
            cropdata=cropd,
            soildata=soil_params,
            sitedata=sitedata
        )
        
        return Wofost72_WLP_FD(params, weather_provider, agro_dict["AgroManagement"])
```

---

## 6. Temporal State Handling

### What-If Branching

```python
def run_whatif(self, farm_id: int, from_date: date, scenario: dict) -> list:
    """Run a what-if simulation branching from current assimilated state."""
    
    # 1. Get last assimilated state as starting point
    last_state = self._get_last_assimilated_state(farm_id, as_of=from_date)
    
    # 2. Re-initialize WOFOST from that date (not from sowing)
    # Note: WOFOST must be re-run from sowing with modified management
    # True state-restart is complex; simplest MVP approach:
    # Re-run full simulation with modified scenario from sowing date
    
    farm = self._get_farm(farm_id)
    modified_irrigation = scenario.get("irrigation_events", [])
    
    return self.run_simulation(
        farm_id=farm_id,
        irrigation_events=modified_irrigation,
        end_date=scenario.get("end_date", farm.harvest_date)
    )
```

---

## 7. Orchestration Checklist

Before running any simulation, verify:
- [ ] Farm exists in DB
- [ ] Weather data available for full period
- [ ] Soil parameters are valid (SMW < SMFCF < SM0)
- [ ] AgroManagement YAML is valid (campaign start < sow date < harvest date)
- [ ] Crop name and variety name exist in PCSE database
- [ ] No existing simulation for same farm + date range (or allow overwrite)
