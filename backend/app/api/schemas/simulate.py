"""
api/schemas/simulate.py — Pydantic Schemas for POST /simulate
==============================================================

Defines the request body and response models for the simulation endpoint.

Design principles:
  1. Request fields map 1-to-1 to what a crop scientist would recognise.
  2. Response includes the 5 primary state variables (LAI, SM, TAGP, TWSO, RFTRA)
     as a daily time series, plus phenological summary and agronomic metrics.
  3. Every field has a description, units, and example value for auto-generated
     Swagger/ReDoc documentation.
  4. Validators enforce physical constraints before any PCSE code is called,
     giving clear user-facing error messages instead of PCSE stack traces.
  5. The DailyState model is intentionally designed to be extended:
     - An `assimilated` boolean will be added when EnKF is implemented (Phase 3)
     - An `observation_lai` optional field will be added when satellite ingestion
       is implemented (Phase 4)

PCSE naming conventions (from docs/implementation_notes.md):
  - Internal PCSE variable names: UPPERCASE (DVS, LAI, SM, TAGP, TWSO, RFTRA)
  - API / database field names:   lowercase (dvs, lai, sm, tagp, twso, rftra)

Irrigation support (Phase 2):
  - IrrigationEvent: a single timed water application event
  - SimulateRequest.irrigation_events: optional list of events (default: [])
  - PCSE signal name: "irrigate"  (NOT "irrigation" — exact PCSE string required)
  - Each event maps to a PCSE AgroManagement TimedEvents entry with:
      amount      — water applied in mm (stored as-is; PCSE reads mm)
      efficiency  — fraction reaching root zone (0.7 default = 70% application efficiency)
  - Dates are validated to fall within [sowing_date, harvest_date] at schema level
"""

import datetime as dt
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════════
# IRRIGATION EVENT SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

class IrrigationEvent(BaseModel):
    """A single timed irrigation application event.

    Maps directly to one row in PCSE AgroManagement's TimedEvents events_table:
        events_table:
          - YYYY-MM-DD: {amount: <mm>, efficiency: 0.7}

    PCSE irrigation mechanics:
      - The "irrigate" signal is sent to WOFOST's WaterbalanceFD sub-model.
      - `amount` (mm) is added to the root-zone water content on the event date.
      - `efficiency` (0–1) accounts for delivery losses (evaporation, runoff
        before infiltration). Effective water added = amount × efficiency.
      - PCSE silently ignores events outside the campaign window — validation
        at the schema level prevents this mistake.

    Validation rules:
      - amount_mm > 0          (no zero or negative irrigation)
      - amount_mm <= 200       (physical cap: 200 mm in one application is an
                                 extreme flood event; typical values: 20–80 mm)
      - date must be >= sowing_date and <= harvest_date (validated in SimulateRequest)
    """

    date: dt.date = Field(
        ...,
        description=(
            "Date of irrigation application in ISO format (YYYY-MM-DD). "
            "Must fall between sowing_date and harvest_date (inclusive). "
            "PCSE silently drops events outside the campaign window — "
            "this validator prevents silent data loss."
        ),
        examples=["2021-01-15"],
    )
    amount_mm: float = Field(
        ...,
        gt=0,
        le=200,
        description=(
            "Volume of water applied in millimetres [mm]. "
            "Represents the depth of water applied to the field surface. "
            "PCSE's WaterbalanceFD receives this as 'amount' in its irrigation signal. "
            "Typical single-application values: 20–80 mm. "
            "Maximum allowed: 200 mm (extreme flood irrigation). "
            "Must be strictly positive (> 0)."
        ),
        examples=[40.0],
    )

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "date": "2021-01-15",
            "amount_mm": 40.0,
        }
    })


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

