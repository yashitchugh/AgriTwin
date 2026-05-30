# AgriTwin Current Project State

## Objective
Agricultural Digital Twin using:
- PCSE/WOFOST
- Ensemble Kalman Filter
- FastAPI
- NASA POWER
- SoilGrids

## Verified PCSE Facts

- Wofost72_WLP_FD is alias of Wofost72_WLP_CWB
- Use pcse.input, not pcse.fileinput
- NASAPowerWeatherDataProvider is in pcse.input
- RDMSOL belongs in soildata
- WOFOST72SiteDataProvider only accepts WAV
- cropd.get_crops_varieties() is the correct API

## Completed

### Documentation
- implementation_notes.md
- project_architecture.md
- pcse_overview.md
- agromanagement_guide.md
- state_variables.md
- weather_pipeline.md
- soil_pipeline.md
- fastapi_architecture.md
- simulation_pipeline.md
- enkf_design.md
- database_schema.md
- minimal_mvp_plan.md

### Working Simulation
backend/app/simulation/minimal_runner.py

Features:
- local WOFOST crop parameters
- dynamic AgroManagement
- synthetic weather
- daily outputs
- verified imports

Outputs:
- LAI
- SM
- TAGP
- TWSO
- DVS

### Weather Service
backend/app/services/weather_service.py

Features:
- NASA POWER integration
- caching
- unit conversion

### Soil Service
backend/app/services/soil_service.py

Features:
- SoilGrids v2.0 integration
- SMW mapping
- SMFCF mapping
- SM0 mapping
- CRAIRC derivation
- caching
- fallback defaults

### Validation

India:
SMW=0.133
SMFCF=0.307
SM0=0.343

Kenya:
SMW=0.207
SMFCF=0.312
SM0=0.396

Simulation with real weather and soil completes successfully.

## Next Objective

Refactor simulator into reusable service architecture.

Then:
1. FastAPI
2. PostgreSQL
3. EnKF
4. Satellite observations
5. AI residual models