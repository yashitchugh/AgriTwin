# weather_pipeline.md
# AgriTwin — Weather Data Pipeline

---

## 1. Overview

AgriTwin fetches daily meteorological data from the **NASA POWER API** (Prediction Of Worldwide Energy Resources). NASA POWER provides free, globally consistent gridded daily weather data at 0.5° resolution, derived from satellite and reanalysis products.

```
User provides: lat, lon, start_date, end_date
        │
        ▼
Weather Service → NASA POWER API
        │
        ▼
Parse JSON → Map to PCSE variable names + units
        │
        ▼
Build custom WeatherDataProvider
        │
        ▼
WOFOST simulation consumes daily weather
```

---

## 2. NASA POWER API — Key Details

**Base URL:**
```
https://power.larc.nasa.gov/api/temporal/daily/point
```

**Required parameters:**

| Parameter | Value |
|---|---|
| `parameters` | Comma-separated list of variable codes |
| `community` | `AG` (Agricultural community) |
| `longitude` | Farm longitude |
| `latitude` | Farm latitude |
| `start` | YYYYMMDD |
| `end` | YYYYMMDD |
| `format` | `JSON` |

---

## 3. Required NASA POWER Variables

| NASA POWER Code | PCSE Variable | Description | Units (NASA) | Units (PCSE) |
|---|---|---|---|---|
| `T2M_MAX` | `TMAX` | Max daily temperature | °C | °C |
| `T2M_MIN` | `TMIN` | Min daily temperature | °C | °C |
| `PRECTOTCORR` | `RAIN` | Precipitation | mm/day | cm/day (**÷10**) |
| `ALLSKY_SFC_SW_DWN` | `IRRAD` | Solar radiation | MJ/m²/day | J/m²/day (**×10⁶**) |
| `WS2M` | `WIND` | Wind speed at 2m | m/s | m/s |
| `QV2M` | `VAP` | Specific humidity at 2m | g/kg | kPa (convert) |
| `RH2M` | — | Relative humidity (alternative) | % | — |

**Important unit conversions:**
```python
TMAX = T2M_MAX           # no change
TMIN = T2M_MIN           # no change
RAIN = PRECTOTCORR / 10  # mm → cm
IRRAD = ALLSKY_SFC_SW_DWN * 1e6  # MJ/m² → J/m²
WIND = WS2M              # no change
VAP = convert_specific_humidity_to_vapour_pressure(QV2M, T2M_MAX)
```

**Vapour pressure conversion from specific humidity:**
```python
def specific_humidity_to_vapour_pressure(qv2m_g_per_kg: float, temp_c: float) -> float:
    """Convert specific humidity (g/kg) to actual vapour pressure (kPa)."""
    # Saturation vapour pressure (kPa)
    es = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    # Actual vapour pressure via mixing ratio approximation
    qv = qv2m_g_per_kg / 1000.0  # g/kg → kg/kg
    vap = (qv * 101.325) / (0.622 + qv)  # kPa
    return vap
```

---

## 4. Full API Call Example

```python
import requests
from datetime import date

def fetch_nasa_power(
    lat: float,
    lon: float,
    start_date: date,
    end_date: date
) -> dict:
    """Fetch daily weather from NASA POWER API."""
    
    params = {
        "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,WS2M,QV2M",
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": start_date.strftime("%Y%m%d"),
        "end": end_date.strftime("%Y%m%d"),
        "format": "JSON"
    }
    
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    return response.json()
```

**Example response structure:**
```json
{
  "properties": {
    "parameter": {
      "T2M_MAX": {
        "20231001": 28.5,
        "20231002": 27.8,
        ...
      },
      "T2M_MIN": {
        "20231001": 14.2,
        "20231002": 13.9,
        ...
      },
      "PRECTOTCORR": {
        "20231001": 0.0,
        "20231002": 3.2,
        ...
      },
      "ALLSKY_SFC_SW_DWN": {
        "20231001": 18.4,
        "20231002": 15.1,
        ...
      },
      "WS2M": {
        "20231001": 2.1,
        "20231002": 1.8,
        ...
      },
      "QV2M": {
        "20231001": 11.2,
        "20231002": 10.8,
        ...
      }
    }
  }
}
```

---

## 5. Parsing and Mapping to PCSE Format

```python
import math
from datetime import date, timedelta
from pcse.base import WeatherDataProvider, WeatherDataContainer
from pcse.util import reference_ET

def parse_nasa_power_to_pcse(raw_json: dict, lat: float, lon: float, elevation: float = 100.0) -> WeatherDataProvider:
    """Parse NASA POWER JSON into a PCSE WeatherDataProvider."""
    
    params = raw_json["properties"]["parameter"]
    dates = sorted(params["T2M_MAX"].keys())
    
    class CustomWeatherProvider(WeatherDataProvider):
        def __init__(self):
            WeatherDataProvider.__init__(self)
            self.latitude = lat
            self.longitude = lon
            self.elevation = elevation
            self.description = [f"NASA POWER weather for lat={lat}, lon={lon}"]
            
            for date_str in dates:
                d = date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
                
                tmax = params["T2M_MAX"].get(date_str, -999)
                tmin = params["T2M_MIN"].get(date_str, -999)
                rain_mm = params["PRECTOTCORR"].get(date_str, 0.0)
                irrad_mj = params["ALLSKY_SFC_SW_DWN"].get(date_str, 0.0)
                wind = params["WS2M"].get(date_str, 1.0)
                qv2m = params["QV2M"].get(date_str, 10.0)
                
                # Skip fill values
                if tmax == -999 or tmin == -999:
                    continue
                
                # Unit conversion
                rain = rain_mm / 10.0        # mm → cm
                irrad = irrad_mj * 1e6       # MJ/m² → J/m²
                vap = specific_humidity_to_vapour_pressure(qv2m, (tmax + tmin) / 2.0)
                
                # Estimate reference ET (Penman-Monteith)
                e0, es0, et0 = reference_ET(d, lat, elevation, tmin, tmax, irrad, vap, wind, angstA=0.29, angstB=0.49)
                
                wdc = WeatherDataContainer(
                    LAT=lat, LON=lon, ELEV=elevation,
                    DAY=d,
                    TMAX=tmax, TMIN=tmin,
                    RAIN=rain,
                    IRRAD=irrad,
                    WIND=wind,
                    VAP=vap,
                    E0=e0, ES0=es0, ET0=et0,
                    SNOWDEPTH=0.0
                )
                self._store_WeatherDataContainer(wdc)
    
    return CustomWeatherProvider()
```

