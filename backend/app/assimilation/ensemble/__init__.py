"""
backend/app/assimilation/ensemble/__init__.py
=============================================

Ensemble module for the AgriTwin Data Assimilation framework.
Contains the EnsembleMember and EnsembleManager for running 
multiple perturbed WOFOST simulations in parallel.
"""

from .ensemble_member import EnsembleMember
from .ensemble_manager import EnsembleManager

__all__ = ["EnsembleMember", "EnsembleManager"]
