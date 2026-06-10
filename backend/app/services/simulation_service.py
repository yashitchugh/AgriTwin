"""
services/simulation_service.py — Simulation Orchestration Service
==================================================================

This is the central service that orchestrates a complete WOFOST crop simulation.
It is the ONLY place in the codebase that knows about both the API layer
(schemas) and the engine layer (PCSE). It deliberately acts as a firewall:
  - Routes pass in a SimulateRequest, get back a SimulateResponse.
  - Routes never touch PCSE, weather, or soil APIs directly.
  - The engine never knows about HTTP or Pydantic schemas.

Execution flow for a single POST /simulate call:
  1. Resolve harvest_date (default: sowing + max_duration)
  2. Fetch real soil data from SoilGrids (with cache) — or use defaults
  3. Call engine.run_simulation() → SimulationResult
  4. Persist SimulationRun + DailyOutputs to the database (if db session provided)
  5. Map SimulationResult → SimulateResponse (schema translation)
  6. Return to route handler

Persistence flow (step 4 detail):
  a. Build SimulationRun ORM object from request + engine results
  b. Save metadata columns (crop, dates, lat/lon, flags)
  c. Save metrics payload (yield, peak_lai, HI, TAGP, TWSO, total_days)
  d. Save phenological summary (dos, doe, doa, dom, doh, laimax, tagp, twso)
  e. Save weather snapshot (source, date range)
  f. Save soil snapshot (SMW, SMFCF, SM0, etc. — exactly as used)
  g. Bulk-insert DailyOutput rows (one per simulated day)

Architecture notes:
  - run_simulation_from_request() accepts an optional SQLAlchemy Session.
    If db=None (e.g. in unit tests), persistence is skipped entirely.
    If db is provided, all 7 save steps run within the same transaction.
  - Persistence failure never crashes the response.  If the DB write fails,
    the SimulateResponse is still returned (without simulation_id).
    The error is logged at ERROR level for monitoring.
  - This service is STATELESS: no instance variables that change between calls.
  - The function signature stays decoupled from FastAPI — no HTTPException here.

EnKF readiness (Phase 3):
  The run_simulation_from_request() function will need to:
  1. Accept an observations dict {date: {LAI: value, uncertainty: std}}
  2. Set step_by_step=True in the engine call
  3. After each step, call assimilation_service.update(wofost, obs) if obs exists
  See docs/simulation_pipeline.md Section 2 for the day-by-day loop design.
"""

import logging
import datetime as dt
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.exceptions import (
    SimulationError,
    WeatherFetchError,
    SoilFetchError,
)
from backend.app.simulation.engine import run_simulation, SimulationResult
from backend.app.services.soil_service import SoilService
from backend.app.api.schemas.simulate import (
    SimulateRequest,
    SimulateResponse,
    DailyState,
    PhenologicalSummary,
    AgronomicMetrics,
)

logger = logging.getLogger(__name__)

