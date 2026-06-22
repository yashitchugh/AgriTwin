"""
assimilation/schemas/assimilation_visualization.py
===================================================

Pydantic v2 schemas for the read-only EnKF visualization API endpoints.
"""

import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel, Field


class CycleHistoryItem(BaseModel):
    """Represents a single completed EnKF cycle for audit/history view."""
    cycle_date: datetime.date = Field(
        ..., 
        description="Date of the assimilation cycle (Y-M-D)."
    )
    variables_updated: List[str] = Field(
        ..., 
        description="List of uppercase variable names updated in this cycle (e.g. ['LAI', 'SM'])."
    )
    observation_vector: Dict[str, Optional[float]] = Field(
        ..., 
        description="Observed variable values at this timestamp."
    )
    prior_state: Dict[str, Optional[float]] = Field(
        ..., 
        description="Prior state mean before filter update (forecast)."
    )
    posterior_state: Dict[str, Optional[float]] = Field(
        ..., 
        description="Posterior state mean after filter update (analysis)."
    )
    innovation: Dict[str, Optional[float]] = Field(
        ..., 
        description="Innovation vector (observation - prior)."
    )
    quality_score: Optional[float] = Field(
        None, 
        description="Average quality score [0-100] of valid observations used in this cycle."
    )
    cycle_number: int = Field(
        ..., 
        description="1-based sequence index of this cycle in the assimilation run."
    )


class TimeSeriesPoint(BaseModel):
    """Comparison point for a single date in a variable timeseries."""
    date: datetime.date = Field(..., description="Calendar date of output.")
    open_loop: Optional[float] = Field(
        None, 
        description="Baseline/open-loop model value for the variable."
    )
    assimilated: Optional[float] = Field(
        None, 
        description="Assimilated model value (with propagated updates) for the variable."
    )
    observation: Optional[float] = Field(
        None, 
        description="Valid observation value on this date, if any."
    )


class TimeSeriesResponse(BaseModel):
    """Complete comparative timeseries response for EnKF plotting."""
    LAI: List[TimeSeriesPoint] = Field(..., description="Timeseries data for Leaf Area Index.")
    SM: List[TimeSeriesPoint] = Field(..., description="Timeseries data for Soil Moisture.")
    TAGP: List[TimeSeriesPoint] = Field(..., description="Timeseries data for Total Above-ground Production.")
    TWSO: List[TimeSeriesPoint] = Field(..., description="Timeseries data for Total Weight Storage Organs.")
    RFTRA: List[TimeSeriesPoint] = Field(..., description="Timeseries data for Relative Transpiration factor.")


class YieldEvolutionPoint(BaseModel):
    """Yield projection point at a specific cycle."""
    date: datetime.date = Field(..., description="Cycle date of projection.")
    predicted_yield_kg_ha: Optional[float] = Field(
        None, 
        description="Current predicted crop yield (storage organ weight TWSO) in kg/ha."
    )