---

## 6. PCSE WeatherDataContainer — Required Fields

| Field | Description | Units |
|---|---|---|
| `LAT` | Latitude | degrees |
| `LON` | Longitude | degrees |
| `ELEV` | Elevation | m asl |
| `DAY` | Date | `datetime.date` object |
| `TMAX` | Maximum temperature | °C |
| `TMIN` | Minimum temperature | °C |
| `RAIN` | Rainfall | **cm/day** |
| `IRRAD` | Solar radiation | **J/m²/day** |
| `WIND` | Wind speed | m/s |
| `VAP` | Actual vapour pressure | **kPa** |
| `E0` | Penman open water ET | **cm/day** |
| `ES0` | Penman bare soil ET | **cm/day** |
| `ET0` | Penman-Monteith reference ET | **cm/day** |
| `SNOWDEPTH` | Snow depth | cm |

**Critical unit warnings:**
- `RAIN` must be in **cm/day**, NOT mm/day
- `IRRAD` must be in **J/m²/day**, NOT MJ/m²/day
- `VAP` must be in **kPa**, NOT hPa or Pa

---

## 7. Caching Strategy

NASA POWER API is slow (5–15 seconds per request). Implement caching to avoid repeated calls:

```python
# PostgreSQL weather_records table as primary cache
# Also use in-memory dict for single session

WEATHER_CACHE = {}  # key: (lat, lon, date_str)

def get_or_fetch_weather(lat: float, lon: float, start: date, end: date, db: Session) -> list:
    """Check DB cache first, fetch only missing dates."""
    
    existing = db.query(WeatherRecord).filter(
        WeatherRecord.latitude == round(lat, 2),
        WeatherRecord.longitude == round(lon, 2),
        WeatherRecord.date >= start,
        WeatherRecord.date <= end
    ).all()
    
    existing_dates = {r.date for r in existing}
    all_dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    missing_dates = all_dates - existing_dates
    
    if missing_dates:
        fetch_start = min(missing_dates)
        fetch_end = max(missing_dates)
        raw = fetch_nasa_power(lat, lon, fetch_start, fetch_end)
        new_records = parse_and_store_weather(raw, lat, lon, db)
        existing.extend(new_records)
    
    return existing
```

---

## 8. Handling Missing Data

NASA POWER uses fill values (-999 or -99) for missing data. Handle them:

```python
def clean_value(val, default=None, fill_values=(-999, -99, -9999)):
    if val in fill_values:
        return default
    return val

# For rainfall, use 0.0 as default if missing
# For temperature, interpolate from neighbors or skip the day
# For radiation, use climatological daily mean as fallback
```

**Missing data strategy for MVP:**
- Missing `RAIN`: replace with 0.0
- Missing `TMAX`/`TMIN`: linear interpolation from adjacent days
- Missing `IRRAD`: use latitude-based clear-sky estimate
- Missing `WIND`: replace with 2.0 m/s (neutral default)
- Missing `VAP`: compute from TMIN using Magnus formula approximation

**Magnus formula for dew point approximation:**
```python
def vap_from_tmin(tmin: float) -> float:
    """Estimate vapour pressure from minimum temperature (dew point proxy)."""
    return 0.6108 * math.exp(17.27 * tmin / (tmin + 237.3))
```

---

## 9. Daily Weather Schema (PostgreSQL)

```sql
CREATE TABLE weather_records (
    id SERIAL PRIMARY KEY,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    date DATE NOT NULL,
    tmax FLOAT,
    tmin FLOAT,
    rain FLOAT,        -- cm/day
    irrad FLOAT,       -- J/m²/day
    wind FLOAT,        -- m/s
    vap FLOAT,         -- kPa
    e0 FLOAT,
    es0 FLOAT,
    et0 FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(latitude, longitude, date)
);
```

---

## 10. Weather Service Module Pattern

```python
# services/weather_service.py

class WeatherService:
    
    def get_weather_provider(
        self,
        lat: float,
        lon: float,
        start_date: date,
        end_date: date,
        db: Session
    ) -> WeatherDataProvider:
        """Main entry point: returns a PCSE-compatible WeatherDataProvider."""
        records = self.get_or_fetch(lat, lon, start_date, end_date, db)
        return self.build_pcse_provider(records, lat, lon)
    
    def get_or_fetch(self, lat, lon, start, end, db):
        ...  # cache check + NASA POWER fetch
    
    def build_pcse_provider(self, records, lat, lon):
        ...  # assemble WeatherDataProvider from DB records
```