# ── Singleton service instances ───────────────────────────────────────────────
# Both services are stateless: they cache data on disk, not in memory.
# Safe to create once at module load time and reuse across requests.
_soil_service = SoilService()


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation_from_request(
    request: SimulateRequest,
    *,
    db: Optional[Session] = None,
) -> SimulateResponse:
    """Execute a complete WOFOST simulation from a validated API request.

    This is the main entry point called by the route handler. It:
      1. Resolves optional dates
      2. Fetches real soil data (with graceful fallback to defaults)
      3. Calls the simulation engine
      4. Persists results to the database (if db session is provided)
      5. Converts the internal SimulationResult to the API response schema

    Args:
        request: Validated SimulateRequest (all validators have already passed)
        db:      Optional SQLAlchemy Session (keyword-only).
                 If provided: SimulationRun + DailyOutputs are persisted and
                 simulation_id is returned in the response.
                 If None: persistence is skipped (useful in unit tests).

    Returns:
        SimulateResponse ready to serialize and return to the client.
        response.simulation_id is populated when db is provided and persistence
        succeeds. It is None when db=None or if DB write fails.

    Raises:
        SimulationError:       PCSE engine internal failure
        WeatherFetchError:     NASA POWER API unreachable (when use_real_weather=True)
        InvalidCropError:      crop_name or variety_name not in PCSE database
        InvalidParameterError: Physical constraints violated in derived parameters
    """
    logger.info(
        "Simulation start: %s/%s at (%.4f°N, %.4f°E), sow=%s",
        request.crop, request.variety,
        request.latitude, request.longitude,
        request.sowing_date,
    )

    # ── Step 1: Resolve harvest date ──────────────────────────────────────────
    harvest_date = _resolve_harvest_date(request)

    # ── Step 2: Fetch soil parameters ─────────────────────────────────────────
    soil_params = _fetch_soil_params(request)

    # ── Step 3: Run simulation engine ─────────────────────────────────────────
    # Convert IrrigationEvent Pydantic objects → plain dicts so the engine
    # layer stays decoupled from the API schema layer (no Pydantic in engine).
    irrigation_dicts = [
        {"date": ev.date, "amount_mm": ev.amount_mm}
        for ev in (request.irrigation_events or [])
    ]
    result = _run_engine(request, harvest_date, soil_params, irrigation_dicts)

    # ── Step 4: Persist to database ───────────────────────────────────────────
    # All 7 save steps run here. Failure is non-fatal: the response is always
    # returned. The simulation_id in the response is None if persistence fails.
    simulation_id: Optional[uuid.UUID] = None
    if db is not None:
        simulation_id = _persist_results(
            db=db,
            request=request,
            result=result,
            harvest_date=harvest_date,
            soil_params=soil_params,
        )

    # ── Step 5: Build and return API response ─────────────────────────────────
    response = _build_response(
        request=request,
        result=result,
        simulation_id=simulation_id,
    )

    logger.info(
        "Simulation complete: %s/%s → %d days, yield=%.0f kg/ha, HI=%.3f, db_id=%s",
        request.crop, request.variety,
        result.total_days,
        result.metrics.get("final_twso_kg_ha", 0),
        result.metrics.get("harvest_index", 0),
        simulation_id,
    )
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_harvest_date(request: SimulateRequest) -> dt.date:
    """Compute effective harvest date.

    If harvest_date is provided in the request, use it directly.
    Otherwise, default to sowing_date + max_duration days.

    This default is intentionally conservative: max_duration=365 days gives
    most annual crops plenty of time to reach maturity under any climate.
    The WOFOST model will stop automatically at maturity even if max_duration
    hasn't been reached.
    """
    if request.harvest_date is not None:
        return request.harvest_date

    harvest = request.sowing_date + dt.timedelta(days=request.max_duration)
    logger.info(
        "No harvest_date provided — defaulting to sowing + %d days = %s",
        request.max_duration, harvest,
    )
    return harvest


def _fetch_soil_params(request: SimulateRequest) -> Optional[dict]:
    """Fetch soil parameters, with fallback to WOFOST defaults.

    Returns:
        dict with PCSE soil keys (SMW, SMFCF, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB)
        or None if real soil not requested or fetch failed.
        The engine handles None → uses its own built-in defaults.
    """
    if not request.use_real_soil:
        logger.info("use_real_soil=False — skipping SoilGrids, using engine defaults")
        return None

    try:
        params = _soil_service.get_soil_params(
            latitude=request.latitude,
            longitude=request.longitude,
        )
        logger.info(
            "SoilGrids OK: SMW=%.3f, SMFCF=%.3f, SM0=%.3f, RDMSOL=%.0f cm",
            params["SMW"], params["SMFCF"], params["SM0"], params["RDMSOL"],
        )
        return params

    except Exception as e:
        # Log and fall back to defaults — the simulation can still run.
        # A hard failure here would block all requests for users in regions
        # where SoilGrids has sparse data. Fallback is the safer choice for MVP.
        logger.warning(
            "SoilGrids fetch failed (%s: %s) — using engine default medium-loam soil",
            type(e).__name__, e,
        )
        return None


