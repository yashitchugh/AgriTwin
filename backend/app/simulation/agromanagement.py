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

Irrigation via TimedEvents (verified: docs/agromanagement_guide.md Section 4):
  - event_signal must be exactly "irrigate" (not "irrigation")
  - Each events_table entry: {YYYY-MM-DD: {amount: <mm>, efficiency: <0-1>}}
  - `amount`     — water applied in mm (NOT cm; PCSE reads mm directly)
  - `efficiency` — fraction of applied water that reaches the root zone
                   (accounts for evaporation and surface runoff losses)
                   Default 0.7 = 70% application efficiency, typical for
                   surface/sprinkler irrigation in semi-arid conditions.
  - PCSE silently ignores events outside the campaign window — dates are
    validated upstream in the schema layer (IrrigationEvent validator).

How PCSE processes irrigation signals:
  1. The AgroManager reads the events_table on each simulation day.
  2. When an event's date matches the current simulation day, PCSE fires
     the "irrigate" signal to the WaterbalanceFD sub-model.
  3. WaterbalanceFD adds (amount × efficiency) mm to root-zone water content.
  4. This raises SM (soil moisture) and consequently RFTRA (transpiration
     reduction factor) toward 1.0 — indicating reduced water stress.

Transplanted rice (DVSI > 0) and the TSUMEM division-by-zero guard:
  All IRRI rice varieties in WOFOST_crop_parameters (Rice_IR64, Rice_IR72,
  Rice_IR8A, etc.) have DVSI > 0 because they represent transplanted seedlings
  that are set in the field already past the emergence stage. When the
  PCSE phenology model runs with crop_start_type="sowing", it enters the
  "emerging" stage and computes:

      r.DVR = 0.1 * r.DTSUME / p.TSUMEM   [phenology.py line 387]

  For transplanted varieties TSUMEM=0 (not applicable) → ZeroDivisionError.
  The fix is to use crop_start_type="emergence" whenever DVSI > 0, which
  causes PCSE to skip the "emerging" stage and start directly at DVS=DVSI.
  The function `get_crop_start_type()` implements this detection.
