"""
backend/app/assimilation/models/__init__.py
============================================

ORM model registry for the assimilation package.

Importing this sub-package ensures Observation and ObservationBatch are
registered in Base.metadata before any create_all() or Alembic autogenerate call.
"""

from backend.app.assimilation.models.observation import Observation, ObservationSource, ObservationStatus  # noqa: F401
from backend.app.assimilation.models.observation_batch import ObservationBatch, BatchProcessingStatus  # noqa: F401

__all__ = [
    "Observation",
    "ObservationSource",
    "ObservationStatus",
    "ObservationBatch",
    "BatchProcessingStatus",
]
