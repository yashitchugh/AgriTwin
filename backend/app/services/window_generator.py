import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Tuple
from uuid import UUID
from sqlalchemy.orm import Session
import logging

from backend.app.models.daily_output import DailyOutput
from backend.app.models.simulation_run import SimulationRun
from backend.app.models.field import Field
from backend.app.assimilation.models.observation import Observation  # For satellite data
from backend.app.api.schemas.window_preprocessing import (
    WindowGenerationRequest, 
    WindowGenerationResponse,
    WindowedTrainingDatum
)

logger = logging.getLogger(__name__)

class WindowGenerator:
    
    def __init__(self, db_session: Session):
        self.db = db_session
        
    def _fetch_daily_data(self, simulation_id: UUID) -> pd.DataFrame:
        """Fetch all daily data for the simulation (WOFOST outputs + weather + satellite)."""
        # 1. Fetch SimulationRun to get field_id and coordinates
        sim_run = self.db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
        if not sim_run:
            logger.error(f"SimulationRun with id {simulation_id} not found.")
            return pd.DataFrame()
            
        field_id = sim_run.field_id
        
        # 2. Fetch daily WOFOST outputs
        outputs = self.db.query(DailyOutput).filter(
            DailyOutput.simulation_run_id == simulation_id
        ).order_by(DailyOutput.date).all()
        
        if not outputs:
            return pd.DataFrame()
            
        target_dates = [out.date for out in outputs]
        start_date = target_dates[0]
        end_date = target_dates[-1]
        
        # 3. Fetch weather data provider
        from backend.app.services.weather_service import WeatherService
        weather_service = WeatherService()
        try:
            wdp = weather_service.get_weather_provider(
                latitude=sim_run.latitude,
                longitude=sim_run.longitude,
                start_date=start_date - timedelta(days=14),  # include 14-day pre-season buffer
                end_date=end_date
            )
        except Exception as e:
            logger.warning(f"Failed to fetch weather from provider: {e}. Using mock weather.")
            wdp = None
            
        # 4. Fetch and interpolate satellite LAI observations
        from backend.app.assimilation.repositories.observation_repository import ObservationRepository
        from backend.app.api.schemas.interpolation import InterpolationRequest
        from backend.app.services.temporal_interpolation_service import TemporalInterpolationService
        
        obs_repo = ObservationRepository(self.db)
        observations = obs_repo.get_by_variable(variable_name="LAI", field_id=field_id)
        
        interpolated_lai = {}
        if len(observations) >= 2:
            obs_dates = [o.timestamp.date() for o in observations]
            obs_values = [o.value for o in observations]
            req = InterpolationRequest(
                observation_dates=obs_dates,
                observation_values=obs_values,
                target_dates=target_dates,
                method="cubic_spline",
                max_allowed_gap_days=15
            )
            try:
                resp = TemporalInterpolationService().interpolate(req)
                for d, val in zip(target_dates, resp.interpolated_values):
                    if val is not None:
                        interpolated_lai[d] = val
            except Exception as e:
                logger.warning(f"Failed to interpolate satellite observations: {e}")
        
        data = []
        for out in outputs:
            # Fetch weather for this date
            weather = self._get_weather_for_date(wdp, out.date)
            
            # Fetch interpolated satellite LAI (fallback to model LAI if no satellite data available)
            sat_lai = interpolated_lai.get(out.date, out.lai if out.lai is not None else 0.0)
            
            data.append({
                "date": out.date,
                # WOFOST outputs
                "LAI": out.lai if out.lai is not None else 0.0,
                "SM": out.sm if out.sm is not None else 0.0,
                "DVS": out.dvs if out.dvs is not None else 0.0,
                "TAGP": out.tagp if out.tagp is not None else 0.0,
                "TWSO": out.twso if out.twso is not None else 0.0,
                "WLV": out.wlv if out.wlv is not None else 0.0,
                "WST": out.wst if out.wst is not None else 0.0,
                "WRT": out.wrt if out.wrt is not None else 0.0,
                "WSO": out.wso if out.wso is not None else 0.0,
                # Weather features (from NASA POWER)
                "TEMP_AVG": weather.get("temp_avg", 25.0),
                "PRECIP": weather.get("precip", 0.0),
                "RADIATION": weather.get("radiation", 20.0),
                # Satellite/interpolated target
                "SAT_LAI": sat_lai,
                # Residual
                "RESIDUAL": sat_lai - (out.lai if out.lai is not None else 0.0)
            })
        
        df = pd.DataFrame(data)
        df.set_index("date", inplace=True)
        return df
    
    def _fetch_soil_features(self, field_id: UUID) -> Dict:
        """Fetch soil properties WITH uncertainty (standard deviation)."""
        from backend.app.services.soil_service import SoilService
        field = self.db.query(Field).filter(Field.id == field_id).first()
        
        # Fallback values
        sand_mean, sand_sd = 40.0, 10.0
        clay_mean, clay_sd = 30.0, 8.0
        silt_mean, silt_sd = 30.0, 7.0
        smw, smfcf, sm0 = 0.12, 0.32, 0.45
        
        if field:
            try:
                svc = SoilService()
                params = svc.get_soil_params(field.latitude, field.longitude)
                smw = params.get("SMW", smw)
                smfcf = params.get("SMFCF", smfcf)
                sm0 = params.get("SM0", sm0)
            except Exception as e:
                logger.warning(f"Failed to fetch soil params via SoilService: {e}")
                
        return {
            "SAND": sand_mean,
            "SAND_SD": sand_sd,
            "CLAY": clay_mean,
            "CLAY_SD": clay_sd,
            "SILT": silt_mean,
            "SILT_SD": silt_sd,
            "SMW": smw,
            "SMFCF": smfcf,
            "SM0": sm0,
        }
    
    def _get_weather_for_date(self, wdp, date_obj: date) -> Dict:
        """Helper: Fetch weather for a specific date from the weather provider."""
        if wdp:
            try:
                try:
                    wdata = wdp(date_obj)
                except TypeError:
                    wdata = wdp[date_obj]
                return {
                    "temp_avg": wdata.TEMP,
                    "precip": wdata.RAIN * 10.0 if wdata.RAIN is not None else 0.0,
                    "radiation": wdata.IRRAD / 1e6 if wdata.IRRAD is not None else 20.0
                }
            except Exception as e:
                logger.debug(f"Date {date_obj} not found in weather provider: {e}")
        return {"temp_avg": 25.0, "precip": 0.0, "radiation": 20.0}
    
    def _normalize_series(self, series: np.ndarray) -> Tuple[np.ndarray, float, float]:
        """Min-Max normalization. Returns (normalized, min, max)."""
        min_val = np.min(series)
        max_val = np.max(series)
        if max_val - min_val == 0:
            return np.zeros_like(series), min_val, max_val
        normalized = (series - min_val) / (max_val - min_val)
        return normalized, min_val, max_val
    
    def generate_windows(self, request: WindowGenerationRequest) -> WindowGenerationResponse:
        """Main entry point: Generate sliding windows for ML training."""
        
        # 1. Fetch all daily data for the simulation
        df = self._fetch_daily_data(request.simulation_id)
        if df.empty:
            return WindowGenerationResponse(
                simulation_id=request.simulation_id,
                total_windows_generated=0,
                features_used=[],
                normalization_scalers={},
                start_date=date.today(),
                end_date=date.today(),
                message="No daily data found."
            )
        
        soil_features = self._fetch_soil_features(request.field_id)
        soil_keys = list(soil_features.keys())
        
        # Add static soil features as constant columns to the DataFrame
        for k, v in soil_features.items():
            df[k] = v
        
        # 3. Define which features will be used in the window
        #    Include: WOFOST states, weather, phenology, and SOIL UNCERTAINTY (the SD!)
        dynamic_features = [
            "LAI", "SM", "DVS", "TAGP", "TWSO",
            "TEMP_AVG", "PRECIP", "RADIATION"
        ]
        all_features = dynamic_features + soil_keys
        n_features_per_day = len(dynamic_features)
        n_static_features = len(soil_keys)
        
        # 4. Convert to numpy arrays for fast sliding window
        dates = list(df.index)
        n_days = len(df)
        feature_matrix = df[all_features].values  # Shape: (n_days, total_features)
        
        # 5. Create sliding windows
        windows = []
        window_size = request.window_size
        stride = request.stride
        
        for start_idx in range(0, n_days - window_size - 1, stride):
            end_idx = start_idx + window_size - 1
            target_idx = end_idx + 1  # The next day (what we're predicting)
            
            # Window data: (window_size, n_features_per_day) + static features
            window_data = feature_matrix[start_idx:end_idx+1, :n_features_per_day]  # Dynamic part
            static_data = feature_matrix[start_idx, n_features_per_day:]  # Static (constant across window)
            
            # Target: The residual at target_idx (we want to predict tomorrow's residual)
            target_residual = df.iloc[target_idx]["RESIDUAL"]
            target_lai = df.iloc[target_idx]["LAI"]
            
            # Flatten the window: (window_size * n_features_per_day) + static_features
            flattened_window = window_data.flatten().tolist() + static_data.tolist()
            
            windows.append({
                "start_date": dates[start_idx],
                "end_date": dates[end_idx],
                "target_date": dates[target_idx],
                "flattened_features": flattened_window,
                "target_residual": target_residual,
                "target_lai": target_lai,
                # Context metadata for explainability
                "window_mean_temp": np.mean(window_data[:, 3]),  # TEMP_AVG
                "window_total_rain": np.sum(window_data[:, 4]),   # PRECIP
                "window_avg_dvs": np.mean(window_data[:, 2]),     # DVS
            })
        
        # 6. Normalize ALL windows if requested
        normalization_scalers = {}
        if request.normalize and windows:
            # Convert all windows to a matrix for min-max scaling
            feature_matrix_all = np.array([w["flattened_features"] for w in windows])
            
            # Normalize each feature column across all windows
            for col_idx in range(feature_matrix_all.shape[1]):
                col_data = feature_matrix_all[:, col_idx]
                normalized_col, min_val, max_val = self._normalize_series(col_data)
                feature_matrix_all[:, col_idx] = normalized_col
                normalization_scalers[f"feature_{col_idx}"] = {"min": min_val, "max": max_val}
            
            # Update the windows with normalized features
            for i, w in enumerate(windows):
                w["flattened_features"] = feature_matrix_all[i].tolist()
        
        # 7. Save windows to database (optional but recommended)
        # You would save these to a `WindowedTrainingData` table
        # For now, just return the statistics
        
        response = WindowGenerationResponse(
            simulation_id=request.simulation_id,
            total_windows_generated=len(windows),
            features_used=all_features,
            normalization_scalers=normalization_scalers,
            start_date=dates[0],
            end_date=dates[-1],
            message=f"Generated {len(windows)} windows of size {window_size}."
        )
        
        # Optional: Save the first few windows to a CSV for debugging
        # import pandas as pd
        # df_windows = pd.DataFrame(windows)
        # df_windows.to_csv(f"windows_{request.simulation_id}.csv", index=False)
        
        return response