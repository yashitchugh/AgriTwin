"""
backend/app/assimilation/ensemble/ensemble_manager.py
=====================================================

Creates and orchestrates multiple WOFOST instances for the Ensemble Kalman Filter.
"""

import logging
import random
import datetime as dt
from typing import Optional, List

from pcse.base import WeatherDataProvider, WeatherDataContainer, ParameterProvider
from pcse.models import Wofost72_WLP_FD

from backend.app.simulation.weather_provider import create_weather_provider
from backend.app.simulation.crop_provider import create_crop_provider
from backend.app.simulation.soil_provider import create_soil_params
from backend.app.simulation.site_provider import create_site_params
from backend.app.simulation.agromanagement import build_agromanagement, get_crop_start_type
from backend.app.assimilation.state.state_vector import StateVector

from .ensemble_member import EnsembleMember

logger = logging.getLogger(__name__)


class PerturbedWeatherProvider(WeatherDataProvider):
    """Wraps an existing WeatherDataProvider and adds noise to daily variables.
    
    This creates independent weather realizations for each ensemble member.
    """
    def __init__(self, base_provider: WeatherDataProvider, noise_std: float = 0.05):
        WeatherDataProvider.__init__(self)
        self.noise_std = noise_std
        
        self.latitude = base_provider.latitude
        self.longitude = base_provider.longitude
        self.elevation = base_provider.elevation
        self.description = [f"Perturbed weather (std={noise_std})"] + base_provider.description
        
        # Pre-generate perturbed weather for all available days
        curr_date = base_provider.first_date
        while curr_date <= base_provider.last_date:
            wdc = base_provider(curr_date)
            
            irrad_noise = random.gauss(1.0, self.noise_std)
            tmin_noise = random.gauss(0.0, self.noise_std * 10)
            tmax_noise = random.gauss(0.0, self.noise_std * 10)
            rain_noise = random.gauss(1.0, self.noise_std)
            
            p_wdc = WeatherDataContainer(
                LAT=wdc.LAT,
                LON=wdc.LON,
                ELEV=wdc.ELEV,
                DAY=wdc.DAY,
                IRRAD=max(0.0, wdc.IRRAD * irrad_noise),
                TMIN=wdc.TMIN + tmin_noise,
                TMAX=wdc.TMAX + tmax_noise,
                TEMP=wdc.TEMP + (tmin_noise + tmax_noise) / 2.0,
                VAP=wdc.VAP,
                RAIN=max(0.0, wdc.RAIN * rain_noise),
                WIND=wdc.WIND,
                E0=wdc.E0,
                ES0=wdc.ES0,
                ET0=wdc.ET0
            )
            self._store_WeatherDataContainer(p_wdc, curr_date)
            curr_date += dt.timedelta(days=1)


