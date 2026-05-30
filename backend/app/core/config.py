"""
core/config.py — Centralized Application Configuration
=======================================================

All tuneable parameters for the AgriTwin backend live here.
Settings are loaded from environment variables or a .env file in the project root.

Usage:
    from backend.app.core.config import settings

    print(settings.LOG_LEVEL)
    print(settings.CACHE_DIR)

Scalability notes:
    - When the database is added (Phase 2), add DATABASE_URL here.
    - When EnKF is added (Phase 3), add ENKF_ENSEMBLE_SIZE here.
    - When async task queues are added (Phase 4), add CELERY_BROKER_URL here.
    - All secrets (API keys, DB passwords) should be env vars, never hardcoded.
"""

import os
from pathlib import Path

# ── Project root resolution ──────────────────────────────────────────────────
# Resolves: backend/app/core/config.py → 3 levels up → project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings:
    """Runtime settings for the AgriTwin backend.

    Each setting has a documented purpose, unit, and default value.
    Add new settings here as the system grows — centralizing them avoids
    magic constants scattered across the codebase.
    """

    # ── Application metadata ─────────────────────────────────────────────
    APP_NAME: str = "AgriTwin API"
    APP_VERSION: str = "0.2.0"
    APP_DESCRIPTION: str = (
        "Agricultural Digital Twin Platform — Physics-based crop simulation "
        "using PCSE/WOFOST 7.2 with NASA POWER weather and SoilGrids soil data. "
        "Designed for future integration with Ensemble Kalman Filter (EnKF) "
        "data assimilation."
    )

    # ── Logging ──────────────────────────────────────────────────────────
    # Set to "DEBUG" for verbose PCSE internal logs; "INFO" for production.
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ── Cache directories ─────────────────────────────────────────────────
    # Weather and soil data are cached to avoid repeated API calls.
    # These directories are created at runtime if they don't exist.
    CACHE_DIR: str = os.getenv(
        "AGRITWIN_CACHE_DIR",
        str(_PROJECT_ROOT / "backend" / ".cache"),
    )

    @property
    def WEATHER_CACHE_DIR(self) -> str:
        """Subdirectory for NASA POWER weather cache files."""
        return os.path.join(self.CACHE_DIR, "weather")

    @property
    def SOIL_CACHE_DIR(self) -> str:
        """Subdirectory for SoilGrids soil cache files."""
        return os.path.join(self.CACHE_DIR, "soil")

    # ── PCSE / WOFOST paths ───────────────────────────────────────────────
    # Path to the WOFOST_crop_parameters repository (checked out as a git submodule).
    CROP_PARAM_DIR: str = os.getenv(
        "AGRITWIN_CROP_PARAM_DIR",
        str(_PROJECT_ROOT / "external_repos" / "WOFOST_crop_parameters"),
    )

    # ── Simulation defaults ───────────────────────────────────────────────
    # Maximum number of simulation days — guards against infinite loops when
    # the crop model fails to reach maturity (e.g. wrong variety/climate match).
    DEFAULT_MAX_DURATION: int = int(os.getenv("AGRITWIN_MAX_DURATION", "365"))

    # Initial available water in root zone [cm].
    # Used by WOFOST72SiteDataProvider (WAV parameter).
    # This will become a per-field parameter when the database is added.
    DEFAULT_WAV: float = float(os.getenv("AGRITWIN_WAV", "10.0"))

    # ── NASA POWER API ─────────────────────────────────────────────────────
    # Timeout for NASA POWER API requests (seconds).
    # The API is occasionally slow; 60s avoids false timeouts.
    WEATHER_API_TIMEOUT: int = int(os.getenv("AGRITWIN_WEATHER_TIMEOUT", "60"))

    # ── SoilGrids API ─────────────────────────────────────────────────────
    # Timeout for SoilGrids API requests (seconds).
    SOIL_API_TIMEOUT: int = int(os.getenv("AGRITWIN_SOIL_TIMEOUT", "30"))

    # ── Future: Database (Phase 2) ────────────────────────────────────────
    # DATABASE_URL: str = os.getenv(
    #     "DATABASE_URL",
    #     "postgresql://agritwin:password@localhost:5432/agritwin"
    # )

    # ── Future: EnKF (Phase 3) ────────────────────────────────────────────
    # Number of parallel ensemble members for the Ensemble Kalman Filter.
    # Standard recommendation: 20–100 members. More = more accurate but slower.
    # ENKF_ENSEMBLE_SIZE: int = int(os.getenv("ENKF_ENSEMBLE_SIZE", "50"))

    # ── CORS origins ──────────────────────────────────────────────────────
    # Restrict in production to known frontend origins.
    # e.g. ["https://agritwin.example.com", "http://localhost:3000"]
    CORS_ORIGINS: list[str] = ["*"]  # Allow all during development


# Singleton settings instance — import this everywhere
settings = Settings()
