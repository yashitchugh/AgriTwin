"""
scenario/generators/sowing_date_generator.py — SowingDateGenerator
===================================================================

Generates a ScenarioDefinition that sweeps sowing dates around a baseline.

Agronomic rationale:
    Sowing date is one of the most impactful management decisions for annual
    crops.  Sowing too early risks cold damage and poor emergence; too late
    misses the optimum photoperiod and thermal window for grain fill.  A
    deterministic grid of offsets (e.g. -30, -15, 0, +15, +30 days from the
    baseline date) samples the feasible planting window and identifies the
    date that maximises yield or minimises heat/cold stress.

    The baseline date (offset=0) is always included so the original simulation
    appears in the comparison as the "current practice" reference.

Generator design:
    - Offsets are integer day shifts relative to baseline_sowing_date.
    - Each offset maps to one ISO date string in parameter_values.
    - The resulting ScenarioDefinition.parameter_values list has one entry
      per offset in the offsets list.
    - Dates are clamped to [Jan 1, Dec 31] of the baseline year to prevent
      impossible dates (e.g. Feb 30 doesn't exist).

NOT implemented here:
    - Simulation execution
    - API routes
    - Ranking or comparison
    - Yield response curve fitting
"""

import datetime
import uuid
import logging
from typing import Optional

from backend.app.scenario.models.scenario_definition import ScenarioDefinition, GeneratorType

logger = logging.getLogger(__name__)

# Default offset grid (days relative to baseline sowing date).
# Covers ±30 days in 15-day steps — samples 5 points across a 2-month window.
# Agronomically meaningful for most winter cereals and Kharif rice.
DEFAULT_OFFSETS: list[int] = [-30, -15, 0, +15, +30]


class SowingDateGenerator:
    """Generate a ScenarioDefinition sweeping sowing dates around a baseline.

    Produces a deterministic grid of sowing dates by applying integer day
    offsets to a baseline sowing date.  The offset=0 point is always included
    so the baseline simulation is part of the comparison.

    Usage:
        gen = SowingDateGenerator()
        definition = gen.generate(
            baseline_sowing_date=datetime.date(2020, 10, 15),
            baseline_simulation_id=uuid.UUID("550e8400-..."),
        )
        # definition.parameter_values = [
        #   "2020-09-15", "2020-09-30", "2020-10-15", "2020-10-30", "2020-11-14"
        # ]

    Args:
        offsets: List of integer day offsets relative to baseline_sowing_date.
                 Default: [-30, -15, 0, +15, +30].
                 All offsets must be unique (duplicates removed with order preserved).
                 Must contain at least 2 elements after deduplication.
    """

    def __init__(self, offsets: Optional[list[int]] = None) -> None:
        # Deduplicate while preserving order (dict trick works in Python 3.7+)
        raw = offsets if offsets is not None else DEFAULT_OFFSETS
        self.offsets: list[int] = list(dict.fromkeys(raw))

        if len(self.offsets) < 2:
            raise ValueError(
                f"SowingDateGenerator requires at least 2 unique offsets, "
                f"got {len(self.offsets)} after deduplication."
            )

    def generate(
        self,
        baseline_sowing_date: datetime.date,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        baseline_simulation_id: Optional[uuid.UUID] = None,
    ) -> ScenarioDefinition:
        """Construct a ScenarioDefinition for a sowing-date sweep.

        Args:
            baseline_sowing_date:    The reference sowing date (offset=0 point).
                                     Must be a datetime.date.
            name:                    Optional human-readable scenario name.
                                     Auto-generated from baseline date if not provided.
            description:             Optional longer description.
                                     Auto-generated if not provided.
            baseline_simulation_id:  UUID of the existing SimulationRun that
                                     corresponds to baseline_sowing_date (offset=0).
                                     Used for delta comparison.  May be None for
                                     draft scenarios.

        Returns:
            ScenarioDefinition with:
                generator_type   = SOWING_DATE
                parameter_name   = "sowing_date"
                parameter_values = ISO date strings for each offset

        Raises:
            ValueError: If baseline_sowing_date + any offset produces an
                        invalid date (handled by clamping to year boundary).
        """
        candidate_dates = self._build_date_grid(baseline_sowing_date)

        auto_name = (
            name or
            f"Sowing date sweep ±{max(abs(o) for o in self.offsets)}d "
            f"around {baseline_sowing_date.isoformat()}"
        )
        auto_description = (
            description or
            f"Deterministic sowing date grid: {len(candidate_dates)} candidates "
            f"from {candidate_dates[0]} to {candidate_dates[-1]}. "
            f"Offsets from baseline ({baseline_sowing_date.isoformat()}): "
            f"{self.offsets} days. "
            f"Generated by SowingDateGenerator."
        )

        logger.info(
            "SowingDateGenerator: baseline=%s offsets=%s → %d candidates",
            baseline_sowing_date, self.offsets, len(candidate_dates),
        )

        return ScenarioDefinition(
            id=uuid.uuid4(),
            name=auto_name,
            description=auto_description,
            generator_type=GeneratorType.SOWING_DATE,
            parameter_name="sowing_date",
            parameter_values=candidate_dates,
            base_simulation_id=baseline_simulation_id,
        )

    def _build_date_grid(self, baseline: datetime.date) -> list[str]:
        """Apply each offset to the baseline date and return ISO strings.

        Clamping rules (edge cases):
          - Dates before Jan 1 of the baseline year → clamped to Jan 1.
          - Dates after Dec 31 of the baseline year → clamped to Dec 31.
          This prevents multi-year spanning which would change the growing
          season entirely (a different scenario type, not a date offset).

        Returns:
            Sorted list of unique ISO date strings ["YYYY-MM-DD", ...].
            Sorted ascending so the ScenarioDefinition reads chronologically.
        """
        year = baseline.year
        year_start = datetime.date(year, 1, 1)
        year_end   = datetime.date(year, 12, 31)

        dates: list[datetime.date] = []
        for offset in self.offsets:
            shifted = baseline + datetime.timedelta(days=offset)
            # Clamp to [Jan 1, Dec 31] of the baseline year
            shifted = max(year_start, min(year_end, shifted))
            dates.append(shifted)

        # Remove duplicates that arose from clamping, preserve sorted order
        seen: set[datetime.date] = set()
        unique_sorted: list[str] = []
        for d in sorted(set(dates)):
            if d not in seen:
                seen.add(d)
                unique_sorted.append(d.isoformat())

        return unique_sorted

    def __repr__(self) -> str:
        return f"<SowingDateGenerator offsets={self.offsets}>"
