# fastapi_architecture.md
# AgriTwin — FastAPI Backend Architecture

---

## 1. Overview

The AgriTwin backend is a **modular FastAPI application** organized into:
- `routes/` — HTTP endpoint definitions
- `services/` — business logic and external API integrations
- `models/` — SQLAlchemy ORM models (database tables)
- `schemas/` — Pydantic request/response validation models
- `core/` — configuration, database, logging

FastAPI is chosen for:
- Async support (non-blocking NASA POWER / SoilGrids calls)
- Automatic OpenAPI documentation
- Native Pydantic validation
- Clean dependency injection

---

## 2. Recommended Folder Structure

```
agritwin/
├── main.py                        # App entry point
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── api/
│   ├── __init__.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── farms.py               # CRUD for farms
│   │   ├── simulate.py            # Trigger simulations
│   │   ├── assimilate.py          # Trigger EnKF assimilation
│   │   ├── observations.py        # Submit field/satellite observations
│   │   └── whatif.py              # What-if scenario runs
│   └── schemas/
│       ├── __init__.py
│       ├── farm.py
│       ├── simulate.py
│       ├── observation.py
│       └── whatif.py
│
├── services/
│   ├── __init__.py
│   ├── weather_service.py
│   ├── soil_service.py
│   ├── agro_service.py
│   ├── simulation_service.py
│   ├── assimilation_service.py
│   └── whatif_service.py
│
├── models/
│   ├── __init__.py
│   ├── farm.py
│   ├── simulation_run.py
│   ├── daily_state.py
│   ├── weather_record.py
│   └── observation.py
│
└── core/
    ├── __init__.py
    ├── config.py
    ├── database.py
    └── logging.py
```

---

## 3. main.py — Application Entry Point

```python
from fastapi import FastAPI
from api.routes import farms, simulate, assimilate, observations, whatif
from core.database import engine, Base

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AgriTwin API",
    description="Agricultural Digital Twin Platform",
    version="0.1.0"
)

app.include_router(farms.router,        prefix="/farms",        tags=["Farms"])
app.include_router(simulate.router,     prefix="/simulate",     tags=["Simulation"])
app.include_router(assimilate.router,   prefix="/assimilate",   tags=["Assimilation"])
app.include_router(observations.router, prefix="/observations", tags=["Observations"])
app.include_router(whatif.router,       prefix="/whatif",       tags=["WhatIf"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
```

---

## 4. core/config.py

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://agritwin:password@localhost:5432/agritwin"
    WEATHER_CACHE_DAYS: int = 7
    ENKF_ENSEMBLE_SIZE: int = 50
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = ".env"

settings = Settings()
```

---

## 5. core/database.py

```python
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from core.config import settings

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

---

## 6. Schemas (Pydantic)

### api/schemas/farm.py

```python
from pydantic import BaseModel
from datetime import date
from typing import Optional

class FarmCreate(BaseModel):
    name: str
    latitude: float
    longitude: float
    crop_type: str                   # e.g., "wheat"
    variety_name: str                # e.g., "Winter_wheat_101"
    sow_date: date
    harvest_date: date
    
    # Soil overrides (optional — fetched from SoilGrids if omitted)
    smfcf: Optional[float] = None
    smw: Optional[float] = None
    sm0: Optional[float] = None
    rdmsol: Optional[float] = None

class FarmResponse(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float
    crop_type: str
    sow_date: date
    harvest_date: date
    
    class Config:
        from_attributes = True
```

### api/schemas/simulate.py

```python
from pydantic import BaseModel
from datetime import date
from typing import Optional, List

class IrrigationEvent(BaseModel):
    date: date
    amount: float        # mm
    efficiency: float = 0.7

class SimulateRequest(BaseModel):
    farm_id: int
    start_date: Optional[date] = None    # defaults to sow_date
    end_date: Optional[date] = None      # defaults to harvest_date
    irrigation_events: List[IrrigationEvent] = []

class DailyStateResponse(BaseModel):
    date: date
    lai: Optional[float]
    sm: Optional[float]
    tagp: Optional[float]
    twso: Optional[float]
    dvs: Optional[float]
    tra: Optional[float]
    rd: Optional[float]

class SimulateResponse(BaseModel):
    simulation_id: int
    farm_id: int
    status: str
    daily_states: List[DailyStateResponse]
```

### api/schemas/observation.py

```python
from pydantic import BaseModel
from datetime import date
from typing import Optional

class ObservationCreate(BaseModel):
    farm_id: int
    obs_date: date
    lai_observed: Optional[float] = None     # m²/m²
    sm_observed: Optional[float] = None      # cm³/cm³
    source: str = "field"                    # "field", "sentinel2", "drone"
    uncertainty_lai: Optional[float] = 0.5  # LAI observation std dev
```

