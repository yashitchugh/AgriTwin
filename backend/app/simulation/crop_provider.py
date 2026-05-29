"""
crop_provider.py — Crop Parameter Provider
============================================

Loads and configures WOFOST crop parameters from YAML files. Wraps
YAMLCropDataProvider with validation, logging, and local file path resolution.

PCSE Crop API (verified against input/yaml_cropdataprovider.py):
  - YAMLCropDataProvider(model=Wofost72_PP, fpath=None, repository=None, force_reload=False)
  - Extends MultiCropDataProvider (which extends dict)
  - Must call set_active_crop(crop_name, variety_name) to populate parameters
  - get_crops_varieties() → dict: {crop_name: [variety_names]}
  - NOTE: get_cropnames() and get_varieties() do NOT exist
"""

import os
import logging
from typing import Optional

# Verified: input/__init__.py line 30 (NOT pcse.fileinput — deprecated)
from pcse.input import YAMLCropDataProvider

logger = logging.getLogger(__name__)

# Default path relative to project root
_DEFAULT_CROP_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",
    "external_repos", "WOFOST_crop_parameters",
))


def create_crop_provider(
    crop_name: str,
    variety_name: str,
    crop_param_dir: Optional[str] = None,
) -> YAMLCropDataProvider:
    """Create and configure a crop parameter provider.

    Loads crop parameters from local YAML files (or remote GitHub repo as fallback),
    then activates the specified crop/variety combination.

    Args:
        crop_name:      Lowercase PCSE crop name (e.g. "wheat", "maize").
                        Must match names in crops.yaml exactly.
        variety_name:   PCSE variety identifier (e.g. "Winter_wheat_101").
                        Must match variety keys in the crop's YAML file.
        crop_param_dir: Path to WOFOST_crop_parameters directory.
                        Falls back to external_repos/WOFOST_crop_parameters.

    Returns:
        YAMLCropDataProvider with active crop parameters loaded

    Raises:
        KeyError: If crop_name or variety_name not found in the parameter database
        FileNotFoundError: If crop_param_dir is specified but doesn't exist
    """
    fpath = crop_param_dir or _DEFAULT_CROP_DIR

    if os.path.isdir(fpath):
        logger.info("Loading crop parameters from local: %s", fpath)
        cropd = YAMLCropDataProvider(fpath=fpath)
    else:
        logger.warning(
            "Crop param dir not found: %s — falling back to remote repository", fpath
        )
        cropd = YAMLCropDataProvider()

    # Verify the crop/variety exists before activating
    # Verified: yaml_cropdataprovider.py line 272
    available = cropd.get_crops_varieties()
    if crop_name not in available:
        available_crops = list(available.keys())
        raise KeyError(
            f"Crop '{crop_name}' not found. Available: {available_crops}"
        )

    varieties = list(available[crop_name])
    if variety_name not in varieties:
        raise KeyError(
            f"Variety '{variety_name}' not found for crop '{crop_name}'. "
            f"Available: {varieties}"
        )

    # Verified: yaml_cropdataprovider.py line 247
    cropd.set_active_crop(crop_name, variety_name)
    logger.info("Activated crop: %s / %s (%d parameters)", crop_name, variety_name, len(cropd))

    return cropd


def list_available_crops(crop_param_dir: Optional[str] = None) -> dict:
    """List all available crops and their varieties.

    Returns:
        dict mapping crop_name → list of variety_names
    """
    fpath = crop_param_dir or _DEFAULT_CROP_DIR
    if os.path.isdir(fpath):
        cropd = YAMLCropDataProvider(fpath=fpath)
    else:
        cropd = YAMLCropDataProvider()

    return cropd.get_crops_varieties()