def _run_engine(
    request: SimulateRequest,
    harvest_date: dt.date,
    soil_params: Optional[dict],
    irrigation_dicts: Optional[list] = None,
) -> SimulationResult:
    """Call the WOFOST engine and handle engine-level exceptions.

    Wraps engine.run_simulation() to convert bare exceptions into
    domain-specific exception types that the route can handle semantically.

    Engine parameter mapping:
        request.crop             → crop_name
        request.variety          → variety_name
        request.sowing_date      → sow_date
        harvest_date             → harvest_date  (resolved above)
        request.latitude         → latitude
        request.longitude        → longitude
        soil_params              → soil_params (None → engine uses defaults)
        request.use_real_weather → use_nasa_weather
        request.max_duration     → max_duration
        irrigation_dicts         → irrigation_events (plain dicts, schema-agnostic)
        step_by_step=False       → batch mode (fastest; step_by_step=True for EnKF)
    """
    try:
        return run_simulation(
            crop_name=request.crop,
            variety_name=request.variety,
            sow_date=request.sowing_date,
            harvest_date=harvest_date,
            latitude=request.latitude,
            longitude=request.longitude,
            soil_params=soil_params,
            use_nasa_weather=request.use_real_weather,
            max_duration=request.max_duration,
            irrigation_events=irrigation_dicts or None,
            # step_by_step=False: run_till_terminate() — fastest for batch simulation.
            # When EnKF is added (Phase 3), this will be set to True and the loop
            # will interleave wofost.run(days=1) with assimilation updates.
            step_by_step=False,
        )

    except KeyError as e:
        # PCSE raises KeyError for unknown crop_name or variety_name.
        # Re-raise as our domain exception for the route to handle.
        raise KeyError(str(e)) from e

    except ValueError as e:
        # Soil ordering violation or date inconsistency from engine.
        raise ValueError(str(e)) from e

    except Exception as e:
        # Unexpected PCSE internal error: wrap it so the route logs it properly.
        raise SimulationError(
            f"WOFOST engine failed for {request.crop}/{request.variety}: {e}"
        ) from e


