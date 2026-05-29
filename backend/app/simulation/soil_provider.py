"""
soil_provider.py — Soil Parameter Builder
===========================================

Builds the soil parameter dict required by PCSE's WaterbalanceFD.
PCSE has no dedicated soil data provider for real soils — soil data is
passed as a plain dict with 8 required keys.

PCSE Soil API (verified against input/soildataproviders.py):
  - DummySoilDataProvider._defaults defines the 8 required keys
  - Keys: SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB
  - Physical constraint: SMW < SMFCF < SM0 (wilting < field capacity < saturation)
  - RDMSOL belongs in soil dict, NOT in sitedata (WOFOST72SiteDataProvider rejects it)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Default soil parameters for a generic medium-texture soil.
# Values match DummySoilDataProvider._defaults (verified: soildataproviders.py lines 10-17)
# with SM0 adjusted to 0.45 for a more realistic loam soil.
DEFAULT_SOIL_PARAMS: dict[str, float] = {
    "SMFCF":  0.30,    # Soil moisture at field capacity        [cm³/cm³]
    "SMW":    0.10,    # Soil moisture at wilting point          [cm³/cm³]
    "SM0":    0.45,    # Soil moisture at saturation (porosity)  [cm³/cm³]
    "CRAIRC": 0.06,    # Critical air content for aeration       [cm³/cm³]
    "RDMSOL": 120.0,   # Maximum rootable soil depth             [cm]
    "K0":     10.0,    # Hydraulic conductivity at saturation    [cm/day]
    "SOPE":   10.0,    # Maximum percolation rate (root zone)    [cm/day]
    "KSUB":   10.0,    # Maximum percolation rate (subsoil)      [cm/day]
}


def create_soil_params(
    smfcf: Optional[float] = None,
    smw: Optional[float] = None,
    sm0: Optional[float] = None,
    crairc: Optional[float] = None,
    rdmsol: Optional[float] = None,
    k0: Optional[float] = None,
    sope: Optional[float] = None,
    ksub: Optional[float] = None,
) -> dict[str, float]:
    """Create a validated soil parameter dict for PCSE.

    Any parameter left as None will use the default value. The function
    validates the physical ordering constraint SMW < SMFCF < SM0.

    Args:
        smfcf:  Field capacity              [cm³/cm³]
        smw:    Wilting point                [cm³/cm³]
        sm0:    Saturation                   [cm³/cm³]
        crairc: Critical air content         [cm³/cm³]
        rdmsol: Max rootable soil depth      [cm]
        k0:     Hydraulic conductivity       [cm/day]
        sope:   Max percolation (root zone)  [cm/day]
        ksub:   Max percolation (subsoil)    [cm/day]

    Returns:
        dict with keys SMFCF, SMW, SM0, CRAIRC, RDMSOL, K0, SOPE, KSUB

    Raises:
        ValueError: If SMW >= SMFCF or SMFCF >= SM0
    """
    soil = {
        "SMFCF":  smfcf  if smfcf  is not None else DEFAULT_SOIL_PARAMS["SMFCF"],
        "SMW":    smw    if smw    is not None else DEFAULT_SOIL_PARAMS["SMW"],
        "SM0":    sm0    if sm0    is not None else DEFAULT_SOIL_PARAMS["SM0"],
        "CRAIRC": crairc if crairc is not None else DEFAULT_SOIL_PARAMS["CRAIRC"],
        "RDMSOL": rdmsol if rdmsol is not None else DEFAULT_SOIL_PARAMS["RDMSOL"],
        "K0":     k0     if k0     is not None else DEFAULT_SOIL_PARAMS["K0"],
        "SOPE":   sope   if sope   is not None else DEFAULT_SOIL_PARAMS["SOPE"],
        "KSUB":   ksub   if ksub   is not None else DEFAULT_SOIL_PARAMS["KSUB"],
    }

    # Physical constraint: wilting point < field capacity < saturation
    # WOFOST will crash or produce nonsense if this ordering is violated
    _validate_soil_moisture_ordering(soil)

    logger.info(
        "Soil params: SMW=%.3f, SMFCF=%.3f, SM0=%.3f, RDMSOL=%.0f cm",
        soil["SMW"], soil["SMFCF"], soil["SM0"], soil["RDMSOL"],
    )
    return soil


def _validate_soil_moisture_ordering(soil: dict[str, float]) -> None:
    """Ensure SMW < SMFCF < SM0.

    This is a fundamental physical constraint:
      - SMW (wilting point): minimum plant-available water
      - SMFCF (field capacity): water held against gravity
      - SM0 (saturation): all pore space filled

    Violating this causes the WaterbalanceFD to produce invalid results
    or crash with cryptic errors.
    """
    smw = soil["SMW"]
    smfcf = soil["SMFCF"]
    sm0 = soil["SM0"]

    if not (smw < smfcf < sm0):
        raise ValueError(
            f"Soil moisture ordering violated: "
            f"SMW({smw:.3f}) < SMFCF({smfcf:.3f}) < SM0({sm0:.3f}) must hold. "
            f"This is a physical constraint (wilting < field capacity < saturation)."
        )
