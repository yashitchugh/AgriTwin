"""
scenario/generators/irrigation_generator.py — IrrigationGenerator
==================================================================

Generates a ScenarioDefinition that compares fixed irrigation schedule tiers.

Agronomic rationale:
    Water stress at critical crop development stages (tillering, stem extension,
    heading, grain fill) reduces yield more severely than stress during vegetative
    growth.  A common extension-service question is:

        "Is zero / two / four / six irrigations sufficient, or do I need more?"

    This generator encodes four canonical irrigation tiers as a deterministic
    sweep.  Each tier is a complete irrigation schedule (list of {date, amount_mm}
    events) expressed relative to the sowing date.  Spacing and timing follow
    the FAO Irrigation and Drainage guidelines for the specified crop type.

Tier definitions (relative to sowing_date):
    RAINFED   — no irrigation events (control)
    TWO_EVENT — 2 applications: at key vegetative + flowering stages
    FOUR_EVENT — 4 applications: evenly spaced across the growing season
    SIX_EVENT — 6 applications: dense irrigation covering all critical periods

Event timing is expressed as days-after-sowing (DAS) offsets and an
amount_mm per application.  The generator converts DAS to actual calendar
dates using the provided sowing_date.

NOT implemented here:
    - Simulation execution
    - API routes
    - Deficit-based irrigation scheduling (requires SM time series)
    - Crop-specific critical period detection
"""

import datetime
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.app.scenario.models.scenario_definition import ScenarioDefinition, GeneratorType

logger = logging.getLogger(__name__)


# ── Schedule tier definitions ─────────────────────────────────────────────────

@dataclass(frozen=True)
class IrrigationEvent:
    """A single irrigation event expressed relative to sowing date.

    Attributes:
        das:       Days After Sowing when this event is applied.
        amount_mm: Volume of water applied [mm].
    """
    das: int        # Days After Sowing
    amount_mm: float  # mm of water applied


@dataclass(frozen=True)
class IrrigationTier:
    """A named irrigation schedule tier.

    Attributes:
        label:       Short display label (e.g. "Rainfed", "2-Event").
        events:      Ordered list of IrrigationEvent objects.
        total_mm:    Pre-computed total irrigation volume [mm].
        description: Agronomic rationale for this tier.
    """
    label: str
    events: tuple[IrrigationEvent, ...]
    description: str

    @property
    def total_mm(self) -> float:
        """Total irrigation water applied across all events [mm]."""
        return sum(e.amount_mm for e in self.events)


# ── Default tier library ──────────────────────────────────────────────────────
# DAS timings are set for a generic 120–150 day cereal/rice season.
# They represent the following phenological windows:
#   DAS 30  — early vegetative (tiller initiation)
#   DAS 55  — active tillering / stem elongation
#   DAS 75  — booting / heading
#   DAS 90  — anthesis / flowering (most drought-sensitive stage)
#   DAS 105 — early grain fill
#   DAS 120 — mid grain fill
# The amount_mm per event (50 mm) is a common agronomic recommendation
# for heavy soils (SMFCF ≈ 0.30, root zone ≈ 30 cm).

DEFAULT_TIERS: tuple[IrrigationTier, ...] = (
    IrrigationTier(
        label="Rainfed",
        events=(),
        description=(
            "No irrigation applied — fully rainfed control. "
            "Establishes the yield penalty from water stress alone. "
            "Baseline for all irrigation scenarios."
        ),
    ),
    IrrigationTier(
        label="2-Event",
        events=(
            IrrigationEvent(das=55, amount_mm=50.0),   # tillering
            IrrigationEvent(das=90, amount_mm=50.0),   # anthesis
        ),
        description=(
            "Two irrigations targeting the two most drought-sensitive stages: "
            "active tillering (DAS 55) and anthesis (DAS 90). "
            "Represents minimum adequate irrigation for cereal crops. "
            "Total: 100 mm."
        ),
    ),
    IrrigationTier(
        label="4-Event",
        events=(
            IrrigationEvent(das=30,  amount_mm=50.0),  # early vegetative
            IrrigationEvent(das=55,  amount_mm=50.0),  # tillering
            IrrigationEvent(das=90,  amount_mm=50.0),  # anthesis
            IrrigationEvent(das=120, amount_mm=50.0),  # grain fill
        ),
        description=(
            "Four irrigations covering the full growing season: "
            "early vegetative (DAS 30), tillering (DAS 55), "
            "anthesis (DAS 90), and grain fill (DAS 120). "
            "Standard schedule for irrigated wheat / rice on loam soils. "
            "Total: 200 mm."
        ),
    ),
    IrrigationTier(
        label="6-Event",
        events=(
            IrrigationEvent(das=30,  amount_mm=50.0),  # early vegetative
            IrrigationEvent(das=55,  amount_mm=50.0),  # tillering
            IrrigationEvent(das=75,  amount_mm=50.0),  # booting
            IrrigationEvent(das=90,  amount_mm=50.0),  # anthesis
            IrrigationEvent(das=105, amount_mm=50.0),  # early grain fill
            IrrigationEvent(das=120, amount_mm=50.0),  # mid grain fill
        ),
        description=(
            "Six irrigations covering all critical growth stages. "
            "Dense schedule for high-input irrigated systems (Punjab, delta rice). "
            "Typically eliminates water stress throughout the season "
            "(expected RFTRA ≈ 1.0 on all days). "
            "Total: 300 mm."
        ),
    ),
)