def _persist_results(
    *,
    db: Session,
    request: SimulateRequest,
    result: SimulationResult,
    harvest_date: dt.date,
    soil_params: Optional[dict],
) -> Optional[uuid.UUID]:
    """Persist a completed simulation to the database.

    Saves 7 artefacts within the caller's transaction:
      1. SimulationRun row (metadata, scalar results, phenological dates)
      2. metrics_payload  (AgronomicMetrics dict as JSON)
      3. summary_payload  (PhenologicalSummary dict as JSON)
      4. request_payload  (full SimulateRequest as JSON for reproducibility)
      5. weather_snapshot (source + date range used by the engine)
      6. soil_snapshot    (exact soil parameters fed to the engine)
      7. DailyOutput rows (one per simulated day, bulk-inserted)

    Transaction ownership:
      This function calls flush() through the repository layer.
      It does NOT commit.  The route handler's get_db() dependency commits
      on clean exit, or rolls back on exception.

    Failure isolation:
      If any step raises an exception, the error is caught and logged.
      The function returns None, signalling to the caller that the DB write
      failed.  This ensures a DB outage never prevents the HTTP response
      from reaching the client.

    Args:
        db:          An open SQLAlchemy Session (provided by get_db dependency).
        request:     The original validated SimulateRequest.
        result:      The SimulationResult from the WOFOST engine.
        harvest_date: Effective harvest date (resolved from request).
        soil_params:  Soil parameters used by the engine (None if defaults).

    Returns:
        UUID of the newly created SimulationRun, or None if persistence failed.
    """
    # Lazy imports inside the function to avoid circular imports at module load.
    # (models → db.base → db.session → nothing; service → models is safe here)
    from backend.app.models.simulation_run import SimulationRun
    from backend.app.models.daily_output import DailyOutput
    from backend.app.repositories.simulation_repository import SimulationRepository
    from backend.app.repositories.daily_output_repository import DailyOutputRepository

    try:
        sim_repo = SimulationRepository(db)
        daily_repo = DailyOutputRepository(db)

        m = result.metrics      # plain dict from compute_harvest_metrics()
        s = result.summary      # plain dict from parse_summary_output(), may be {}

        # ── 1. Build SimulationRun ORM object ─────────────────────────────────
        run_id = uuid.uuid4()
        run = SimulationRun(
            id=run_id,

            # No field_id yet — ad-hoc simulation (Phase 2 will wire this up
            # when the farm/field registration endpoints are live).
            field_id=None,

            # ── 2. Metadata ───────────────────────────────────────────────────
            run_type=(
                "irrigated"
                if request.irrigation_events
                else "baseline"
            ),
            status="completed",
            model_name="Wofost72_WLP_FD",
            model_version="7.2",
            latitude=request.latitude,
            longitude=request.longitude,
            crop=request.crop,
            variety=request.variety,
            sowing_date=request.sowing_date,
            harvest_date=harvest_date,
            use_real_weather=request.use_real_weather,
            use_real_soil=request.use_real_soil,

            # ── 3. Scalar metrics (denormalised for fast queries) ─────────────
            yield_kg_ha=m.get("final_twso_kg_ha"),
            peak_lai=m.get("peak_lai"),
            harvest_index=m.get("harvest_index"),
            final_tagp=m.get("final_tagp_kg_ha"),
            final_twso=m.get("final_twso_kg_ha"),
            total_days=result.total_days,

            # ── 4. Phenological summary (scalar dates) ────────────────────────
            # Convert ISO strings back to date objects for the Date columns.
            dos=_parse_date(s.get("dos")),
            doe=_parse_date(s.get("doe")),
            doa=_parse_date(s.get("doa")),
            dom=_parse_date(s.get("dom")),
            doh=_parse_date(s.get("doh")),

            # ── 5-7. JSON payload columns ─────────────────────────────────────
            # request_payload: full request body → enables exact run reproduction
            request_payload=request.model_dump(mode="json"),

            # metrics_payload: full AgronomicMetrics dict as returned to the API
            metrics_payload=m,

            # summary_payload: full PhenologicalSummary dict
            summary_payload=s if s else None,

            # weather_snapshot: source metadata (not the full daily series)
            weather_snapshot=_build_weather_snapshot(request),

            # soil_snapshot: exact parameters fed to the WOFOST engine
            soil_snapshot=soil_params,

            # warnings: placeholder — will collect engine warnings in Phase 3
            warnings=[],
            notes=None,
        )

        # ── Save SimulationRun (steps 1–6) ────────────────────────────────────
        sim_repo.save_simulation(run)
        logger.info("Persisted SimulationRun id=%s", run_id)

        # ── 7. Bulk-insert DailyOutput rows ───────────────────────────────────
        # result.daily_output is a list of dicts with lowercase keys matching
        # the DailyOutput column names exactly (produced by output_parser.py).
        daily_rows = [
            DailyOutput(
                simulation_run_id=run_id,
                # Date is stored as ISO string in result.daily_output; convert to date.
                date=dt.date.fromisoformat(record["date"]),
                dvs=record.get("dvs"),
                lai=record.get("lai"),
                sm=record.get("sm"),
                tagp=record.get("tagp"),
                twso=record.get("twso"),
                twlv=record.get("twlv"),
                twst=record.get("twst"),
                twrt=record.get("twrt"),
                rftra=record.get("rftra"),
                tra=record.get("tra"),
                evs=record.get("evs"),
                rd=record.get("rd"),
            )
            for record in result.daily_output
        ]
        daily_repo.save_daily_outputs(daily_rows)
        logger.info(
            "Persisted %d DailyOutput rows for SimulationRun id=%s",
            len(daily_rows), run_id,
        )

        return run_id

    except Exception as exc:
        # Persistence failure must never prevent the HTTP response.
        # Log with full traceback; return None so the caller omits simulation_id.
        logger.error(
            "DB persistence failed for %s/%s at (%.4f, %.4f): %s",
            request.crop, request.variety,
            request.latitude, request.longitude,
            exc,
            exc_info=True,
        )
        # Roll back any partial writes from this persistence attempt.
        # The session is still usable after rollback (get_db handles close).
        try:
            db.rollback()
        except Exception:
            pass
        return None


