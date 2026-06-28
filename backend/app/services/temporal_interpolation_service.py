import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter
from datetime import datetime, date, timedelta
from typing import List, Tuple
import logging
from backend.app.api.schemas.interpolation import InterpolationRequest, InterpolationResponse

logger = logging.getLogger(__name__)

class TemporalInterpolationService:
    
    @staticmethod
    def _dates_to_numeric(dates: List[date]) -> np.ndarray:
        """Convert date objects to numeric days for math operations."""
        base_date = dates[0]
        return np.array([(d - base_date).days for d in dates])

    @staticmethod
    def _check_large_gaps(dates: List[date], max_gap: int) -> List[dict]:
        """Implements the Cloud-Gap Trigger. Flags gaps longer than max_gap."""
        flags = []
        for i in range(len(dates) - 1):
            gap = (dates[i+1] - dates[i]).days
            if gap > max_gap:
                flags.append({
                    "start_date": dates[i],
                    "end_date": dates[i+1],
                    "gap_days": gap,
                    "risk": "high",
                    "action": "Skipping interpolation; holding open-loop for this window."
                })
        return flags

    def interpolate(self, request: InterpolationRequest) -> InterpolationResponse:
        # 1. Check for dangerous cloud gaps FIRST
        gap_flags = self._check_large_gaps(request.observation_dates, request.max_allowed_gap_days)
        if gap_flags:
            logger.warning(f"Large gaps detected: {gap_flags}. Interpolation may be unreliable here.")
            # For research: We still interpolate, but we mark the quality flags.

        # 2. Convert dates to numeric X for math
        obs_days = self._dates_to_numeric(request.observation_dates)
        target_days = self._dates_to_numeric(request.target_dates)

        # 3. Handle edge case: Not enough points
        if len(obs_days) < 2:
            return InterpolationResponse(
                interpolated_dates=request.target_dates,
                interpolated_values=[None] * len(request.target_dates),
                quality_flags=[{"error": "Not enough observations to interpolate"}],
                method_used="none",
                message="Need at least 2 satellite observations."
            )

        # 4. Select Method
        if request.method == "linear":
            # Simple linear interpolation
            interpolated_vals = np.interp(target_days, obs_days, request.observation_values)
            method_name = "Linear"

        elif request.method == "cubic_spline":
            # Cubic Spline (smooth curve)
            cs = CubicSpline(obs_days, request.observation_values, bc_type='natural')
            interpolated_vals = cs(target_days)
            method_name = "Cubic Spline"

        elif request.method == "savgol":
            # Savitzky-Golay: First interpolate linearly to daily, then smooth the noise
            linear_interp = np.interp(target_days, obs_days, request.observation_values)
            # Window size must be odd and less than len(target_days)
            window = min(7, len(target_days) if len(target_days) % 2 == 1 else len(target_days) - 1)
            if window < 5:
                window = 5
            interpolated_vals = savgol_filter(linear_interp, window_length=window, polyorder=3)
            method_name = "Savitzky-Golay"
        else:
            raise ValueError("Unsupported method")

        # 5. Clip values to physically realistic bounds (LAI cannot be negative)
        interpolated_vals = np.clip(interpolated_vals, 0, 8)  # Max LAI ~8

        # 6. Build Quality Flags for each target day
        quality_flags = []
        for i, d in enumerate(request.target_dates):
            if d in request.observation_dates:
                quality_flags.append({"date": d, "type": "satellite_observation", "is_interpolated": False})
            else:
                quality_flags.append({"date": d, "type": "interpolated", "is_interpolated": True})
        
        # Add the cloud-gap warnings to the response
        quality_flags.extend(gap_flags)

        return InterpolationResponse(
            interpolated_dates=request.target_dates,
            interpolated_values=list(interpolated_vals),
            quality_flags=quality_flags,
            method_used=method_name,
            message=f"Interpolation complete. Gap flags: {len(gap_flags)} large gaps detected."
        )