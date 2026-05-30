"""
soil_service.py — SoilGrids Soil Data Ingestion Service
=========================================================

Fetches soil hydraulic properties from the ISRIC SoilGrids v2.0 REST API
and maps them to WOFOST-compatible soil parameters for Wofost72_WLP_FD.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOILGRIDS → WOFOST PARAMETER MAPPING
=====================================

SoilGrids provides volumetric water content at three matric potentials:

  ┌────────────┬──────────────────────────┬────────────┬───────────────────┐
  │ SoilGrids  │ Physical meaning         │ WOFOST key │ PCSE unit         │
  ├────────────┼──────────────────────────┼────────────┼───────────────────┤
  │ wv0010     │ Water at 10 kPa suction  │ SM0        │ cm³/cm³           │
  │            │ ≈ near-saturation        │ (porosity) │ (vol. fraction)   │
  ├────────────┼──────────────────────────┼────────────┼───────────────────┤
  │ wv0033     │ Water at 33 kPa suction  │ SMFCF      │ cm³/cm³           │
  │            │ ≈ field capacity         │            │                   │
  ├────────────┼──────────────────────────┼────────────┼───────────────────┤
  │ wv1500     │ Water at 1500 kPa suction│ SMW        │ cm³/cm³           │
  │            │ ≈ permanent wilting point │            │                   │
  └────────────┴──────────────────────────┴────────────┴───────────────────┘

SoilGrids raw values are INTEGERS with a d_factor divisor:
  - Water retention: raw value / d_factor → 10⁻² cm³/cm³ → ÷100 → cm³/cm³
  - Example: wv0033 raw=310, d_factor=10 → 31.0 vol% → 0.310 cm³/cm³

Physical constraint (MUST hold for WOFOST):
  SMW < SMFCF < SM0  (wilting point < field capacity < saturation)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SCIENTIFIC ASSUMPTIONS (MVP):
  1. Soil is vertically homogeneous (single layer, no horizons)
  2. Root zone is 0-30 cm depth (weighted average of 0-5, 5-15, 15-30 cm)
  3. wv0010 ≈ saturation (10 kPa is near-saturation; true saturation at 0 kPa
     is slightly higher but not provided by SoilGrids)
  4. wv0033 = field capacity (standard agronomic convention for most soils)
  5. wv1500 = permanent wilting point (standard, -15 bar)
  6. CRAIRC = SM0 - SMFCF (critical air content = porosity minus FC)
  7. K0, SOPE, KSUB use defaults (SoilGrids does not provide Ksat)
  8. RDMSOL defaults to 120 cm (typical for wheat on deep soils)
"""

import os
import json
import logging
import datetime as dt
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── SoilGrids API Configuration ────────────────────────────────────────

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# Properties to request:
#   wv0010 — volumetric water content at 10 kPa  → SM0 (near-saturation)
#   wv0033 — volumetric water content at 33 kPa  → SMFCF (field capacity)
#   wv1500 — volumetric water content at 1500 kPa → SMW (wilting point)
#   clay   — clay fraction (for pedotransfer fallback and diagnostics)
#   sand   — sand fraction (for pedotransfer fallback and diagnostics)
#   bdod   — bulk density (for porosity cross-check)
SOILGRIDS_PROPERTIES = ["wv0010", "wv0033", "wv1500", "clay", "sand", "bdod"]

# Depths to request and weight-average across (covers root zone)
# Weighted by layer thickness: 0-5=5cm, 5-15=10cm, 15-30=15cm → total 30cm
SOILGRIDS_DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]
DEPTH_WEIGHTS = {
    "0-5cm": 5.0,    # 5 cm thick
    "5-15cm": 10.0,  # 10 cm thick
    "15-30cm": 15.0,  # 15 cm thick
}
TOTAL_DEPTH_CM = sum(DEPTH_WEIGHTS.values())  # 30 cm

# Default cache directory
DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".agritwin_cache", "soil"
)

# ─── Default WOFOST hydraulic parameters ─────────────────────────────────
# Used when SoilGrids does not provide a value (K0, SOPE, KSUB)
# or as fallback when the API is unreachable.
# These are physically reasonable values for a medium-textured loam.

