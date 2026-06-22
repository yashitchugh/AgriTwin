"""
assimilation/schemas/assimilation_api.py — Pydantic Schemas for Assimilation API
================================================================================

Defines input request and output response schemas for the assimilation REST endpoints.
"""

import datetime
import uuid
from typing import Optional
from pydantic import BaseModel, Field


class AssimilationRunRequest(BaseModel):
    """Input request schema for starting an EnKF assimilation season run."""
    simulation_id: uuid.UUID = Field(
        ...,
        description="UUID of the baseline simulation run."
    )
    field_id: uuid.UUID = Field(
        ...,
        description="UUID of the field from which to load observations."
    )
    ensemble_size: int = Field(
        ...,
        ge=1,
        description="Number of ensemble members to create for the EnKF loop."
    )


class AssimilationRunStartResponse(BaseModel):
    """Output response schema returned when an EnKF assimilation run completes."""
    assimilation_run_id: uuid.UUID = Field(
        ...,
        description="UUID of the created AssimilationRun."
    )
    status: str = Field(
        ...,
        description="Lifecycle status of the assimilation run (COMPLETED or FAILED)."
    )
    executed_cycles: int = Field(
        ...,
        description="Number of assimilation cycles that were successfully executed."
    )
    observations_assimilated: int = Field(
        ...,
        description="Total count of observations assimilated during the run."
    )


class AssimilationStatusResponse(BaseModel):
    """Output response schema returned when querying assimilation run status."""
    assimilation_run_id: uuid.UUID = Field(
        ...,
        description="UUID of the latest assimilation run for the simulation."
    )
    latest_assimilation_run: uuid.UUID = Field(
        ...,
        description="Same as assimilation_run_id, stored for client compatibility."
    )
    status: str = Field(
        ...,
        description="Current lifecycle status of the run (PENDING, RUNNING, COMPLETED, FAILED)."
    )
    ensemble_size: int = Field(
        ...,
        description="Ensemble size used for the simulation."
    )
    total_cycles: int = Field(
        ...,
        description="Total number of potential cycles discovered for the run."
    )
    executed_cycles: int = Field(
        ...,
        description="Number of cycles executed."
    )
    skipped_cycles: int = Field(
        ...,
        description="Number of cycles skipped due to quality control / missing data."
    )
    latest_cycle_date: Optional[datetime.date] = Field(
        None,
        description="The date of the latest successfully assimilated cycle, if any."
    )
    observations_assimilated: int = Field(
        ...,
        description="Total count of observations assimilated."
    )
