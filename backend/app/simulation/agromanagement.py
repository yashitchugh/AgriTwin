"""
agromanagement.py — AgroManagement Builder
============================================

Builds PCSE-compatible AgroManagement lists programmatically.

PCSE AgroManagement API (verified: input/yaml_agro_loader.py lines 6-29):
  - YAMLAgroManagementReader(list) reads YAML, extracts ['AgroManagement']
  - Engine expects a Python LIST, not the full dict
  - campaign_start_date must be <= crop_start_date
  - crop_name must be lowercase, matching PCSE database exactly
  - crop_start_type: "sowing" | "emergence"
  - crop_end_type: "harvest" | "maturity" | "earliest"
"""

import logging
import datetime as dt
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Default buffer: campaign starts this many days before sowing
_CAMPAIGN_BUFFER_DAYS = 14


def build_agromanagement(
    crop_name: str,
    variety_name: str,
    sow_date: dt.date,
    harvest_date: dt.date,
    campaign_start_date: Optional[dt.date] = None,
    crop_start_type: str = "sowing",
    crop_end_type: str = "harvest",
    max_duration: int = 300,
) -> list:
    """Build a PCSE-compatible AgroManagement list.

    The returned list can be passed directly to the WOFOST Engine constructor
    as the agromanagement argument.

    Args:
        crop_name:           Lowercase crop (e.g. "wheat", "maize")
        variety_name:        Variety key (e.g. "Winter_wheat_101")
        sow_date:            Sowing date
        harvest_date:        Expected harvest date
        campaign_start_date: Campaign start (defaults to sow_date - 14 days).
                             Must be <= sow_date.
        crop_start_type:     "sowing" or "emergence"
        crop_end_type:       "harvest", "maturity", or "earliest"
        max_duration:        Maximum crop duration in days (prevents infinite loops)

    Returns:
        list suitable for passing to Wofost72_WLP_FD(params, wdp, agro_list)

    Raises:
        ValueError: If dates are inconsistent
    """
    # Default campaign start: 14 days before sowing
    if campaign_start_date is None:
        campaign_start_date = sow_date - dt.timedelta(days=_CAMPAIGN_BUFFER_DAYS)

    # Validate date ordering
    if campaign_start_date > sow_date:
        raise ValueError(
            f"campaign_start_date ({campaign_start_date}) must be <= "
            f"sow_date ({sow_date})"
        )
    if sow_date >= harvest_date:
        raise ValueError(
            f"sow_date ({sow_date}) must be < harvest_date ({harvest_date})"
        )

    # Validate crop_start_type and crop_end_type
    valid_start_types = {"sowing", "emergence"}
    valid_end_types = {"harvest", "maturity", "earliest"}
    if crop_start_type not in valid_start_types:
        raise ValueError(f"crop_start_type must be one of {valid_start_types}")
    if crop_end_type not in valid_end_types:
        raise ValueError(f"crop_end_type must be one of {valid_end_types}")

    # Build the AgroManagement structure as a Python dict, then extract
    # the list component that PCSE expects.
    agro_yaml = f"""
AgroManagement:
- {campaign_start_date}:
    CropCalendar:
      crop_name: {crop_name}
      variety_name: {variety_name}
      crop_start_date: {sow_date}
      crop_start_type: {crop_start_type}
      crop_end_date: {harvest_date}
      crop_end_type: {crop_end_type}
      max_duration: {max_duration}
    TimedEvents: null
    StateEvents: null
"""
    agro_list = yaml.safe_load(agro_yaml)["AgroManagement"]

    logger.info(
        "AgroManagement: %s/%s, sow=%s, harvest=%s, max_dur=%d",
        crop_name, variety_name, sow_date, harvest_date, max_duration,
    )
    return agro_list