---

## 7. Routes

### api/routes/farms.py

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.database import get_db
from api.schemas.farm import FarmCreate, FarmResponse
from services.soil_service import SoilService
from models.farm import Farm

router = APIRouter()

@router.post("/", response_model=FarmResponse)
def create_farm(farm_in: FarmCreate, db: Session = Depends(get_db)):
    soil_svc = SoilService()
    soil_params = soil_svc.get_soil_params(farm_in.latitude, farm_in.longitude, db)
    
    farm = Farm(
        name=farm_in.name,
        latitude=farm_in.latitude,
        longitude=farm_in.longitude,
        crop_type=farm_in.crop_type,
        variety_name=farm_in.variety_name,
        sow_date=farm_in.sow_date,
        harvest_date=farm_in.harvest_date,
        smfcf=farm_in.smfcf or soil_params["SMFCF"],
        smw=farm_in.smw or soil_params["SMW"],
        sm0=farm_in.sm0 or soil_params["SM0"],
        rdmsol=farm_in.rdmsol or soil_params["RDMSOL"],
    )
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm

@router.get("/{farm_id}", response_model=FarmResponse)
def get_farm(farm_id: int, db: Session = Depends(get_db)):
    farm = db.query(Farm).filter(Farm.id == farm_id).first()
    if not farm:
        raise HTTPException(status_code=404, detail="Farm not found")
    return farm
```

### api/routes/simulate.py

```python
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from core.database import get_db
from api.schemas.simulate import SimulateRequest, SimulateResponse
from services.simulation_service import SimulationService

router = APIRouter()

@router.post("/run", response_model=SimulateResponse)
def run_simulation(
    request: SimulateRequest,
    db: Session = Depends(get_db)
):
    svc = SimulationService(db)
    result = svc.run_simulation(
        farm_id=request.farm_id,
        irrigation_events=request.irrigation_events,
        start_date=request.start_date,
        end_date=request.end_date
    )
    return result

@router.get("/{farm_id}/states")
def get_farm_states(farm_id: int, db: Session = Depends(get_db)):
    from models.daily_state import DailyState
    states = db.query(DailyState).filter(
        DailyState.farm_id == farm_id
    ).order_by(DailyState.date).all()
    return states
```

---

## 8. Services Layer Pattern

Each service is a class with injected DB session:

```python
# services/simulation_service.py

class SimulationService:
    def __init__(self, db: Session):
        self.db = db
        self.weather_svc = WeatherService()
        self.soil_svc = SoilService()
        self.agro_svc = AgroService()
    
    def run_simulation(self, farm_id: int, ...) -> dict:
        farm = self._get_farm(farm_id)
        weather_provider = self.weather_svc.get_weather_provider(...)
        soil_params = self.soil_svc.get_soil_params(...)
        agro_dict = self.agro_svc.build_agromanagement(...)
        
        states = self._run_wofost(weather_provider, soil_params, agro_dict, farm)
        self._store_states(farm_id, states)
        
        return {"simulation_id": ..., "daily_states": states}
```

---

## 9. Async Considerations

WOFOST simulation is **CPU-bound**, not I/O-bound. Do NOT use `async def` for simulation routes — use sync endpoints with FastAPI's thread pool:

```python
# CPU-bound tasks: use regular def (FastAPI runs in threadpool automatically)
@router.post("/run")
def run_simulation(request: SimulateRequest, db: Session = Depends(get_db)):
    ...

# I/O-bound tasks (fetching APIs): can use async def + httpx
@router.get("/weather/preview")
async def preview_weather(lat: float, lon: float):
    async with httpx.AsyncClient() as client:
        response = await client.get(NASA_POWER_URL, params=...)
    return response.json()
```

For long-running simulations (> 5 seconds), consider background tasks:
```python
@router.post("/run-async")
def run_simulation_async(
    request: SimulateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    background_tasks.add_task(simulation_service.run_simulation, ...)
    return {"status": "queued", "message": "Simulation started in background"}
```

---

## 10. Error Handling Pattern

```python
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)

class SimulationService:
    def run_simulation(self, farm_id: int, ...):
        try:
            ...
        except FarmNotFoundError:
            raise HTTPException(status_code=404, detail=f"Farm {farm_id} not found")
        except WeatherFetchError as e:
            logger.error(f"Weather fetch failed for farm {farm_id}: {e}")
            raise HTTPException(status_code=502, detail="Weather data unavailable")
        except Exception as e:
            logger.exception(f"Unexpected simulation error for farm {farm_id}")
            raise HTTPException(status_code=500, detail="Simulation failed")
```
