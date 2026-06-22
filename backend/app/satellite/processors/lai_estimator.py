# backend/app/satellite/processors/lai_estimator.py

import math
from abc import ABC, abstractmethod

class LAIEstimator(ABC):
    """Abstract base class for Leaf Area Index (LAI) estimators."""

    @abstractmethod
    def estimate_lai(self, index_value: float, index_name: str) -> float:
        """Estimate LAI from a vegetation index value.
        
        Args:
            index_value: The value of the vegetation index.
            index_name: The name of the vegetation index (e.g., 'NDVI', 'OSAVI', 'SeLI').
            
        Returns:
            The estimated Leaf Area Index (LAI).
        """
        pass

class EmpiricalLAIEstimator(LAIEstimator):
    """Empirical regression-based LAI estimator.
    
    Supports standard indices (NDVI, OSAVI, SeLI) with customizable parameters
    and physical clipping limits.
    """

    def __init__(self, min_lai: float = 0.0, max_lai: float = 8.0) -> None:
        self.min_lai = min_lai
        self.max_lai = max_lai

    def estimate_lai(self, index_value: float, index_name: str) -> float:
        """Estimate LAI based on empirical equations.
        
        Handles NaNs, clips output to physical limits, and validates input index.
        """
        if index_value is None or math.isnan(index_value):
            return float('nan')

        name_upper = index_name.upper()
        
        if name_upper == "NDVI":
            # LAI = 0.1 * exp(4.0 * NDVI)
            raw_lai = 0.1 * math.exp(4.0 * index_value)
        elif name_upper == "OSAVI":
            # LAI = 0.15 * exp(3.5 * OSAVI)
            raw_lai = 0.15 * math.exp(3.5 * index_value)
        elif name_upper == "SELI":
            # LAI = 5.0 * SeLI - 0.5
            raw_lai = 5.0 * index_value - 0.5
        else:
            raise ValueError(f"Unsupported vegetation index: '{index_name}'. Supported: NDVI, OSAVI, SeLI")

        # Clip to physical limits
        return max(self.min_lai, min(self.max_lai, raw_lai))
