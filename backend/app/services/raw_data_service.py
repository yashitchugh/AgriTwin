"""
backend/app/services/raw_data_service.py — 7-Day Raw Data Collection Service
==============================================================================

Integrates the collection workflow into the main application.

This service:
1. Fetches 7-day weather from NASA POWER
2. Fetches static soil from SoilGrids
3. Validates all data
4. Stores raw records in PostgreSQL
5. Returns structured data for Conv1D-LSTM cleaning

Usage:
    service = RawDataService()
    result = service.fetch_and_store_window(
        field_id=uuid.uuid4(),
        latitude=28.6,
        longitude=77.2,
        db=session
    )
    print(f"Weather valid: {result['validation']['weather_valid']}")
"""

import logging, uuid, requests
import datetime as dt
from typing import Optional
import numpy as np
from sqlalchemy.orm import Session
from backend.app.models.raw_weather_record import RawWeatherRecord
from backend.app.models.field import Field
from backend.app.services.weather_service import WeatherService
from backend.app.services.soil_service import SoilService

logger = logging.getLogger(__name__)

class RawDataService:
    def __init__(self,timeout:int=60):
        self.timeout=timeout
        self.weather_service=WeatherService()
        self.soil_service=SoilService()
        logger.info("RawDataService initialized with timeout=%ds",timeout)

    def fetch_and_store_window(
            self,
            field_id:uuid.UUID,
            latitude:float,
            longitude:float,
            db:Session,
            end_date:Optional[dt.date]=None
    )->dict:
        #calculate 7-day window
        if end_date is None:
            end_date=dt.date.today() - dt.timedelta(days=1)
        start_date=end_date-dt.timedelta(6)

        logger.info(
            "Fetching 7-day window for field %s: %s to %s",
            field_id, start_date, end_date
        )
        #step-1:Extract 7 days of weather from WeatherService
        weather_data=self._extract_weather_from_provider(
            latitude,longitude,start_date,end_date,
        )

        #step-2: Validate weather data
        validation=self._validate_weather(weather_data)

        #step-3: Fetch soil properties
        soil_data = self.soil_service.get_soil_params(latitude,longitude)

        #step-4:Store raw weather records if validation passed
        records_stored=0
        if validation["weather_valid"]==True:
            records_stored=self._store_raw_weather_records(
                field_id, weather_data,db,
            )
        return {
            "weather_data": weather_data,
            "soil_data": soil_data,
            "validation": validation,
            "records_stored": records_stored
        }

    #Adding the helper method

    def _extract_weather_from_provider(
            self, latitude:float, longitude:float, start_date:dt.date, end_date:dt.date,
    ) ->list[dict]:  #list containing multiple dictonaries
        """
        Fetch weather from NASA POWER and extract 7 days in raw format.
        
        Returns list of 7 dicts, each with keys:
            date, temperature_2m, temperature_min, temperature_max,
            precipitation, radiation, wind_speed, vapor_pressure, toa_radiation
        """

        #Fetch using existing WeatherService
        wdp = self.weather_service.get_weather_provider(
            latitude,longitude,start_date,end_date
        )

        #Extract 7 days from the WeatherDataProvider
        weather_data= []
        current_date=start_date

        while current_date<=end_date:
            wdc = wdp(current_date)

            # Convert PCSE units back to NASA POWER units for raw storage
            weather_data.append({
                "date": current_date.isoformat(),
                "latitude": latitude,
                "longitude": longitude,
                "temperature_2m": wdc.TEMP,           # °C (no conversion)
                "temperature_min": wdc.TMIN,          # °C (no conversion)
                "temperature_max": wdc.TMAX,          # °C (no conversion)
                "precipitation": wdc.RAIN * 10.0,     # cm → mm
                "radiation": wdc.IRRAD / 1e6,         # J/m²/day → MJ/m²/day
                "wind_speed": wdc.WIND,               # m/s (no conversion)
                "vapor_pressure": wdc.VAP,            # hPa (already converted)
                "toa_radiation": None,                # Not stored in WDC
            })

            current_date+= dt.timedelta(days=1)
        logger.info("Extracted %d days of weather data", len(weather_data))
        return weather_data
    
    def _validate_weather(self,weather_data:list[dict])->dict:
        """
        Validate 7-day weather data for completeness and physical consistency.
        
        Checks:
        - Exactly 7 days present
        - All 7 variables present for each day
        - No missing/null values
        - Temperature ordering: TMIN <= TEMP <= TMAX
        
        Returns dict with keys: weather_valid, error_messages
        """

        errors = []

        #checks : Must have exactly 7 days
        if len(weather_data)!=7:
            errors.append(f"Expected 7 days, got {len(weather_data)}")
            return{"weather_valid":False, "error_message":errors}
        
        #check each day
        required_vars= [
            "temperature_2m", "temperature_min", "temperature_max",
            "precipitation", "radiation", "wind_speed", "vapor_pressure"
        ]

        for i , day in enumerate(weather_data):
            day_num=i+1

            #check all variables present
            for var in required_vars:
                if var not in day or day[var] is None:
                    errors.append(f"Day {day_num}: Missing {var}")

            #check temperature ordering
            if all(day.get(k) is not None for k in ["temperature_min","temperature_2m", "temperature_max" ]):
                tmin=day["temperature_min"]
                temp=day["temperature_2m"]
                tmax=day["temperature_max"]

                if not (tmin<=temp<=tmax):
                    errors.append(
                        f"Day {day_num}: Invalid temp ordering "
                        f"(TMIN={tmin:.1f}, TEMP={temp:.1f}, TMAX={tmax:.1f})"
                    )
        
        is_valid = len(errors)==0
        if is_valid:
            logger.info("Weather validation passed: 7 days complete")
        else:
            logger.warning("Weather validation failed: %s", ";".join(errors))

        return {"weather_valid":is_valid, "error_messages":errors}
    
    def _store_raw_weather_records(
            self,
            field_id: uuid.UUID,
            weather_data:list[dict],
            db:Session
    )->int:
        """
        Store 7 RawWeatherRecord instances in the database.
        
        Returns: Number of records successfully stored
        """
        records_created = 0

        for day in weather_data:
            # Parse date string back to date object if needed
            date_value = day["date"]
            if isinstance(date_value, str):
                date_value = dt.date.fromisoformat(date_value)
        
            record = RawWeatherRecord(
                id=uuid.uuid4(),
                field_id=field_id,
                date=date_value,  # ← Use parsed date
                latitude=day.get("latitude", 0.0),
                longitude=day.get("longitude", 0.0),
                source="nasa_power",
                api_status="success",
                temperature_2m=day["temperature_2m"],
                temperature_min=day["temperature_min"],
                temperature_max=day["temperature_max"],
                precipitation=day["precipitation"],
                radiation=day["radiation"],
                wind_speed=day["wind_speed"],
                vapor_pressure=day["vapor_pressure"],
                toa_radiation=day.get("toa_radiation"),
                raw_payload=day
        )
            
            db.add(record)
            records_created+=1

        db.commit()
        logger.info("Stored %d raw weather records for field %s", records_created, field_id)
        return records_created
    
        
        

