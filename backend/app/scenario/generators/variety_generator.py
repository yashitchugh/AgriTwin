"""
scenario/generators/variety_generator.py — VarietyGenerator
============================================================

Generates a ScenarioDefinition that tests every available variety for a crop.

Agronomic rationale:
    Different WOFOST varieties for the same crop have different thermal time
    requirements (TSUMEM, TSUM1, TSUM2), different maximum LAI (LAIEM, SPAN),
    and different harvest index parameters (CVO, CVL, CVS).  Comparing all
    registered varieties under identical weather, soil, and management conditions
    identifies which genetic material is best suited to a given environment —
    the core of a variety selection analysis.

    This generator fetches the full list of varieties from the local
    WOFOST_crop_parameters YAML database and creates one candidate per variety,
    excluding the baseline variety (which appears via base_simulation_id).

    If exclude_baseline=True (default), the baseline variety is removed from
    parameter_values so it does not execute twice (the baseline run covers it).

Design:
    The generator calls list_available_crops() to retrieve all registered
    varieties.  This function reads local YAML files and does NOT call any
    network API.  Results are deterministic given the same YAML database.

NOT implemented here:
    - Simulation execution
    - API routes
    - Variety trait filtering (e.g. show only short-season varieties)
    - Machine learning variety recommendation
"""

import uuid
import logging
from typing import Optional

from backend.app.scenario.models.scenario_definition import ScenarioDefinition, GeneratorType
from backend.app.simulation.crop_provider import list_available_crops

logger = logging.getLogger(__name__)


class VarietyGenerator:
    """Generate a ScenarioDefinition sweeping all available varieties for a crop.

    Queries the local WOFOST YAML parameter database for the given crop and
    builds one parameter_value (variety string) per registered variety.

    Usage:
        gen = VarietyGenerator()
        definition = gen.generate(
            crop="wheat",
            baseline_variety="apache",
            baseline_simulation_id=uuid.UUID("..."),
        )
        # definition.parameter_values = ["Winter_wheat_101", "Hereward", ...]
        # (excludes "apache" because that is the baseline)

    Args:
        crop_param_dir: Optional path override for the WOFOST YAML crop
                        parameters directory.  Passed to list_available_crops().
                        Defaults to external_repos/WOFOST_crop_parameters/.
        exclude_baseline: If True (default), the baseline_variety is excluded
                          from parameter_values to avoid duplicating the baseline
                          run.  Set to False to include all varieties including
                          the baseline (useful when no baseline_simulation_id
                          is provided).
    """

    def __init__(
        self,
        crop_param_dir: Optional[str] = None,
        exclude_baseline: bool = True,
    ) -> None:
        self.crop_param_dir = crop_param_dir
        self.exclude_baseline = exclude_baseline

    def generate(
        self,
        crop: str,
        baseline_variety: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        baseline_simulation_id: Optional[uuid.UUID] = None,
    ) -> ScenarioDefinition:
        """Construct a ScenarioDefinition for a variety sweep.

        Fetches all registered varieties for `crop` from the local YAML
        database, optionally excludes `baseline_variety`, and builds a
        ScenarioDefinition whose parameter_values is the resulting list.

        Args:
            crop:                    PCSE crop name (e.g. "wheat", "rice", "maize").
                                     Must match a key in the WOFOST YAML database.
            baseline_variety:        The variety used in the baseline simulation.
                                     Excluded from parameter_values when
                                     exclude_baseline=True (default).
            name:                    Optional scenario name. Auto-generated if omitted.
            description:             Optional description. Auto-generated if omitted.
            baseline_simulation_id:  UUID of the baseline SimulationRun.
                                     May be None for draft scenarios.

        Returns:
            ScenarioDefinition with:
                generator_type   = VARIETY
                parameter_name   = "variety"
                parameter_values = sorted list of variety strings

        Raises:
            KeyError:   If `crop` is not found in the WOFOST YAML database.
            ValueError: If fewer than 2 candidate varieties remain after
                        excluding the baseline (cannot form a comparison).
        """
        varieties = self._fetch_varieties(crop)

        if self.exclude_baseline and baseline_variety in varieties:
            varieties = [v for v in varieties if v != baseline_variety]
            logger.debug(
                "VarietyGenerator: excluded baseline variety %r → %d remaining",
                baseline_variety, len(varieties),
            )

        if len(varieties) < 2:
            raise ValueError(
                f"VarietyGenerator: fewer than 2 candidate varieties for crop "
                f"'{crop}' after excluding baseline '{baseline_variety}'. "
                f"Cannot form a comparison scenario. "
                f"Set exclude_baseline=False to include the baseline variety."
            )

        auto_name = (
            name or
            f"Variety sweep — {crop} ({len(varieties)} varieties, "
            f"baseline: {baseline_variety})"
        )
        auto_description = (
            description or
            f"Comparing {len(varieties)} registered WOFOST varieties for crop '{crop}'. "
            f"Baseline variety: '{baseline_variety}' "
            f"({'excluded from' if self.exclude_baseline else 'included in'} "
            f"parameter_values). "
            f"Varieties: {', '.join(varieties[:5])}"
            f"{f' ... (+{len(varieties) - 5} more)' if len(varieties) > 5 else ''}. "
            f"Generated by VarietyGenerator."
        )

        logger.info(
            "VarietyGenerator: crop=%r baseline=%r candidates=%d varieties=%s",
            crop, baseline_variety, len(varieties), varieties,
        )

        return ScenarioDefinition(
            id=uuid.uuid4(),
            name=auto_name,
            description=auto_description,
            generator_type=GeneratorType.VARIETY,
            parameter_name="variety",
            parameter_values=varieties,
            base_simulation_id=baseline_simulation_id,
        )

    def _fetch_varieties(self, crop: str) -> list[str]:
        """Load all registered variety names for `crop` from the YAML database.

        Args:
            crop: PCSE crop name (lowercase, e.g. "wheat").

        Returns:
            Sorted list of variety name strings.

        Raises:
            KeyError: If `crop` is not in the WOFOST YAML database.
        """
        all_crops = list_available_crops(self.crop_param_dir)

        if crop not in all_crops:
            available = sorted(all_crops.keys())
            raise KeyError(
                f"Crop '{crop}' not found in WOFOST YAML database. "
                f"Available crops: {available}"
            )

        # Return sorted list for deterministic ordering across runs
        varieties = sorted(all_crops[crop])
        logger.debug(
            "VarietyGenerator: found %d varieties for %r: %s",
            len(varieties), crop, varieties,
        )
        return varieties

    def list_varieties(self, crop: str) -> list[str]:
        """Utility: return all available varieties for a crop without generating a scenario.

        Useful for populating API dropdowns or validating user input.

        Args:
            crop: PCSE crop name.

        Returns:
            Sorted list of variety name strings.
        """
        return self._fetch_varieties(crop)

    def __repr__(self) -> str:
        return (
            f"<VarietyGenerator "
            f"exclude_baseline={self.exclude_baseline} "
            f"crop_param_dir={self.crop_param_dir!r}>"
        )