"""

import logging
import datetime as dt
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


# Crops that are always transplanted (DVSI > 0 in their standard parameters).
# These must always use crop_start_type="emergence" to avoid TSUMEM=0 crash.
# Key: crop_name (lowercase), value: minimum DVSI threshold for transplanted mode.
_TRANSPLANTED_CROPS = {
    "rice": 0.0,   # All IRRI varieties have DVSI >= 0.16
}


def get_crop_start_type(crop_name: str, cropdata=None) -> str:
    """Determine the correct crop_start_type for PCSE AgroManagement.

    For transplanted crops (DVSI > 0), PCSE must be told to start at
    "emergence" — meaning the crop is placed in the field as a seedling
    at DVS=DVSI. Using "sowing" for these crops causes PCSE to enter the
    "emerging" phenology stage and divide by TSUMEM=0 (ZeroDivisionError).

    For direct-seeded crops (DVSI = 0, e.g. wheat, maize) "sowing" is correct.

    Args:
        crop_name:  Lowercase crop name (e.g. "rice", "wheat").
        cropdata:   Optional loaded YAMLCropDataProvider (already set_active_crop'd).
                    If provided, DVSI is read directly for accurate detection.
                    If None, crop_name is used to look up the known transplanted list.

    Returns:
        "emergence" for transplanted crops, "sowing" for direct-seeded crops.
    """
    crop_lower = crop_name.lower()

    # If we have live crop data, check DVSI directly (most accurate)
    if cropdata is not None:
        try:
            dvsi = cropdata.get("DVSI") or 0.0
            if dvsi > 0.0:
                logger.info(
                    "Crop %s has DVSI=%.3f > 0 — using crop_start_type='emergence' "
                    "(transplanted mode, avoids TSUMEM=0 ZeroDivisionError)",
                    crop_name, dvsi,
                )
                return "emergence"
        except Exception:
            pass  # Fall through to name-based detection

    # Name-based fallback: known transplanted crops
    if crop_lower in _TRANSPLANTED_CROPS:
        logger.info(
            "Crop '%s' is a known transplanted crop — using crop_start_type='emergence'",
            crop_name,
        )
        return "emergence"

    return "sowing"

# Default buffer: campaign starts this many days before sowing
_CAMPAIGN_BUFFER_DAYS = 14

# Default irrigation application efficiency (fraction reaching root zone).
# 0.7 = 70% efficiency — standard for surface/sprinkler irrigation.
# Drip irrigation would use higher values (0.85–0.95).
_DEFAULT_IRRIGATION_EFFICIENCY = 0.7


def build_agromanagement(
    crop_name: str,
    variety_name: str,
    sow_date: dt.date,
    harvest_date: dt.date,
    campaign_start_date: Optional[dt.date] = None,
    crop_start_type: str = "sowing",
    crop_end_type: str = "harvest",
    max_duration: int = 300,
    irrigation_events: Optional[list] = None,
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
        irrigation_events:   Optional list of dicts, each with keys:
                               "date"       (datetime.date or ISO string)
                               "amount_mm"  (float, water applied in mm)
                             Converted to PCSE TimedEvents with event_signal="irrigate".
                             Pass None or [] for a rainfed simulation.

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

    # ── Build TimedEvents block for irrigation ────────────────────────────────
    # PCSE expects TimedEvents as a Python list (or None for no events).
    # Each entry in events_table is a dict: {date_str: {amount: mm, efficiency: f}}
    #
    # How this maps to the PCSE signal:
    #   event_signal: "irrigate"   — the exact signal name PCSE's AgroManager listens for
    #   name:         descriptive label (not used internally by PCSE)
    #   comment:      documentation string (not used internally by PCSE)
    #   events_table: list of {date: {amount, efficiency}} entries
    #
    # `amount` is in mm — PCSE's WaterbalanceFD reads it directly in mm.
    # Do NOT convert to cm; PCSE handles the unit internally.
    timed_events = _build_timed_irrigation_events(irrigation_events)

    # ── Assemble AgroManagement dict ─────────────────────────────────────────
    # We build as a Python dict then use yaml.dump + yaml.safe_load to get
    # the exact list structure PCSE expects. This is safer than f-string
    # YAML because date objects are handled correctly by PyYAML.
    agro_dict = {
        "AgroManagement": [
            {
                campaign_start_date: {
                    "CropCalendar": {
                        "crop_name": crop_name,
                        "variety_name": variety_name,
                        "crop_start_date": sow_date,
                        "crop_start_type": crop_start_type,
                        "crop_end_date": harvest_date,
                        "crop_end_type": crop_end_type,
                        "max_duration": max_duration,
                    },
                    "TimedEvents": timed_events,
                    "StateEvents": None,
                }
            }
        ]
    }

    # Round-trip through YAML to get the native Python types PCSE expects.
    # PyYAML serializes datetime.date objects as YYYY-MM-DD strings, and
    # yaml.safe_load converts them back to datetime.date — matching PCSE's
    # internal date handling exactly.
    agro_yaml_str = yaml.dump(agro_dict, default_flow_style=False)
    agro_list = yaml.safe_load(agro_yaml_str)["AgroManagement"]

    n_events = len(irrigation_events) if irrigation_events else 0
    logger.info(
        "AgroManagement: %s/%s, sow=%s, harvest=%s, max_dur=%d, irrigation_events=%d",
        crop_name, variety_name, sow_date, harvest_date, max_duration, n_events,
    )
    return agro_list


def _build_timed_irrigation_events(irrigation_events: Optional[list]) -> Optional[list]:
    """Build the PCSE TimedEvents list for irrigation.

    Args:
        irrigation_events: list of dicts with "date" and "amount_mm" keys,
                           or None / empty list for a rainfed simulation.

    Returns:
        PCSE-compatible TimedEvents list, or None if no irrigation events.

    PCSE TimedEvents structure (verified: agromanagement_guide.md Section 4):
        [
          {
            "event_signal": "irrigate",        # exact signal name — do NOT change
            "name": "Timed irrigation",         # descriptive label
            "comment": "irrigation amounts in mm",
            "events_table": [
              {datetime.date(YYYY, MM, DD): {"amount": <mm>, "efficiency": 0.7}},
              ...
            ]
          }
        ]

    Notes on `amount` vs `amount_mm`:
      - The API schema uses `amount_mm` as the field name for clarity.
      - PCSE's AgroManagement YAML uses `amount` (no unit suffix).
      - This function translates `amount_mm` → `amount` when building the dict.
      - The value is always in mm — no unit conversion is needed.
    """
    if not irrigation_events:
        # No irrigation: return None so PCSE sets TimedEvents to null.
        # This matches the rainfed baseline behaviour.
        return None

    events_table = []
    for ev in irrigation_events:
        # Normalize date: accept both datetime.date objects and ISO strings.
        # PyYAML will serialize datetime.date to YYYY-MM-DD during dump.
        if isinstance(ev.get("date"), str):
            event_date = dt.date.fromisoformat(ev["date"])
        else:
            event_date = ev["date"]

        # `amount` is the PCSE field name (mm). `efficiency` defaults to 0.7.
        # irrigation_amount (from ev["amount_mm"]) is passed as-is — no conversion.
        events_table.append({
            event_date: {
                "amount": ev["amount_mm"],
                "efficiency": ev.get("efficiency", _DEFAULT_IRRIGATION_EFFICIENCY),
            }
        })

    return [
        {
            # "irrigate" is the exact PCSE signal name.
            # The AgroManager dispatches this to WaterbalanceFD.
            "event_signal": "irrigate",
            "name": "Timed irrigation",
            "comment": "irrigation amounts in mm",
            "events_table": events_table,
        }
    ]