class SimulateRequest(BaseModel):
    """Request body for POST /simulate.

    Minimum viable request requires 5 fields: latitude, longitude, crop,
    variety, sowing_date. All other fields have sensible defaults.

    Irrigation events are optional — omitting them (or passing []) produces
    a rainfed simulation identical to pre-irrigation behaviour (backward compatible).

    Curl example (with irrigation):
        curl -X POST http://localhost:8000/simulate \\
             -H 'Content-Type: application/json' \\
             -d '{
               "latitude": 28.6,
               "longitude": 77.2,
               "crop": "wheat",
               "variety": "Winter_wheat_101",
               "sowing_date": "2020-10-15",
               "harvest_date": "2021-07-30",
               "irrigation_events": [
                 {"date": "2021-01-15", "amount_mm": 40},
                 {"date": "2021-02-20", "amount_mm": 50}
               ]
             }'
    """

    # ── Location ─────────────────────────────────────────────────────────────
    latitude: float = Field(
        ...,
        ge=-90,
        le=90,
        description=(
            "Site latitude in decimal degrees (WGS84). "
            "Used to fetch weather (NASA POWER) and soil (SoilGrids) data."
        ),
        examples=[28.6],
    )
    longitude: float = Field(
        ...,
        ge=-180,
        le=180,
        description=(
            "Site longitude in decimal degrees (WGS84). "
            "Used to fetch weather (NASA POWER) and soil (SoilGrids) data."
        ),
        examples=[77.2],
    )

    # ── Crop configuration ────────────────────────────────────────────────────
    crop: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Lowercase PCSE crop name. Must match a crop in the WOFOST parameter "
            "database exactly. Common values: 'wheat', 'maize', 'rice', 'soybean', "
            "'barley', 'sorghum'. Use GET /crops to list all available crops."
        ),
        examples=["wheat"],
    )
    variety: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "PCSE variety identifier. Must match a variety key within the crop's "
            "YAML parameter file. Example: 'Winter_wheat_101' for wheat. "
            "Use GET /crops to list available varieties per crop."
        ),
        examples=["Winter_wheat_101"],
    )

    # ── Dates ─────────────────────────────────────────────────────────────────
    sowing_date: dt.date = Field(
        ...,
        description=(
            "Sowing / planting date in ISO format (YYYY-MM-DD). "
            "This is the start of the crop growing season. "
            "AgroManagement campaign starts 14 days before this date."
        ),
        examples=["2020-10-15"],
    )
    harvest_date: Optional[dt.date] = Field(
        default=None,
        description=(
            "Expected harvest date (YYYY-MM-DD). If omitted, the simulation runs "
            "until crop maturity or sowing_date + max_duration days, whichever "
            "comes first. For most crops, 270–365 days from sowing is sufficient."
        ),
        examples=["2021-07-30"],
    )

    # ── Irrigation ────────────────────────────────────────────────────────────
    irrigation_events: list[IrrigationEvent] = Field(
        default=[],
        description=(
            "Optional list of timed irrigation applications. "
            "Each event specifies a date and water amount in mm. "
            "Omitting this field (or passing []) produces a rainfed simulation "
            "— fully backward compatible with existing requests. "
            "Events are mapped to PCSE AgroManagement TimedEvents with "
            "event_signal='irrigate'. Application efficiency defaults to 0.7 "
            "(70% of applied water reaches the root zone). "
            "Dates must fall between sowing_date and harvest_date."
        ),
        examples=[
            [
                {"date": "2021-01-15", "amount_mm": 40},
                {"date": "2021-02-20", "amount_mm": 50},
            ]
        ],
    )

    # ── Simulation control ────────────────────────────────────────────────────
    max_duration: int = Field(
        default=365,
        ge=30,
        le=730,
        description=(
            "Maximum simulation duration in days. Guards against infinite loops "
            "when the crop fails to reach maturity (e.g., wrong variety for climate). "
            "The simulation stops at min(harvest_date, sowing_date + max_duration)."
        ),
    )

    # ── Data source flags ─────────────────────────────────────────────────────
    use_real_weather: bool = Field(
        default=True,
        description=(
            "If True (default), fetch daily weather from the NASA POWER API "
            "(requires internet, ~5s). The weather covers the full sowing-to-harvest "
            "period plus a 14-day pre-season buffer. "
            "If False, use deterministic synthetic weather — useful for testing "
            "or offline development."
        ),
    )
    use_real_soil: bool = Field(
        default=True,
        description=(
            "If True (default), fetch depth-averaged soil properties from the "
            "SoilGrids v2.0 REST API (SMW, SMFCF, SM0, CRAIRC). "
            "If False, use default medium-loam parameters: "
            "SMW=0.10, SMFCF=0.30, SM0=0.45."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("crop")
    @classmethod
    def crop_must_be_lowercase(cls, v: str) -> str:
        """PCSE crop names are always lowercase (docs/implementation_notes.md WARNING 4).

        YAMLCropDataProvider stores crops by lowercase key. Passing 'Wheat'
        instead of 'wheat' raises a KeyError deep inside PCSE with an unhelpful
        message — this validator provides a clear user-facing error instead.
        """
        if v != v.lower():
            raise ValueError(
                f"crop must be lowercase (got '{v}'). "
                f"PCSE crop names are case-sensitive: use '{v.lower()}' not '{v}'."
            )
        return v

    @model_validator(mode="after")
    def harvest_must_be_after_sowing(self) -> "SimulateRequest":
        """Validate harvest_date > sowing_date at the model level.

        This uses model_validator (not field_validator) because it needs
        access to both fields simultaneously.
        """
        if self.harvest_date is not None:
            if self.harvest_date <= self.sowing_date:
                raise ValueError(
                    f"harvest_date ({self.harvest_date}) must be strictly after "
                    f"sowing_date ({self.sowing_date}). "
                    f"Minimum gap: 1 day."
                )
            # Sanity check: harvest more than 730 days after sowing is almost
            # certainly an input error (no annual crop takes 2+ years).
            delta = (self.harvest_date - self.sowing_date).days
            if delta > 730:
                raise ValueError(
                    f"harvest_date is {delta} days after sowing_date — this exceeds "
                    f"730 days (2 years). Check your dates."
                )
        return self

    @model_validator(mode="after")
    def irrigation_dates_within_season(self) -> "SimulateRequest":
        """Validate all irrigation event dates fall within the growing season.

        PCSE silently ignores TimedEvents that fall outside the campaign window
        (docs/agromanagement_guide.md Mistake 4). A silent ignore would confuse
        users who expect their irrigation to be applied — this validator
        raises a clear error instead.

        The validation window is [sowing_date, harvest_date]. When harvest_date
        is None, only the lower bound (sowing_date) is enforced, since the
        upper bound is determined at runtime by max_duration.
        """
        for ev in self.irrigation_events:
            if ev.date < self.sowing_date:
                raise ValueError(
                    f"Irrigation event on {ev.date} is before sowing_date "
                    f"({self.sowing_date}). Irrigation before crop emergence "
                    f"is not supported — move the event to on or after sowing_date."
                )
            if self.harvest_date is not None and ev.date > self.harvest_date:
                raise ValueError(
                    f"Irrigation event on {ev.date} is after harvest_date "
                    f"({self.harvest_date}). PCSE would silently ignore this event. "
                    f"Move the event before harvest_date or remove it."
                )
        return self

    model_config = ConfigDict(
        # Allow extra fields to be ignored (forward compatibility)
        extra="ignore",
        json_schema_extra={
            "example": {
                "latitude": 28.6,
                "longitude": 77.2,
                "crop": "wheat",
                "variety": "Winter_wheat_101",
                "sowing_date": "2020-10-15",
                "harvest_date": "2021-07-30",
                "use_real_weather": True,
                "use_real_soil": True,
                "irrigation_events": [
                    {"date": "2021-01-15", "amount_mm": 40},
                    {"date": "2021-02-20", "amount_mm": 50},
                ],
            }
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class DailyState(BaseModel):
    """One day of WOFOST simulation output.

    The 5 primary state variables (lai, sm, tagp, twso, rftra) are the core
    outputs documented in the task requirements. Additional variables (dvs,
    tra, rd) are included because they are scientifically valuable and
    required for the EnKF state vector in Phase 3.

    Why Optional?
        Before sowing, crop variables (lai, tagp, etc.) are None — the plant
        does not exist yet. Soil moisture (sm) exists from day 1.
        After maturity, some variables plateau or become None.

    Irrigation diagnostics:
        rftra — the key irrigation stress indicator. Values < 1.0 on days
        without irrigation events indicate the crop was water-stressed.
        After an irrigation event, rftra should rise toward 1.0 if sufficient
        water was applied.

    EnKF design note (Phase 3):
        When data assimilation is implemented, two fields will be added:
          - assimilated: bool — True if an EnKF update was applied on this day
          - observation_lai: Optional[float] — satellite/field LAI measurement used
        These are left as TODO comments to document the planned extension point.
    """

    date: str = Field(
        description="Simulation date in ISO format (YYYY-MM-DD).",
    )

    # ── Required outputs (per task specification) ─────────────────────────
    lai: Optional[float] = Field(
        default=None,
        description=(
            "Leaf Area Index [m²/m²]. "
            "Green leaf area per unit ground area. "
            "Starts at 0 at emergence, peaks during vegetative growth, "
            "declines as leaves senesce toward maturity. "
            "Key variable for satellite assimilation (NDVI/LAI products)."
        ),
    )
    sm: Optional[float] = Field(
        default=None,
        description=(
            "Volumetric soil moisture in the root zone [cm³ water / cm³ soil]. "
            "Ranges between SMW (wilting point) and SM0 (saturation). "
            "Non-None from simulation day 1 (waterbalance initializes immediately). "
            "Should rise visibly on irrigation event dates and then decline as "
            "the crop extracts water via transpiration."
        ),
    )
    tagp: Optional[float] = Field(
        default=None,
        description=(
            "Total Above-Ground Production [kg dry matter / ha]. "
            "Cumulative biomass of leaves + stems + storage organs above soil. "
            "Monotonically increasing until maturity."
        ),
    )
    twso: Optional[float] = Field(
        default=None,
        description=(
            "Total Weight of Storage Organs [kg / ha]. "
            "Grain/seed/tuber weight — the economically relevant yield component. "
            "Starts accumulating at anthesis (DVS=1), reaches maximum at maturity."
        ),
    )

    # ── Irrigation stress diagnostic ──────────────────────────────────────
    rftra: Optional[float] = Field(
        default=None,
        description=(
            "Relative water stress factor for transpiration [-]. "
            "RFTRA = Actual Transpiration / Potential Transpiration. "
            "Range: 0.0 (maximum stress, crop cannot transpire) to "
            "1.0 (no stress, crop transpires at full potential). "
            "This is the primary irrigation diagnostic variable: "
            "RFTRA < 1.0 on a given day indicates the crop was water-stressed. "
            "After a successful irrigation event, RFTRA should approach 1.0. "
            "Useful for identifying when and how much irrigation improved "
            "yield potential relative to a rainfed baseline."
        ),
    )

    # ── Additional state variables (scientifically valuable) ─────────────
    dvs: Optional[float] = Field(
        default=None,
        description=(
            "Development Stage [-]. "
            "0=emergence, 1=anthesis (flowering), 2=maturity. "
            "DVS drives phenological transitions in WOFOST. "
            "Useful for determining where in the season the crop is."
        ),
    )
    tra: Optional[float] = Field(
        default=None,
        description=(
            "Actual crop Transpiration [cm/day]. "
            "Reduced from potential transpiration under water stress. "
            "TRA < ET0 indicates water stress. "
            "EnKF assimilation hook: TRA connects to satellite ET products."
        ),
    )
    rd: Optional[float] = Field(
        default=None,
        description=(
            "Root Depth [cm]. "
            "Deepens during vegetative phase as roots explore the soil profile. "
            "Important for soil water extraction dynamics."
        ),
    )
    evs: Optional[float] = Field(
        default=None,
        description=(
            "Actual Soil Evaporation [cm/day]. "
            "Decreases as LAI increases and canopy shades the soil. "
            "Non-None in step-by-step mode only."
        ),
    )

    # ── Cumulative biomass pools (batch mode) ─────────────────────────────
    twlv: Optional[float] = Field(
        default=None,
        description="Total Weight of Leaves [kg dry matter / ha]. Cumulative.",
    )
    twst: Optional[float] = Field(
        default=None,
        description="Total Weight of Stems [kg dry matter / ha]. Cumulative.",
    )
    twrt: Optional[float] = Field(
        default=None,
        description="Total Weight of Roots [kg dry matter / ha]. Cumulative.",
    )

    # ── Live-state organ weights (step-by-step / EnKF mode only) ─────────
    # These are None in current batch-mode (run_till_terminate) runs.
    # They will be populated when step_by_step=True is used (Phase 3).
    wlv: Optional[float] = Field(
        default=None,
        description=(
            "Actual leaf weight [kg/ha] at current timestep (pre-senescence). "
            "None in batch mode."
        ),
    )
    wst: Optional[float] = Field(
        default=None,
        description="Actual stem weight [kg/ha] at current timestep. None in batch mode.",
    )
    wrt: Optional[float] = Field(
        default=None,
        description="Actual root weight [kg/ha] at current timestep. None in batch mode.",
    )
    wso: Optional[float] = Field(
        default=None,
        description="Actual storage organ weight [kg/ha] at current timestep. None in batch mode.",
    )

    # ── Digital Twin / EnKF extension hooks ──────────────────────────────
    # TODO (EnKF Phase 3): Add these fields when assimilation is implemented:
    #   assimilated: bool = Field(default=False, ...)
    #   observation_lai: Optional[float] = Field(default=None, ...)
    #   ensemble_spread_lai: Optional[float] = Field(default=None, ...)


class PhenologicalSummary(BaseModel):
    """Season-level phenological summary from PCSE get_summary_output().

    Contains dates of key phenological events and season-level aggregates.
    These come directly from the WOFOST CropSimulation component and are
    only available after run_till_terminate() completes.

    In step-by-step mode (future EnKF), this summary may be None if the
    crop hasn't reached maturity yet — the route handles this gracefully.
    """
    dos: Optional[str] = Field(None, description="Date of Sowing (ISO)")
    doe: Optional[str] = Field(None, description="Date of Emergence (ISO)")
    doa: Optional[str] = Field(None, description="Date of Anthesis / Flowering (ISO)")
    dom: Optional[str] = Field(None, description="Date of Maturity (ISO)")
    doh: Optional[str] = Field(None, description="Date of Harvest (ISO)")
    laimax: Optional[float] = Field(None, description="Peak Leaf Area Index [m²/m²]")
    tagp: Optional[float] = Field(None, description="Final total above-ground biomass [kg/ha]")
    twso: Optional[float] = Field(None, description="Final storage organ weight / yield [kg/ha]")


class AgronomicMetrics(BaseModel):
    """Computed agronomic performance metrics for the full season.

    Derived from the daily output after the simulation completes.
    These summarize the season in 5–6 numbers for quick assessment.

    EnKF note (Phase 3):
        When assimilation is added, include:
          - assimilation_events: int — number of days on which EnKF updated state
          - mean_ensemble_spread: float — average LAI ensemble spread [m²/m²]
    """
    total_days: int = Field(
        description="Total number of simulated days (campaign start to end).",
    )
    peak_lai: float = Field(
        description="Maximum Leaf Area Index reached during the season [m²/m²].",
    )
    final_dvs: float = Field(
        description=(
            "Development Stage at the last simulated day. "
            "2.0 = full maturity reached. <2.0 = crop truncated by harvest_date."
        ),
    )
    final_tagp_kg_ha: float = Field(
        description="Total above-ground biomass at season end [kg dry matter / ha].",
    )
    final_twso_kg_ha: float = Field(
        description=(
            "Storage organ weight at season end [kg / ha]. "
            "This is the simulated yield — the main agronomic output."
        ),
    )
    harvest_index: float = Field(
        description=(
            "Harvest Index = TWSO / TAGP [-]. "
            "Fraction of total biomass that becomes harvestable yield. "
            "Typical values: wheat 0.40–0.55, maize 0.45–0.55."
        ),
    )


class SimulateResponse(BaseModel):
    """Full response from POST /simulate.

    Structure:
        status          → "success" | "error"
        message         → human-readable summary
        simulation_id   → UUID of the persisted SimulationRun (None if DB unavailable)
        request         → echo of input parameters (for traceability)
        metrics         → 6 key agronomic numbers
        summary         → phenological dates from WOFOST
        daily_states    → full daily time series (LAI, SM, TAGP, TWSO, RFTRA, ...)

    The response is intentionally verbose: downstream dashboards and the
    future EnKF service need the full daily_states array. If bandwidth is
    a concern in production, a ?fields=lai,sm query parameter can be added
    to filter columns — see docs/fastapi_architecture.md Section 7.

    Database persistence note:
        simulation_id is populated when the SimulationRun record and all
        DailyOutput rows are successfully written to the database after the
        simulation completes. It is None when:
          - The database session was not provided (unit tests / offline mode)
          - The DB write failed (error is logged; the simulation result is
            still returned so the client is never left without data)
        Use simulation_id with future endpoints:
          GET /simulate/{simulation_id}          → retrieve stored results
          GET /simulate/{simulation_id}/daily    → retrieve daily time series

    EnKF design note (Phase 3):
        The daily_states list will carry EnKF metadata (assimilated flag,
        ensemble spread) once assimilation is implemented. The response
        schema is backward-compatible — clients that ignore unknown fields
        will not break.
    """

    status: str = Field(description="'success' or 'error'")
    message: str = Field(description="Human-readable summary of the simulation result")

    # Database persistence identifier — populated when the run is saved to the DB.
    simulation_id: Optional[uuid.UUID] = Field(
        default=None,
        description=(
            "UUID of the SimulationRun record in the database. "
            "Use this to retrieve stored results via future GET endpoints. "
            "None if the database write was skipped or failed "
            "(the simulation result is still fully returned in this response)."
        ),
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )

    # Echo the request so callers don't need to track what they sent
    request: SimulateRequest = Field(
        description="Original request parameters (echoed for traceability)"
    )

    # Agronomic summary
    metrics: AgronomicMetrics = Field(
        description="Key agronomic performance metrics for the full season"
    )
    summary: Optional[PhenologicalSummary] = Field(
        default=None,
        description=(
            "Phenological summary (dates of emergence, anthesis, maturity). "
            "None if the crop did not complete its full cycle within the "
            "simulated period."
        ),
    )

    # Full daily time series — the primary deliverable
    daily_states: list[DailyState] = Field(
        description=(
            "Daily simulation output time series. One record per simulated day "
            "from campaign_start (sowing_date - 14 days) to harvest/maturity. "
            "Primary outputs per day: lai, sm, tagp, twso, rftra."
        ),
    )
