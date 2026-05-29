"""
site_provider.py — Site Parameter Builder
==========================================

Creates validated site parameters using WOFOST72SiteDataProvider.

PCSE Site API (verified: input/sitedataproviders.py lines 55-81):
  - WAV is the ONLY required parameter
  - RDMSOL does NOT go here — it belongs in soildata
"""

import logging

from pcse.input import WOFOST72SiteDataProvider  # Verified: input/__init__.py line 33

logger = logging.getLogger(__name__)


def create_site_params(
    wav: float = 10.0,
    ifunrn: int = 0,
    notinf: float = 0.0,
    ssi: float = 0.0,
    ssmax: float = 0.0,
    smlim: float = 0.4,
) -> WOFOST72SiteDataProvider:
    """Create validated site parameters for WOFOST 7.2.

    Args:
        wav:     Initial available soil water [cm]. Range: 0-100.
        ifunrn:  Non-infiltrating rainfall flag (0 or 1).
        notinf:  Max fraction not infiltrating [0-1].
        ssi:     Initial surface water storage [cm].
        ssmax:   Max surface water storage [cm].
        smlim:   Initial soil moisture limit [cm³/cm³].

    Returns:
        WOFOST72SiteDataProvider with validated site parameters
    """
    sitedata = WOFOST72SiteDataProvider(
        WAV=wav, IFUNRN=ifunrn, NOTINF=notinf,
        SSI=ssi, SSMAX=ssmax, SMLIM=smlim,
    )
    logger.info("Site params: WAV=%.1f cm, SMLIM=%.2f", wav, smlim)
    return sitedata