class EnsembleManager:
    """Manages the creation and execution of WOFOST ensemble members."""
    
    def __init__(
        self,
        crop_name: str = "wheat",
        variety_name: str = "Winter_wheat_101",
        sow_date: Optional[dt.date] = None,
        harvest_date: Optional[dt.date] = None,
        latitude: float = 52.0,
        longitude: float = 5.5,
        elevation: float = 10.0,
        wav: float = 10.0,
        soil_params: Optional[dict] = None,
        crop_param_dir: Optional[str] = None,
        use_nasa_weather: bool = False,
        max_duration: int = 300,
        irrigation_events: Optional[list] = None,
    ):
        if sow_date is None:
            sow_date = dt.date(2020, 10, 15)
        if harvest_date is None:
            harvest_date = dt.date(2021, 7, 30)
            
        self.members: List[EnsembleMember] = []
        
        # Initialize baseline providers
        self.base_wdp = create_weather_provider(
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            start_year=sow_date.year,
            end_year=harvest_date.year,
            use_nasa=use_nasa_weather,
            start_date=sow_date,
            end_date=harvest_date,
        )
        
        self.cropd = create_crop_provider(
            crop_name=crop_name,
            variety_name=variety_name,
            crop_param_dir=crop_param_dir,
        )
        
        if soil_params is not None:
            self.base_soildata = create_soil_params(**{k.lower(): v for k, v in soil_params.items()})
        else:
            self.base_soildata = create_soil_params()
            
        self.sitedata = create_site_params(wav=wav)
        
        crop_start_type = get_crop_start_type(crop_name, cropdata=self.cropd)
        
        self.agro = build_agromanagement(
            crop_name=crop_name,
            variety_name=variety_name,
            sow_date=sow_date,
            harvest_date=harvest_date,
            max_duration=max_duration,
            irrigation_events=irrigation_events,
            crop_start_type=crop_start_type,
        )

    def create_ensemble(self, n: int = 50) -> None:
        """Create N ensemble members with perturbed parameters and weather.
        
        Perturbs crop (SLATB, SPAN, TSUM1, TSUM2) and soil (SMFCF, SMW) 
        parameters around their baseline values using a Gaussian distribution.
        """
        base_slatb = self.cropd["SLATB"]
        base_span = self.cropd["SPAN"]
        base_tsum1 = self.cropd["TSUM1"]
        base_tsum2 = self.cropd["TSUM2"]
        base_smfcf = self.base_soildata["SMFCF"]
        base_smw = self.base_soildata["SMW"]
        base_sm0 = self.base_soildata["SM0"]
        
        self.members = []
        for i in range(n):
            # Sample parameters (~10% standard deviation)
            # SLATB is a table [x1, y1, x2, y2, ...] -> perturb only y values (odd indices)
            slatb = list(base_slatb)
            for j in range(1, len(slatb), 2):
                slatb[j] = max(0.001, random.gauss(slatb[j], slatb[j] * 0.1))
                
            span = max(10.0, random.gauss(base_span, base_span * 0.1))
            tsum1 = max(100.0, random.gauss(base_tsum1, base_tsum1 * 0.1))
            tsum2 = max(100.0, random.gauss(base_tsum2, base_tsum2 * 0.1))
            
            # Soil parameters: preserve physical constraints (SMW < SMFCF < SM0)
            smw = max(0.01, random.gauss(base_smw, base_smw * 0.1))
            smfcf = max(smw + 0.02, random.gauss(base_smfcf, base_smfcf * 0.1))
            smfcf = min(smfcf, base_sm0 - 0.02)
            
            my_cropd = dict(self.cropd)
            my_cropd.update({
                "SLATB": slatb,
                "SPAN": span,
                "TSUM1": tsum1,
                "TSUM2": tsum2,
            })
            
            my_soildata = dict(self.base_soildata)
            my_soildata.update({
                "SMFCF": smfcf,
                "SMW": smw
            })

            overrides = {
                "SLATB": slatb,
                "SPAN": span,
                "TSUM1": tsum1,
                "TSUM2": tsum2,
                "SMFCF": smfcf,
                "SMW": smw
            }
            
            wdp = PerturbedWeatherProvider(self.base_wdp, noise_std=0.05)
            
            params = ParameterProvider(
                cropdata=my_cropd,
                soildata=my_soildata,
                sitedata=self.sitedata,
            )
            
            wofost = Wofost72_WLP_FD(params, wdp, self.agro)
            
            member = EnsembleMember(
                member_id=i,
                wofost=wofost,
                perturbed_parameters=overrides
            )
            self.members.append(member)
            
        logger.info(f"Created {n} ensemble members with perturbed parameters.")

    def run_until(self, target_date: dt.date) -> None:
        """Advance all ensemble members to the specified target date."""
        for member in self.members:
            # Continue running as long as the member hasn't reached the target
            # and isn't terminated by maturity or harvest
            while member.current_date < target_date and not member.wofost.flag_terminate:
                member.wofost.run(days=1)

    def extract_state_vectors(self) -> List[StateVector]:
        """Extract the current state vector from all ensemble members.
        
        Returns:
            A list of StateVector objects representing the current ensemble state.
        """
        return [member.current_state for member in self.members]
