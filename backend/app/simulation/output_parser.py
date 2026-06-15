"""
output_parser.py — Simulation Output Extraction & Parsing
==========================================================

Extracts and normalizes WOFOST simulation outputs for downstream use
(API responses, EnKF assimilation, database storage).

PCSE Output API (verified against engine.py):
  - get_output() → list[dict]  (engine.py line 424-431)
      Daily records with keys from OUTPUT_VARS + 'day'
  - get_summary_output() → list[dict]  (engine.py line 433-437)
      Summary at crop finish (LAIMAX, DOE, DOA, DOM, etc.)
  - get_variable(varname) → float | None  (base/engine.py line 67-97)
      Live state access via VariableKiosk, case-insensitive fallback
"""

import logging
import datetime as dt
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tracked variables ──────────────────────────────────────────────────
# Maps PCSE uppercase keys → lowercase output field names.
# These are the variables defined in Wofost72_WLP_CWB.conf OUTPUT_VARS
# plus additional useful ones accessible via get_variable().
#
# Irrigation diagnostic pair — the two most informative variables for
# assessing whether irrigation improved crop water status:
#
#   SM (Volumetric soil moisture [cm³/cm³]):
#     Computed daily by WaterbalanceFD. After an irrigation event fires,
#     SM should rise by approximately (amount × efficiency) / (RD × 10) cm³/cm³.
#     SM is bounded between SMW (permanent wilting point) and SM0 (saturation).
#     Watching SM over time reveals how quickly the crop depletes each
#     irrigation application and when the next one is needed.
#
#   RFTRA (Relative water stress factor for transpiration [-]):
#     RFTRA = Actual Transpiration (TRA) / Potential Transpiration (TRAMX).
#     Range: 0.0 (maximum stress — crop cannot transpire at all) to
#            1.0 (no stress — crop transpires at full potential).
#     Computed by WaterbalanceFD as: RFTRA = TRA / TRAMX.
#     RFTRA < 1.0 on a given day means the crop was water-stressed:
#       - Photosynthesis and biomass accumulation (TAGP) are reduced
#       - Grain filling (TWSO) is reduced proportionally
#       - Yield loss accumulates over stressed days
#     After a successful irrigation event, RFTRA should rise back toward 1.0
#     within 1–3 days as SM recovers into the non-stressed zone (SM > SMCR).
#     Comparing RFTRA between irrigated and rainfed runs quantifies the
#     irrigation benefit: more days at RFTRA=1.0 → higher yield potential.
#
#   LAI (Leaf Area Index [m²/m²]):
#     Irrigation prevents early leaf senescence under stress. Irrigated crops
#     maintain higher LAI during grain filling → more photosynthate to TWSO.
#
#   TAGP (Total Above-Ground Production [kg/ha]):
#     Monotonically increases; reflects integrated carbon gain. Irrigation
#     benefit shows as higher TAGP trajectory relative to rainfed baseline.
#
#   TWSO (Total Weight of Storage Organs [kg/ha]):
#     The yield variable. Begins accumulating at anthesis (DVS ≥ 1.0).
#     Irrigation during the grain-filling phase (DVS 1.0–2.0) is most
#     effective at improving TWSO. This is the primary optimization target.
TRACKED_VARIABLES: dict[str, str] = {
    # ── In OUTPUT_VARS (always present in batch mode) ────────────────────
    "DVS":   "dvs",     # Development stage [-]
    "LAI":   "lai",     # Leaf Area Index [m²/m²]
    "SM":    "sm",      # Volumetric soil moisture [cm³/cm³]
    "TAGP":  "tagp",    # Total above-ground production [kg/ha dry matter]
    "TWSO":  "twso",    # Total weight of storage organs (yield) [kg/ha]
    "TWLV":  "twlv",    # Total weight of leaves [kg/ha]
    "TWST":  "twst",    # Total weight of stems [kg/ha]
    "TWRT":  "twrt",    # Total weight of roots [kg/ha]
    "TRA":   "tra",     # Actual crop transpiration [cm/day]
    "RD":    "rd",      # Rooting depth [cm]
    "RFTRA": "rftra",   # Transpiration reduction factor [0–1]

    # ── Live-state variables (NOT in OUTPUT_VARS) ────────────────────────
    # Available via get_variable() in step-by-step mode ONLY.
    # These will be None in current batch-mode (run_till_terminate) runs.
    # Columns are stored nullable so existing records are fully compatible.
    # When step_by_step=True is enabled for EnKF in Phase 3, these will be
    # populated via extract_daily_state() on every wofost.run(days=1) call.
    #
    # WLV / WST / WRT / WSO: instantaneous organ weights [kg/ha] at the
    # current timestep BEFORE senescence is applied. Distinct from TWLV/TWST
    # /TWRT/TWSO which are cumulative total weights.
    "WLV":   "wlv",     # Actual leaf weight [kg/ha] (pre-senescence daily value)
    "WST":   "wst",     # Actual stem weight [kg/ha]
    "WRT":   "wrt",     # Actual root weight [kg/ha]
    "WSO":   "wso",     # Actual storage organ weight [kg/ha]
    #
    # EVS: soil evaporation [cm/day].
    # Available as a live-state variable but not always in OUTPUT_VARS.
    "EVS":   "evs",     # Actual soil evaporation [cm/day]
}


