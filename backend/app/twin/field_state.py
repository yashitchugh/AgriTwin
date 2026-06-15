"""
backend/app/twin/field_state.py — Digital Twin Field State
===========================================================

FieldState is the central abstraction for the AgriTwin Digital Twin layer.
It represents the **complete virtual state of a simulated agricultural field
at a single point in time**.

Scientific context:
    In a physics-based Digital Twin, a "state" is the minimal set of variables
    from which the model can be re-started and evolved forward in time.
    For WOFOST 7.2, the state vector includes:
      - Development stage (DVS) — drives phenological transitions
      - Leaf Area Index (LAI) — controls canopy radiation interception
      - Biomass pools (TWLV, TWST, TWRT, TWSO) — energy storage reservoirs
      - Soil moisture (SM) — controls water stress and transpiration
      - Root depth (RD) — determines soil volume for water extraction

    FieldState captures all of these plus secondary diagnostics (RFTRA, TRA, EVS)
    and the instantaneous pre-senescence biomass variables (WLV, WST, WRT, WSO)
    that are needed by ensemble assimilation algorithms.

Design principles (read before extending this class):
    1. NO business logic — FieldState is a container, not a controller.
    2. NO database access — repositories build FieldState from ORM objects;
       FieldState never queries the DB directly.
    3. ALL fields optional — a FieldState can represent a partial observation
       (e.g. only sm + lai from satellite) without a complete simulation state.
    4. Immutability — once constructed, a FieldState is not modified in place.
       EnKF should produce a NEW FieldState for each assimilation step.
    5. Factory methods are the canonical construction path. Do not call __init__
       directly in production code.

NOT implemented here:
    - EnKF (Ensemble Kalman Filter) — no ensemble, no perturbation, no analysis step
    - Satellite observation ingestion — no image reading or product downloading
    - Scenario engine — no what-if parameter changes
    - Optimization — no yield maximization, no irrigation scheduling
    - Machine learning — no model training or inference

Future extension points (documented here so they are easy to find):
    - FieldState.apply_enkf_update(analysis_vector) → FieldState
          Create a corrected state from an EnKF analysis vector.
    - FieldState.to_enkf_vector() → np.ndarray
          Serialize to numpy for ensemble arithmetic.
    - FieldState.with_observation(obs_dict) → FieldState
          Produce a new state with observed values injected.
"""

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular imports — DailyOutput and SimulationResult are imported
    # only for type checking, not at runtime.
    from backend.app.models.daily_output import DailyOutput
    from backend.app.simulation.engine import SimulationResult


