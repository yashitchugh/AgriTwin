import numpy as np
from datetime import date, timedelta
from typing import List, Dict, Tuple
from uuid import UUID
from sqlalchemy.orm import Session
import logging

from backend.app.models.daily_output import DailyOutput
from backend.app.models.simulation_run import SimulationRun
from backend.app.models.field import Field
from backend.app.api.schemas.error_correction import (
    ErrorCorrectionRequest, 
    ErrorCorrectionResponse, 
    DailyCorrectionRecord
)

logger = logging.getLogger(__name__)

class ErrorCorrectionService:
    
    def __init__(self, db_session: Session):
        self.db = db_session
        
    def _get_daily_outputs(self, simulation_id: UUID, start_date: date, end_date: date) -> Dict[date, dict]:
        """Fetch WOFOST daily outputs for the 7-day window."""
        outputs = self.db.query(DailyOutput).filter(
            DailyOutput.simulation_run_id == simulation_id,
            DailyOutput.date >= start_date,
            DailyOutput.date <= end_date
        ).order_by(DailyOutput.date).all()
        
        result = {}
        for out in outputs:
            result[out.date] = {
                "lai": out.lai,
                "sm": out.sm,
                "dvs": out.dvs,
                "tagp": out.tagp,
                "twso": out.twso,  # Yield
                "wlv": out.wlv,
                "wst": out.wst,
                "wrt": out.wrt,
                "wso": out.wso
            }
        return result
    
    def _get_interpolated_observations(self, field_id: UUID, start_date: date, end_date: date) -> Dict[date, float]:
        """
        Fetch your Phase 1 interpolated LAI values for the window.
        We fetch raw LAI observations for this field from the database, then run
        the TemporalInterpolationService to fill values daily across the target window.
        """
        from backend.app.assimilation.repositories.observation_repository import ObservationRepository
        from backend.app.api.schemas.interpolation import InterpolationRequest
        from backend.app.services.temporal_interpolation_service import TemporalInterpolationService
        
        # 1. Fetch raw LAI observations for the field
        obs_repo = ObservationRepository(self.db)
        observations = obs_repo.get_by_variable(variable_name="LAI", field_id=field_id)
        
        if len(observations) < 2:
            logger.warning(
                f"Fewer than 2 LAI observations found for field {field_id}. "
                f"Cannot interpolate values for the window {start_date} to {end_date}."
            )
            return {}
            
        # 2. Extract observation dates and values
        observation_dates = [obs.timestamp.date() for obs in observations]
        observation_values = [obs.value for obs in observations]
        
        # 3. Create target daily dates within the 7-day window
        target_dates = []
        curr = start_date
        while curr <= end_date:
            target_dates.append(curr)
            curr += timedelta(days=1)
            
        # 4. Interpolate over target dates using cubic spline
        req = InterpolationRequest(
            observation_dates=observation_dates,
            observation_values=observation_values,
            target_dates=target_dates,
            method="cubic_spline",
            max_allowed_gap_days=15  # generous gap tolerance for window logic
        )
        
        interpolation_service = TemporalInterpolationService()
        resp = interpolation_service.interpolate(req)
        
        # 5. Build daily mapping dictionary
        result = {}
        for d, val in zip(target_dates, resp.interpolated_values):
            if val is not None:
                result[d] = val
        return result
    
    def _compute_blending_weight(self, residual: float, threshold: float) -> float:
        """
        Compute a scalar Kalman Gain-like blending weight.
        - If residual is small (< threshold/2), trust the satellite more (W=0.8)
        - If residual is large (> threshold), trust the model more (W=0.2)
        - Smooth transition in between
        """
        if abs(residual) < threshold * 0.5:
            return 0.8  # Trust satellite
        elif abs(residual) > threshold:
            return 0.2  # Trust model
        else:
            # Linear transition
            normalized = (abs(residual) - threshold * 0.5) / (threshold * 0.5)
            return 0.8 - normalized * 0.6
    
    def _correct_anomaly(self, wofost_val: float, satellite_val: float, blending_weight: float) -> float:
        """Apply the blending correction."""
        # Corrected = (1 - W) * WOFOST + W * Satellite
        return (1 - blending_weight) * wofost_val + blending_weight * satellite_val
    
    def _update_database(self, simulation_id: UUID, date_obj: date, variable: str, corrected_value: float):
        """Update the DailyOutput table with the corrected value."""
        daily_output = self.db.query(DailyOutput).filter(
            DailyOutput.simulation_run_id == simulation_id,
            DailyOutput.date == date_obj
        ).first()
        
        if daily_output:
            if variable == "LAI":
                daily_output.lai = corrected_value
            elif variable == "SM":
                daily_output.sm = corrected_value
            # Add more variables as needed
        
        self.db.commit()
    
    def correct_window(self, request: ErrorCorrectionRequest) -> ErrorCorrectionResponse:
        """Main entry point: Process the 7-day window."""
        
        # 1. Validate the window is exactly 7 days
        window_days = (request.window_end_date - request.window_start_date).days
        if window_days != 6:  # 7 days inclusive
            return ErrorCorrectionResponse(
                simulation_id=request.simulation_id,
                window_start=request.window_start_date,
                window_end=request.window_end_date,
                total_days_processed=0,
                anomalies_detected=0,
                anomalies_corrected=0,
                correction_summary=[],
                message=f"Window must be exactly 7 days. Got {window_days + 1} days."
            )
        
        # 2. Fetch WOFOST daily outputs for the window
        wofost_data = self._get_daily_outputs(
            request.simulation_id, 
            request.window_start_date, 
            request.window_end_date
        )
        
        if not wofost_data:
            return ErrorCorrectionResponse(
                simulation_id=request.simulation_id,
                window_start=request.window_start_date,
                window_end=request.window_end_date,
                total_days_processed=0,
                anomalies_detected=0,
                anomalies_corrected=0,
                correction_summary=[],
                message="No WOFOST data found for this window."
            )
        
        # 3. Fetch interpolated satellite observations for the window
        satellite_data = self._get_interpolated_observations(
            request.field_id,
            request.window_start_date,
            request.window_end_date
        )
        
        # 4. Process each day in the window
        corrections = []
        anomalies_count = 0
        corrected_count = 0
        
        current_date = request.window_start_date
        while current_date <= request.window_end_date:
            if current_date in wofost_data and current_date in satellite_data:
                wofost_lai = wofost_data[current_date]["lai"]
                satellite_lai = satellite_data[current_date]
                
                # Compute residual
                residual = satellite_lai - wofost_lai
                
                # Check if it's an anomaly
                is_anomaly = abs(residual) > request.residual_threshold
                if is_anomaly:
                    anomalies_count += 1
                    
                    # Compute blending weight (scalar Kalman Gain)
                    blending_weight = self._compute_blending_weight(residual, request.residual_threshold)
                    
                    # Apply correction
                    corrected_lai = self._correct_anomaly(wofost_lai, satellite_lai, blending_weight)
                    corrected_count += 1
                    
                    # Update the database
                    self._update_database(request.simulation_id, current_date, "LAI", corrected_lai)
                    
                    correction_record = DailyCorrectionRecord(
                        date=current_date,
                        variable="LAI",
                        wofost_value=wofost_lai,
                        satellite_value=satellite_lai,
                        residual=residual,
                        was_anomaly=True,
                        correction_applied=corrected_lai - wofost_lai,
                        corrected_value=corrected_lai,
                        blending_weight=blending_weight
                    )
                else:
                    # No correction needed
                    correction_record = DailyCorrectionRecord(
                        date=current_date,
                        variable="LAI",
                        wofost_value=wofost_lai,
                        satellite_value=satellite_lai,
                        residual=residual,
                        was_anomaly=False,
                        correction_applied=0.0,
                        corrected_value=wofost_lai,
                        blending_weight=0.0
                    )
                
                corrections.append(correction_record.model_dump())
            
            current_date += timedelta(days=1)
        
        # 5. Return response
        return ErrorCorrectionResponse(
            simulation_id=request.simulation_id,
            window_start=request.window_start_date,
            window_end=request.window_end_date,
            total_days_processed=len(corrections),
            anomalies_detected=anomalies_count,
            anomalies_corrected=corrected_count,
            correction_summary=corrections,
            message=f"Processed {len(corrections)} days. Found {anomalies_count} anomalies, corrected {corrected_count}."
        )