class IrrigationGenerator:
    """Generate a ScenarioDefinition comparing fixed irrigation schedule tiers.

    Each tier (Rainfed, 2-Event, 4-Event, 6-Event) becomes one parameter_value
    in the ScenarioDefinition.  The parameter_value format follows the
    IrrigationEvent API schema:
        {"events": [{"date": "YYYY-MM-DD", "amount_mm": 50.0}, ...]}

    Usage:
        gen = IrrigationGenerator()
        definition = gen.generate(
            sowing_date=datetime.date(2020, 11, 15),
            baseline_simulation_id=uuid.UUID("..."),
        )
        # definition.parameter_values[0] = {"events": []}          (Rainfed)
        # definition.parameter_values[1] = {"events": [{...}, {...}]}  (2-Event)

    Args:
        tiers: Optional custom tier sequence.  Defaults to the 4-tier standard
               library (Rainfed, 2-Event, 4-Event, 6-Event).
               Must contain at least 2 tiers.
    """

    def __init__(self, tiers: Optional[tuple[IrrigationTier, ...]] = None) -> None:
        self.tiers: tuple[IrrigationTier, ...] = tiers if tiers is not None else DEFAULT_TIERS
        if len(self.tiers) < 2:
            raise ValueError(
                f"IrrigationGenerator requires at least 2 tiers, "
                f"got {len(self.tiers)}."
            )

    def generate(
        self,
        sowing_date: datetime.date,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        baseline_simulation_id: Optional[uuid.UUID] = None,
    ) -> ScenarioDefinition:
        """Construct a ScenarioDefinition for the irrigation tier sweep.

        Args:
            sowing_date:             Sowing date for the season being analysed.
                                     Used to convert DAS offsets to calendar dates.
            name:                    Optional scenario name. Auto-generated if omitted.
            description:             Optional description. Auto-generated if omitted.
            baseline_simulation_id:  UUID of the reference SimulationRun.
                                     Typically the rainfed or current-practice run.

        Returns:
            ScenarioDefinition with:
                generator_type   = IRRIGATION
                parameter_name   = "irrigation_events"
                parameter_values = list of {"events": [...]} dicts,
                                   one per tier, with calendar dates

        Example parameter_values element (2-Event tier, sowing 2020-11-15):
            {
              "tier_label": "2-Event",
              "total_mm":   100.0,
              "events": [
                {"date": "2021-01-09", "amount_mm": 50.0},   # DAS 55
                {"date": "2021-02-13", "amount_mm": 50.0},   # DAS 90
              ]
            }
        """
        parameter_values = [
            self._tier_to_dict(tier, sowing_date)
            for tier in self.tiers
        ]

        tier_labels = [t.label for t in self.tiers]
        total_mms = [t.total_mm for t in self.tiers]

        auto_name = (
            name or
            f"Irrigation tier sweep ({' / '.join(tier_labels)}) "
            f"sowing {sowing_date.isoformat()}"
        )
        auto_description = (
            description or
            f"Comparing {len(self.tiers)} irrigation schedules: "
            f"{', '.join(f'{lbl} ({mm:.0f} mm)' for lbl, mm in zip(tier_labels, total_mms))}. "
            f"Sowing date: {sowing_date.isoformat()}. "
            f"Generated by IrrigationGenerator."
        )

        logger.info(
            "IrrigationGenerator: sowing=%s tiers=%s",
            sowing_date, tier_labels,
        )

        return ScenarioDefinition(
            id=uuid.uuid4(),
            name=auto_name,
            description=auto_description,
            generator_type=GeneratorType.IRRIGATION,
            parameter_name="irrigation_events",
            parameter_values=parameter_values,
            base_simulation_id=baseline_simulation_id,
        )

    def _tier_to_dict(
        self,
        tier: IrrigationTier,
        sowing_date: datetime.date,
    ) -> dict:
        """Convert an IrrigationTier to the API-compatible dict format.

        Converts DAS offsets to actual calendar dates using sowing_date.
        Adds tier_label and total_mm as metadata keys for display purposes.

        Args:
            tier:        The IrrigationTier to convert.
            sowing_date: Reference date for DAS-to-calendar conversion.

        Returns:
            Dict with keys:
                tier_label  — display name (e.g. "4-Event")
                total_mm    — total water applied (e.g. 200.0)
                events      — list of {"date": str, "amount_mm": float} dicts
        """
        events = [
            {
                "date": (sowing_date + datetime.timedelta(days=event.das)).isoformat(),
                "amount_mm": event.amount_mm,
            }
            for event in tier.events
        ]
        return {
            "tier_label": tier.label,
            "total_mm": tier.total_mm,
            "events": events,
        }

    def __repr__(self) -> str:
        labels = [t.label for t in self.tiers]
        return f"<IrrigationGenerator tiers={labels}>"
