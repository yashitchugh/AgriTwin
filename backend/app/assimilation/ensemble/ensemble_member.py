"""
backend/app/assimilation/ensemble/ensemble_member.py
====================================================

Represents a single WOFOST ensemble member for Data Assimilation (EnKF).
Each member holds its own instance of the PCSE engine and perturbed parameters.
"""

import datetime as dt
from dataclasses import dataclass

from pcse.models import Wofost72_WLP_FD

from backend.app.assimilation.state.state_vector import StateVector
from backend.app.simulation.output_parser import extract_daily_state


@dataclass
class EnsembleMember:
    """A single WOFOST ensemble member.
    
    Attributes:
        member_id: Unique integer identifying this member within the ensemble.
        wofost: The running PCSE engine instance for this member.
        perturbed_parameters: Dictionary of parameters that were perturbed 
                              for this member (e.g., SLATB, SPAN, etc.)
    """
    member_id: int
    wofost: Wofost72_WLP_FD
    perturbed_parameters: dict[str, float]

    @property
    def current_date(self) -> dt.date:
        """The current simulation date of this ensemble member."""
        return self.wofost.day

    @property
    def current_state(self) -> StateVector:
        """The extracted StateVector representation of the current WOFOST state."""
        state_dict = extract_daily_state(self.wofost, self.wofost.day)
        
        # StateVector expects date as datetime.date, but extract_daily_state returns an ISO string.
        # We parse it back here.
        kwargs = {}
        for k, v in state_dict.items():
            if k == "date":
                kwargs[k] = dt.date.fromisoformat(v) if isinstance(v, str) else v
            elif hasattr(StateVector, k):
                kwargs[k] = v
                
        return StateVector(**kwargs)