@dataclass
class FieldState:
    """Virtual state of a simulated agricultural field at a single instant.

    This is the primary data structure that future AgriTwin modules will
    consume.  It isolates the assimilation / scenario layer from both the
    WOFOST engine and the database schema.

    Attributes:
        field_id:     UUID of the Field record this state belongs to.
                      None for ad-hoc simulations not linked to a persistent field.
        simulation_id: UUID of the SimulationRun that produced this state.
                       None if the state came from a direct observation or is synthetic.
        current_date: Calendar date this state represents.
        source:       Provenance tag. One of:
                          "simulation"   — derived from a WOFOST run
                          "daily_output" — loaded from a persisted DailyOutput row
                          "observation"  — future: from satellite / field sensor
                          "enkf"         — future: post-EnKF analysis state
        updated_at:   Timestamp when this FieldState was constructed (UTC).

    Core variables (always included when available):
        lai:   Leaf Area Index [m²/m²]
        sm:    Volumetric soil moisture [cm³/cm³]
        tagp:  Total above-ground production [kg/ha]
        twso:  Total weight of storage organs / yield [kg/ha]

    Development variables:
        dvs:   Development stage [-] (0=emergence, 1=anthesis, 2=maturity)
        rd:    Root depth [cm]

    Stress variables:
        rftra: Transpiration reduction factor [-] (1=no stress, 0=full stress)
        tra:   Actual transpiration [cm/day]
        evs:   Soil evaporation [cm/day]

    Biomass pools — cumulative totals (batch-mode, always available):
        twlv:  Total weight of leaves [kg/ha]
        twst:  Total weight of stems [kg/ha]
        twrt:  Total weight of roots [kg/ha]

    Biomass pools — live-state instantaneous (step-by-step mode only):
        wlv:   Actual leaf weight [kg/ha] (pre-senescence, today's value)
        wst:   Actual stem weight [kg/ha] (pre-senescence)
        wrt:   Actual root weight [kg/ha] (pre-senescence)
        wso:   Actual storage organ weight [kg/ha] (pre-senescence)
    """

    # ── Identity ──────────────────────────────────────────────────────────
    field_id: Optional[uuid.UUID] = field(default=None)
    simulation_id: Optional[uuid.UUID] = field(default=None)
    current_date: Optional[datetime.date] = field(default=None)

    # ── Core variables ────────────────────────────────────────────────────
    lai: Optional[float] = field(default=None)
    sm: Optional[float] = field(default=None)
    tagp: Optional[float] = field(default=None)
    twso: Optional[float] = field(default=None)

    # ── Development ───────────────────────────────────────────────────────
    dvs: Optional[float] = field(default=None)
    rd: Optional[float] = field(default=None)

    # ── Stress diagnostics ────────────────────────────────────────────────
    rftra: Optional[float] = field(default=None)
    tra: Optional[float] = field(default=None)
    evs: Optional[float] = field(default=None)

    # ── Biomass pools — cumulative totals (batch mode, always available) ──
    twlv: Optional[float] = field(default=None)
    twst: Optional[float] = field(default=None)
    twrt: Optional[float] = field(default=None)

    # ── Biomass pools — live-state (step-by-step / EnKF mode only) ────────
    # These are None in all current batch-mode simulations.
    # WLV/WST/WRT/WSO are WOFOST variables not included in OUTPUT_VARS.
    # They become available via get_variable() in step-by-step (run(days=1)) mode.
    wlv: Optional[float] = field(default=None)
    wst: Optional[float] = field(default=None)
    wrt: Optional[float] = field(default=None)
    wso: Optional[float] = field(default=None)

    # ── Metadata ──────────────────────────────────────────────────────────
    source: str = field(default="unknown")
    updated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    # ── Factory methods ───────────────────────────────────────────────────

    @classmethod
    def from_daily_output(
        cls,
        row: "DailyOutput",
        *,
        field_id: Optional[uuid.UUID] = None,
    ) -> "FieldState":
        """Construct a FieldState from a persisted DailyOutput ORM row.

        This is the standard way to rehydrate a FieldState from the database
        for display, analysis, or future assimilation.

        Args:
            row:      A DailyOutput ORM instance (loaded from daily_outputs table).
            field_id: Optional Field UUID to attach for identity context.
                      Derived from row.simulation_run.field_id when available,
                      but passing it here avoids a DB round-trip.

        Returns:
            FieldState populated from the database row.
            All nullable columns (wlv, wst, wrt, wso, evs) will be None for
            batch-mode runs and populated for future step-by-step runs.

        Example:
            rows = daily_repo.get_daily_outputs(sim_id)
            latest = FieldState.from_daily_output(rows[-1], field_id=field.id)
        """
        return cls(
            field_id=field_id,
            simulation_id=row.simulation_run_id,
            current_date=row.date,
            # Core
            lai=row.lai,
            sm=row.sm,
            tagp=row.tagp,
            twso=row.twso,
            # Development
            dvs=row.dvs,
            rd=row.rd,
            # Stress
            rftra=row.rftra,
            tra=row.tra,
            evs=row.evs,
            # Biomass pools — cumulative
            twlv=row.twlv,
            twst=row.twst,
            twrt=row.twrt,
            # Biomass pools — live-state (None in batch runs)
            wlv=row.wlv,
            wst=row.wst,
            wrt=row.wrt,
            wso=row.wso,
            source="daily_output",
        )

    @classmethod
    def from_simulation(
        cls,
        result: "SimulationResult",
        *,
        date: Optional[datetime.date] = None,
        field_id: Optional[uuid.UUID] = None,
        simulation_id: Optional[uuid.UUID] = None,
    ) -> "FieldState":
        """Construct a FieldState from a live SimulationResult object.

        Extracts state from the LAST simulated day of the result by default.
        Pass `date` to select a specific day from the daily_output series.

        This method is used:
          - Immediately after run_simulation_from_request() returns, before
            the results are persisted to the DB.
          - In future step-by-step mode to expose the current engine state
            to assimilation modules.

        Args:
            result:        SimulationResult from the WOFOST engine.
            date:          Target date (ISO string match). If None, uses last day.
            field_id:      Optional Field UUID for identity context.
            simulation_id: Optional SimulationRun UUID if already allocated.

        Returns:
            FieldState for the selected day.
            Live-state variables (wlv, wst, wrt, wso) will be None because
            parse_batch_output() does not include OUTPUT_VARS-excluded variables.

        Example:
            result = run_simulation(...)
            state = FieldState.from_simulation(result, field_id=field.id)
        """
        daily = result.daily_output
        if not daily:
            return cls(
                field_id=field_id,
                simulation_id=simulation_id,
                source="simulation",
            )

        if date is not None:
            date_str = date.isoformat() if isinstance(date, datetime.date) else date
            record = next(
                (r for r in daily if r["date"] == date_str),
                daily[-1],  # fall back to last day if date not found
            )
        else:
            record = daily[-1]

        return cls(
            field_id=field_id,
            simulation_id=simulation_id,
            current_date=datetime.date.fromisoformat(record["date"]),
            # Core
            lai=record.get("lai"),
            sm=record.get("sm"),
            tagp=record.get("tagp"),
            twso=record.get("twso"),
            # Development
            dvs=record.get("dvs"),
            rd=record.get("rd"),
            # Stress
            rftra=record.get("rftra"),
            tra=record.get("tra"),
            evs=record.get("evs"),
            # Biomass pools — cumulative (available in batch mode)
            twlv=record.get("twlv"),
            twst=record.get("twst"),
            twrt=record.get("twrt"),
            # Biomass pools — live-state (None in batch mode)
            wlv=record.get("wlv"),
            wst=record.get("wst"),
            wrt=record.get("wrt"),
            wso=record.get("wso"),
            source="simulation",
        )

    # ── Utility ───────────────────────────────────────────────────────────

    @property
    def is_water_stressed(self) -> bool:
        """True if the crop is experiencing water stress on this day.

        RFTRA < 1.0 means actual transpiration is below potential — the crop
        cannot fully transpire, which reduces photosynthesis and biomass accumulation.
        """
        return self.rftra is not None and self.rftra < 1.0

    @property
    def has_live_state(self) -> bool:
        """True if the instantaneous (pre-senescence) biomass variables are available.

        These are only populated in step-by-step (EnKF-compatible) mode.
        In batch mode (run_till_terminate), this is always False.
        """
        return any(v is not None for v in (self.wlv, self.wst, self.wrt, self.wso))

    def to_dict(self) -> dict:
        """Serialize to a plain dict for logging, API responses, or JSON export."""
        return {
            "field_id": str(self.field_id) if self.field_id else None,
            "simulation_id": str(self.simulation_id) if self.simulation_id else None,
            "current_date": self.current_date.isoformat() if self.current_date else None,
            "source": self.source,
            "updated_at": self.updated_at.isoformat(),
            # Core
            "lai": self.lai,
            "sm": self.sm,
            "tagp": self.tagp,
            "twso": self.twso,
            # Development
            "dvs": self.dvs,
            "rd": self.rd,
            # Stress
            "rftra": self.rftra,
            "tra": self.tra,
            "evs": self.evs,
            # Biomass cumulative
            "twlv": self.twlv,
            "twst": self.twst,
            "twrt": self.twrt,
            # Biomass live-state
            "wlv": self.wlv,
            "wst": self.wst,
            "wrt": self.wrt,
            "wso": self.wso,
        }

    def __repr__(self) -> str:
        dvs_s = f"{self.dvs:.3f}" if self.dvs is not None else "None"
        lai_s = f"{self.lai:.3f}" if self.lai is not None else "None"
        sm_s = f"{self.sm:.3f}" if self.sm is not None else "None"
        twso_s = f"{self.twso:.1f}" if self.twso is not None else "None"
        return (
            f"<FieldState "
            f"date={self.current_date} "
            f"dvs={dvs_s} "
            f"lai={lai_s} "
            f"sm={sm_s} "
            f"twso={twso_s} "
            f"source={self.source!r}>"
        )