DEFAULT_K0 = 10.0      # Hydraulic conductivity at saturation [cm/day]
DEFAULT_SOPE = 10.0    # Maximum percolation rate, root zone [cm/day]
DEFAULT_KSUB = 10.0    # Maximum percolation rate, subsoil [cm/day]
DEFAULT_RDMSOL = 120.0 # Maximum rootable soil depth [cm]
DEFAULT_CRAIRC = 0.06  # Critical air content [cm³/cm³]


class SoilService:
    """Fetches soil properties from SoilGrids and maps to WOFOST parameters.

    Usage:
        svc = SoilService()
        soil_params = svc.get_soil_params(latitude=28.8, longitude=77.5)
        # Returns dict with: SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = os.path.abspath(cache_dir or DEFAULT_CACHE_DIR)

    def get_soil_params(
        self,
        latitude: float,
        longitude: float,
        rdmsol: float = DEFAULT_RDMSOL,
        force_update: bool = False,
    ) -> dict[str, float]:
        """Fetch soil properties and return WOFOST-compatible parameter dict.

        Args:
            latitude:     Site latitude (-90 to 90)
            longitude:    Site longitude (-180 to 180)
            rdmsol:       Maximum rootable depth [cm]. Defaults to 120 cm.
                          SoilGrids doesn't provide this — it depends on
                          soil depth, compaction layers, and crop root type.
            force_update: Bypass cache

        Returns:
            dict with keys: SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB
            All moisture values in cm³/cm³, validated SMW < SMFCF < SM0.

        Raises:
            ValueError: Invalid coordinates or unphysical soil values
            RuntimeError: SoilGrids API failure (with fallback to defaults)
        """
        if not (-90 <= latitude <= 90):
            raise ValueError(f"Latitude must be in [-90, 90], got {latitude}")
        if not (-180 <= longitude <= 180):
            raise ValueError(f"Longitude must be in [-180, 180], got {longitude}")

        logger.info(
            "Fetching soil parameters for (%.4f, %.4f)", latitude, longitude
        )

        # ── Try cache ────────────────────────────────────────────────────
        if not force_update:
            cached = self._load_from_cache(latitude, longitude)
            if cached is not None:
                # Re-apply rdmsol override (user might change it)
                cached["RDMSOL"] = rdmsol
                return cached

        # ── Fetch from SoilGrids ─────────────────────────────────────────
        try:
            raw = self._fetch_from_soilgrids(latitude, longitude)
        except (RuntimeError, requests.RequestException) as e:
            logger.warning(
                "SoilGrids fetch failed: %s — using default soil parameters", e
            )
            return self._build_defaults(rdmsol)

        # ── Extract and depth-average the raw values ─────────────────────
        try:
            averaged = self._depth_average(raw)
        except (KeyError, ValueError) as e:
            logger.warning(
                "SoilGrids data incomplete: %s — using defaults", e
            )
            return self._build_defaults(rdmsol)

        # ── Convert to WOFOST parameters ─────────────────────────────────
        soil_params = self._convert_to_wofost(averaged, rdmsol)

        # ── Validate physical constraints ────────────────────────────────
        soil_params = self._validate_and_fix(soil_params)

        # ── Cache ────────────────────────────────────────────────────────
        self._save_to_cache(raw, soil_params, latitude, longitude)

        logger.info(
            "Soil params: SMW=%.3f, SMFCF=%.3f, SM0=%.3f, RDMSOL=%.0f cm",
            soil_params["SMW"], soil_params["SMFCF"], soil_params["SM0"],
            soil_params["RDMSOL"],
        )
        return soil_params

    # ─── SoilGrids API ───────────────────────────────────────────────────

    def _fetch_from_soilgrids(
        self, latitude: float, longitude: float
    ) -> dict:
        """Query SoilGrids REST API.

        Endpoint: https://rest.isric.org/soilgrids/v2.0/properties/query
        Returns parsed JSON response.
        """
        params = {
            "lon": longitude,
            "lat": latitude,
            "property": SOILGRIDS_PROPERTIES,
            "depth": SOILGRIDS_DEPTHS,
            "value": "mean",
        }

        logger.info("Fetching from SoilGrids for (%.4f, %.4f)", latitude, longitude)

        try:
            response = requests.get(SOILGRIDS_URL, params=params, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"SoilGrids request failed: {e}") from e

        if response.status_code != 200:
            raise RuntimeError(
                f"SoilGrids returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        data = response.json()

        # Check for empty layers (location is masked: water, urban, ice)
        layers = data.get("properties", {}).get("layers", [])
        if not layers:
            raise RuntimeError(
                f"SoilGrids returned empty layers for ({latitude}, {longitude}). "
                f"This location may be water, urban, or outside coverage."
            )

        return data

    # ─── Data Processing ─────────────────────────────────────────────────

    def _depth_average(self, raw_data: dict) -> dict[str, float]:
        """Compute depth-weighted average of SoilGrids properties.

        Weights are proportional to layer thickness:
            0-5 cm (5 cm) + 5-15 cm (10 cm) + 15-30 cm (15 cm) = 30 cm

        Returns dict with keys like 'wv0010', 'wv0033', 'wv1500', 'clay', 'sand', 'bdod'
        as actual values (after d_factor conversion and depth-averaging).
        """
        result = {}

        for layer in raw_data["properties"]["layers"]:
            prop_name = layer["name"]
            d_factor = layer["unit_measure"]["d_factor"]

            weighted_sum = 0.0
            weight_sum = 0.0

            for depth_entry in layer["depths"]:
                label = depth_entry["label"]
                mean_raw = depth_entry["values"].get("mean")

                if mean_raw is None:
                    # This depth is masked (null) — skip it
                    logger.debug("Null value for %s at %s", prop_name, label)
                    continue

                if label not in DEPTH_WEIGHTS:
                    continue

                # Apply SoilGrids conversion:
                # raw_value / d_factor → value in target_units
                actual = mean_raw / d_factor
                weight = DEPTH_WEIGHTS[label]

                weighted_sum += actual * weight
                weight_sum += weight

            if weight_sum == 0:
                raise ValueError(f"No valid data for property '{prop_name}'")

            result[prop_name] = weighted_sum / weight_sum

        # Verify we have the 3 critical water retention properties
        for required in ["wv0010", "wv0033", "wv1500"]:
            if required not in result:
                raise KeyError(f"Missing required property: {required}")

        return result

    def _convert_to_wofost(
        self, averaged: dict[str, float], rdmsol: float
    ) -> dict[str, float]:
        """Convert depth-averaged SoilGrids values to WOFOST soil dict.

        UNIT CONVERSION (critical!):
        ─────────────────────────────
        SoilGrids water retention (wv0010, wv0033, wv1500):
          - After d_factor: values are in "10⁻² cm³/cm³" = vol%
          - Example: wv0033 = 31.0 means 31.0 vol% = 0.310 cm³/cm³
          - PCSE needs cm³/cm³ → divide by 100

        Implementation notes WARNING 6 reminds us:
          "actual_value = raw_integer × conversion_factor"
          "e.g., wv0033 raw=284, factor=0.1 → actual=28.4 → /100 = 0.284 cm³/cm³"
        """
        # ── Water retention → PCSE moisture fractions ────────────────────
        # wv values are in vol% after d_factor → divide by 100 for cm³/cm³
        sm0 = averaged["wv0010"] / 100.0    # Near-saturation → SM0
        smfcf = averaged["wv0033"] / 100.0  # Field capacity → SMFCF
        smw = averaged["wv1500"] / 100.0    # Wilting point → SMW

        # ── Critical air content ─────────────────────────────────────────
        # CRAIRC = air-filled porosity needed for root respiration
        # Approximation: SM0 - SMFCF (air space between saturation and FC)
        # Typical range: 0.04-0.10 cm³/cm³
        crairc = sm0 - smfcf
        # Clamp to reasonable range
        crairc = max(0.02, min(crairc, 0.15))

        # ── Hydraulic conductivity ───────────────────────────────────────
        # SoilGrids does NOT provide Ksat, SOPE, or KSUB.
        # These require lab measurements or complex pedotransfer functions
        # beyond MVP scope. Use defaults appropriate for medium soils.
        k0 = DEFAULT_K0
        sope = DEFAULT_SOPE
        ksub = DEFAULT_KSUB

        soil = {
            "SM0":    round(sm0, 4),
            "SMFCF":  round(smfcf, 4),
            "SMW":    round(smw, 4),
            "CRAIRC": round(crairc, 4),
            "RDMSOL": rdmsol,
            "K0":     k0,
            "SOPE":   sope,
            "KSUB":   ksub,
        }

        # Log diagnostic info if texture data available
        if "clay" in averaged and "sand" in averaged:
            logger.info(
                "Soil texture: clay=%.1f%%, sand=%.1f%% (silt≈%.1f%%)",
                averaged["clay"], averaged["sand"],
                100.0 - averaged["clay"] - averaged["sand"],
            )

        return soil

    def _validate_and_fix(self, soil: dict[str, float]) -> dict[str, float]:
        """Validate and fix the SMW < SMFCF < SM0 constraint.

        Sometimes SoilGrids data can produce borderline cases where
        wv0010 ≈ wv0033 (sandy soils drain quickly to FC). We apply
        minimum separation to ensure WOFOST doesn't crash.
        """
        smw = soil["SMW"]
        smfcf = soil["SMFCF"]
        sm0 = soil["SM0"]

        MIN_SEPARATION = 0.02  # Minimum 2 vol% between levels

        # Fix ordering if violated
        if smw >= smfcf:
            logger.warning(
                "SMW(%.3f) >= SMFCF(%.3f) — adjusting SMW downward", smw, smfcf
            )
            soil["SMW"] = smfcf - MIN_SEPARATION

        if soil["SMFCF"] >= sm0:
            logger.warning(
                "SMFCF(%.3f) >= SM0(%.3f) — adjusting SM0 upward", smfcf, sm0
            )
            soil["SM0"] = soil["SMFCF"] + MIN_SEPARATION

        # Final safety clamp: all values must be > 0
        for key in ["SMW", "SMFCF", "SM0"]:
            if soil[key] <= 0:
                logger.warning("%s=%.4f is non-positive — clamping to 0.01", key, soil[key])
                soil[key] = 0.01

        # Re-check after fixes
        if not (soil["SMW"] < soil["SMFCF"] < soil["SM0"]):
            logger.error(
                "Could not fix soil ordering: SMW=%.3f, SMFCF=%.3f, SM0=%.3f "
                "— using safe defaults",
                soil["SMW"], soil["SMFCF"], soil["SM0"],
            )
            soil["SMW"] = 0.10
            soil["SMFCF"] = 0.30
            soil["SM0"] = 0.45

        return soil

    def _build_defaults(self, rdmsol: float) -> dict[str, float]:
        """Return safe default soil parameters when SoilGrids is unavailable.

        Values represent a generic medium-textured loam soil.
        """
        logger.info("Using default soil parameters (medium loam)")
        return {
            "SMFCF": 0.30,
            "SMW": 0.10,
            "SM0": 0.45,
            "CRAIRC": DEFAULT_CRAIRC,
            "RDMSOL": rdmsol,
            "K0": DEFAULT_K0,
            "SOPE": DEFAULT_SOPE,
            "KSUB": DEFAULT_KSUB,
        }

    # ─── Caching ─────────────────────────────────────────────────────────

    def _get_cache_path(self, latitude: float, longitude: float) -> str:
        """Cache path keyed by lat/lon truncated to 0.01°."""
        lat_key = int(latitude * 100)
        lon_key = int(longitude * 100)
        fname = f"soil_LAT{lat_key:06d}_LON{lon_key:06d}.json"
        return os.path.join(self.cache_dir, fname)

    def _save_to_cache(
        self, raw_data: dict, soil_params: dict,
        latitude: float, longitude: float,
    ) -> None:
        """Save both raw SoilGrids response and derived WOFOST params."""
        cache_path = self._get_cache_path(latitude, longitude)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        cache_obj = {
            "latitude": latitude,
            "longitude": longitude,
            "fetched_at": dt.datetime.now().isoformat(),
            "wofost_params": soil_params,
            "soilgrids_raw": raw_data,
        }

        try:
            with open(cache_path, "w") as f:
                json.dump(cache_obj, f, indent=2)
            logger.info("Soil data cached to %s", cache_path)
        except (IOError, OSError) as e:
            logger.warning("Failed to write soil cache: %s", e)

    def _load_from_cache(
        self, latitude: float, longitude: float
    ) -> Optional[dict[str, float]]:
        """Load WOFOST params from cache. Returns None on miss."""
        cache_path = self._get_cache_path(latitude, longitude)

        if not os.path.exists(cache_path):
            return None

        try:
            with open(cache_path, "r") as f:
                cache_obj = json.load(f)
            logger.info("Loaded soil data from cache")
            return cache_obj["wofost_params"]
        except (json.JSONDecodeError, KeyError, IOError) as e:
            logger.warning("Soil cache corrupt: %s", e)
            return None
