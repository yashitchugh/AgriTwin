"""
assimilation/schemas/assimilation.py — Pydantic Schemas for Assimilation
========================================================================

Defines response and creation schemas for AssimilationRun and AssimilationState.
"""

import datetime
import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field


class AssimilationStateResponse(BaseModel):
    """Pydantic schema representing AssimilationState details."""
    id: uuid.UUID
    field_id: Optional[uuid.UUID] = None
    simulation_run_id: Optional[uuid.UUID] = None
    assimilation_run_id: Optional[uuid.UUID] = None
    assimilation_time: datetime.datetime
    ensemble_mean: dict[str, Any]
    ensemble_covariance: dict[str, Any]
    observation_vector: dict[str, Any]
    innovation_vector: dict[str, Any]
    kalman_gain: dict[str, Any]
    updated_state_vector: dict[str, Any]
    forecast_state_vector: dict[str, Any]
    number_of_members: int
    observation_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class AssimilationRunCreate(BaseModel):
    """Schema for creating a new AssimilationRun."""
    simulation_id: uuid.UUID
    ensemble_size: int
    config_json: Optional[dict[str, Any]] = None


class AssimilationRunResponse(BaseModel):
    """Schema representing an AssimilationRun response."""
    id: uuid.UUID
    simulation_id: uuid.UUID
    started_at: datetime.datetime
    completed_at: Optional[datetime.datetime] = None
    status: str
    ensemble_size: int
    total_cycles: int
    executed_cycles: int
    skipped_cycles: int
    observations_used: int
    config_json: Optional[dict[str, Any]] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}