def extract_daily_state(wofost, current_date: dt.date) -> dict:
    """Extract current state variables from a running WOFOST instance.

    Designed for step-by-step simulation (run(days=1) loop).
    Safe to call every day — returns None for variables not yet initialized
    (e.g. crop variables before sowing).

    This function is the correct extraction point for future EnKF integration:
    the returned dict can be directly used as the state vector.

    Args:
        wofost: Running Wofost72_WLP_FD instance
        current_date: Current simulation date

    Returns:
        dict with 'date' (ISO string) + lowercase variable names
    """
    state = {"date": current_date.isoformat()}

    for pcse_key, field_name in TRACKED_VARIABLES.items():
        # Verified: base/engine.py line 67-97
        # get_variable returns None if not found (does NOT raise)
        val = wofost.get_variable(pcse_key)
        state[field_name] = float(val) if val is not None else None

    return state


def parse_batch_output(output: list[dict]) -> list[dict]:
    """Parse the output from wofost.get_output() into normalized dicts.

    PCSE get_output() returns dicts with uppercase keys and a 'day' key
    (datetime.date). This function normalizes to lowercase field names
    and ISO date strings for API/database compatibility.

    Args:
        output: Raw output from wofost.get_output()

    Returns:
        list of normalized dicts with lowercase keys and ISO dates
    """
    parsed = []
    for record in output:
        row = {"date": record["day"].isoformat()}
        for pcse_key, field_name in TRACKED_VARIABLES.items():
            val = record.get(pcse_key)
            row[field_name] = float(val) if val is not None else None
        parsed.append(row)

    logger.info("Parsed %d daily output records", len(parsed))
    return parsed


def parse_summary_output(summary: list[dict]) -> Optional[dict]:
    """Parse the summary output from wofost.get_summary_output().

    Summary includes season-level aggregates like LAIMAX, phenological
    dates (DOE, DOA, DOM, DOH, DOV), and cumulative values.

    Args:
        summary: Raw output from wofost.get_summary_output()

    Returns:
        Normalized summary dict, or None if no summary available
    """
    if not summary:
        return None

    raw = summary[0]
    result = {}
    for key, value in raw.items():
        lk = key.lower()
        if value is None:
            result[lk] = None
        elif isinstance(value, dt.date):
            result[lk] = value.isoformat()
        elif isinstance(value, float):
            result[lk] = round(value, 4)
        else:
            result[lk] = value

    logger.info("Parsed summary output with %d fields", len(result))
    return result


def compute_harvest_metrics(output: list[dict]) -> dict:
    """Compute key agronomic metrics from simulation output.

    Args:
        output: Raw output from wofost.get_output()

    Returns:
        dict with peak_lai, final_yield, harvest_index, etc.
    """
    if not output:
        return {}

    peak_lai = max((r.get("LAI", 0) or 0) for r in output)
    final = output[-1]
    final_twso = final.get("TWSO", 0) or 0
    final_tagp = final.get("TAGP", 0) or 0
    final_dvs = final.get("DVS", 0) or 0
    hi = final_twso / final_tagp if final_tagp > 0 else 0.0

    return {
        "total_days": len(output),
        "peak_lai": round(peak_lai, 3),
        "final_dvs": round(final_dvs, 3),
        "final_tagp_kg_ha": round(final_tagp, 1),
        "final_twso_kg_ha": round(final_twso, 1),
        "harvest_index": round(hi, 3),
    }
