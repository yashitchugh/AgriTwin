"""
api/routes/simulate.py — Simulation Routes
===========================================

Defines the HTTP endpoints for the crop simulation feature:

  POST /simulate           — Run a complete WOFOST crop simulation
  GET  /simulate/crops     — List all available crops and their varieties

Architecture rules (from docs/fastapi_architecture.md):
  1. Routes ONLY parse requests, call services, and return responses.
     Never put WOFOST, PCSE, weather, or soil code directly in route handlers.
  2. Simulation endpoints use regular `def` (not `async def`) because WOFOST
     is CPU-bound. FastAPI automatically runs sync endpoints in a threadpool,
     so they don't block the event loop.
  3. Error handling: routes catch domain exceptions and raise HTTPException.
     Services raise domain-specific exceptions (not HTTPException).
  4. All routes use the router singleton — never `app = FastAPI()` in route files.

Database session injection:
  The `db` parameter uses FastAPI's Depends(get_db) pattern.
  get_db() yields a Session, commits on clean exit, rolls back on exception.
  The session is passed to run_simulation_from_request() which passes it to
  the persistence layer.  Routes never touch the session directly.

Future endpoints to add here (from docs/project_architecture.md Section 10):
  POST /simulate/step         — Run one day, return state (EnKF stepping interface)
  POST /simulate/whatif       — Branch a simulation from current state
  GET  /simulate/{run_id}     — Retrieve stored results of a past run (Phase 2 DB)
  GET  /simulate/{run_id}/daily — Retrieve daily time series for a past run
"""

import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.exceptions import SimulationError, InvalidCropError
from backend.app.api.schemas.simulate import SimulateRequest, SimulateResponse
from backend.app.services.simulation_service import run_simulation_from_request
from backend.app.simulation.crop_provider import list_available_crops
from backend.app.db.session import get_db

logger = logging.getLogger(__name__)

# ── Router ────────────────────────────────────────────────────────────────────
# All routes defined here are prefixed with "/simulate" by main.py when
# app.include_router(router, prefix="/simulate") is called.
# Each individual route path below is the SUFFIX after "/simulate".
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# POST /simulate — Run simulation
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "",                              # POST /simulate  (prefix applied by main.py)
    response_model=SimulateResponse,
    summary="Run a WOFOST crop simulation",
    description=(
        "Runs a complete WOFOST 7.2 water-limited crop simulation for a given "
        "location and crop configuration.\n\n"
        "**Required inputs:** `latitude`, `longitude`, `crop`, `variety`, `sowing_date`\n\n"
        "**Primary outputs (daily time series):**\n"
        "- `lai` — Leaf Area Index [m²/m²]\n"
        "- `sm` — Soil moisture [cm³/cm³]\n"
        "- `tagp` — Total above-ground production [kg/ha]\n"
        "- `twso` — Storage organ weight / yield [kg/ha]\n\n"
        "Weather is fetched from NASA POWER API and soil from SoilGrids unless "
        "you set `use_real_weather=false` or `use_real_soil=false` (useful for "
        "fast testing without internet).\n\n"
        "The response includes a `simulation_id` (UUID) when the run is "
        "successfully persisted to the database. Use this ID with future "
        "GET endpoints to retrieve stored results without re-running the simulation.\n\n"
        "Use `GET /simulate/crops` to see available crop/variety combinations."
    ),
    response_description=(
        "Simulation results including daily LAI/SM/TAGP/TWSO time series, "
        "phenological summary, agronomic metrics, and a simulation_id for "
        "retrieving stored results."
    ),
    tags=["Simulation"],
)
def run_simulate(
    request: SimulateRequest,
    db: Session = Depends(get_db),
) -> SimulateResponse:
    """Run a WOFOST 7.2 water-limited crop simulation.

    This is a synchronous endpoint (not async) because WOFOST is CPU-bound.
    FastAPI automatically dispatches sync endpoints to a threadpool, so this
    endpoint does not block other concurrent requests.

    The `db` session is injected by FastAPI's dependency system.  After the
    simulation completes, the service layer persists the SimulationRun and all
    DailyOutput rows within the same transaction.  get_db() commits on clean
    exit or rolls back on exception.

    Error responses:
        400 — Invalid crop or variety name (not found in PCSE database)
        422 — Pydantic validation error (bad lat/lon, harvest before sowing, etc.)
        500 — PCSE internal engine failure
        502 — NASA POWER or SoilGrids API unreachable (only when use_real_*=true)
    """
    try:
        # Pass the injected session to the service layer.
        # If the DB write fails, run_simulation_from_request() logs the error
        # and returns the response with simulation_id=None (non-fatal).
        result = run_simulation_from_request(request, db=db)
        return result

    except (KeyError, InvalidCropError) as e:
        # Unknown crop_name or variety_name — user error, not server error.
        # Logged at WARNING (not ERROR) because it's not a server fault.
        logger.warning(
            "Invalid crop config [%s/%s]: %s",
            request.crop, request.variety, e,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid crop configuration: {e}. "
                f"Use GET /simulate/crops to see available crops and varieties."
            ),
        )

    except ValueError as e:
        # Physical constraint violation: soil ordering, date logic, etc.
        # Pydantic already handles most date validation — this catches
        # engine-level validation (e.g. soil SMW >= SMFCF from SoilGrids data).
        logger.warning(
            "Parameter validation error for %s/%s: %s",
            request.crop, request.variety, e,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Parameter validation failed: {e}",
        )

    except SimulationError as e:
        # PCSE engine internal failure — unexpected error in WOFOST physics.
        # Logged at ERROR with full traceback for debugging.
        logger.error(
            "Simulation engine error for %s/%s at (%.4f, %.4f): %s",
            request.crop, request.variety,
            request.latitude, request.longitude, e,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Simulation engine failed: {e}",
        )

    except Exception as e:
        # Catch-all: unexpected errors (API timeouts, disk errors, etc.)
        # Full traceback is logged; client receives a generic 500.
        logger.error(
            "Unexpected error in simulate route: %s\n%s",
            e, traceback.format_exc(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {type(e).__name__}: {e}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# GET /simulate/crops — List available crops
# ═══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/crops",                        # GET /simulate/crops
    summary="List available crops and varieties",
    description=(
        "Returns all crop names and their available varieties from the local "
        "WOFOST_crop_parameters database (external_repos/WOFOST_crop_parameters).\n\n"
        "Use the `crop` and `variety` values from this response when calling "
        "`POST /simulate`.\n\n"
        "**Note:** crop names are lowercase (e.g. 'wheat', not 'Wheat')."
    ),
    response_description="Dict mapping crop name → list of variety names",
    tags=["Simulation"],
)
def get_available_crops() -> dict[str, list[str]]:
    """Return all available WOFOST crop/variety combinations.

    This is a lightweight endpoint — it reads from the local YAML files,
    no API calls required. Response is suitable for populating a UI dropdown.

    Response format:
        {
            "wheat": ["Winter_wheat_101", "Spring_wheat_101", ...],
            "maize": ["Grain_maize_201", ...],
            ...
        }
    """
    try:
        crops = list_available_crops()
        # Convert any non-list values to lists for consistent schema
        return {crop: list(varieties) for crop, varieties in crops.items()}

    except Exception as e:
        logger.error("Failed to list crops: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read crop parameter database: {e}",
        )