def _build_response(
    request: SimulateRequest,
    result: SimulationResult,
    simulation_id: Optional[uuid.UUID] = None,
) -> SimulateResponse:
    """Translate a SimulationResult into the API SimulateResponse schema.

    This function performs the schema translation between internal engine
    representations and the external API contract. It is the only place
    where PCSE output field names are mapped to API field names.

    Mapping summary:
        result.daily_output  → list[DailyState]
        result.metrics       → AgronomicMetrics
        result.summary       → PhenologicalSummary (if available)
        simulation_id        → SimulateResponse.simulation_id (UUID | None)
    """
    # ── Daily states → DailyState list ───────────────────────────────────────
    # result.daily_output is already normalized: lowercase keys, ISO date strings.
    # The DailyState(**record) unpacking relies on matching field names between
    # output_parser.TRACKED_VARIABLES and DailyState model fields.
    daily_states = [
        DailyState(**record)
        for record in result.daily_output
    ]

    # ── Metrics → AgronomicMetrics ────────────────────────────────────────────
    # result.metrics is a plain dict from compute_harvest_metrics().
    # AgronomicMetrics validates field types automatically via Pydantic.
    metrics = AgronomicMetrics(**result.metrics)

    # ── Summary → PhenologicalSummary ─────────────────────────────────────────
    # Summary is None if the crop didn't complete its cycle (truncated by
    # harvest_date). In that case we return None in the response — callers
    # should check for this when interpreting phenological dates.
    summary = None
    if result.summary:
        # Filter to only fields defined in PhenologicalSummary to avoid
        # Pydantic validation errors from unexpected PCSE summary keys.
        summary_fields = PhenologicalSummary.model_fields.keys()
        filtered = {k: v for k, v in result.summary.items() if k in summary_fields}
        summary = PhenologicalSummary(**filtered)

    # ── Compose response ──────────────────────────────────────────────────────
    final_yield = result.metrics.get("final_twso_kg_ha", 0)
    return SimulateResponse(
        status="success",
        message=(
            f"Simulation completed successfully. "
            f"{result.total_days} days simulated, "
            f"yield = {final_yield:.0f} kg/ha."
        ),
        simulation_id=simulation_id,
        request=request,
        metrics=metrics,
        summary=summary,
        daily_states=daily_states,
    )


# ── Small private utilities ───────────────────────────────────────────────────

def _parse_date(value: Optional[str]) -> Optional[dt.date]:
    """Convert an ISO date string to a date object, or return None."""
    if value is None:
        return None
    try:
        if isinstance(value, dt.date):
            return value
        return dt.date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _build_weather_snapshot(request: SimulateRequest) -> dict:
    """Build a compact weather metadata dict for storage in weather_snapshot.

    Stores the provenance of weather data used (not the full daily series,
    which is in DailyOutput). Useful for diagnosing climate-related anomalies.
    """
    return {
        "source": "nasa_power" if request.use_real_weather else "synthetic",
        "latitude": request.latitude,
        "longitude": request.longitude,
        "season_start": request.sowing_date.isoformat(),
        "season_end": (
            request.harvest_date.isoformat()
            if request.harvest_date
            else None
        ),
    }
