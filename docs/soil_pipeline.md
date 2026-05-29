# soil_pipeline.md
# AgriTwin — Soil Data Pipeline

---

## 1. Overview

AgriTwin uses **SoilGrids** (ISRIC World Soil Information) as its primary soil data source. SoilGrids provides globally consistent, ML-derived soil property maps at 250m resolution.

For the MVP, we need a minimal but scientifically valid set of soil hydraulic parameters to drive the WOFOST water balance module.

```
User provides: lat, lon
      │
      ▼
SoilGrids REST API → JSON response
      │
      ▼
Extract & aggregate by depth layer
      │
      ▼
Map to PCSE soil parameter names
      │
      ▼
Apply pedotransfer functions (if needed)
      │
      ▼
WOFOST soil parameter dict
```

---

## 2. SoilGrids API

**Base URL:**
```
https://rest.isric.org/soilgrids/v2.0/properties/query
```

**Key parameters:**

| Parameter | Description |
|---|---|
| `lon` | Longitude |
| `lat` | Latitude |
| `property` | Soil property code (e.g., `silt`, `clay`, `sand`, `phh2o`, `bdod`, `wv0010`, `wv0033`, `wv1500`) |
| `depth` | Depth layer (e.g., `0-5cm`, `5-15cm`, `15-30cm`, `30-60cm`, `60-100cm`) |
| `value` | `mean` (use mean prediction) |

---

## 3. Required Soil Properties from SoilGrids

| SoilGrids Code | Description | Native Units |
|---|---|---|
| `wv0010` | Volumetric water content at pF 1.0 (near saturation) | cm³/100cm³ |
| `wv0033` | Volumetric water content at pF 2.0 (≈ field capacity) | cm³/100cm³ |
| `wv1500` | Volumetric water content at pF 4.2 (≈ wilting point) | cm³/100cm³ |
| `bdod` | Bulk density | kg/dm³ (×100 = cg/cm³) |
| `clay` | Clay content | g/kg (÷1000 = fraction) |
| `sand` | Sand content | g/kg |
| `silt` | Silt content | g/kg |
| `phh2o` | pH in water | pH×10 |

**Note on units:** SoilGrids returns values multiplied by 10 or 100 for precision in integer storage. Always divide by the conversion factor listed in the API response metadata.

---

## 4. Fetching Soil Data

```python
import requests

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

REQUIRED_PROPERTIES = ["wv0010", "wv0033", "wv1500", "bdod", "clay", "sand", "silt"]
DEPTH_LAYERS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm"]

def fetch_soilgrids(lat: float, lon: float) -> dict:
    """Fetch soil properties from SoilGrids REST API."""
    
    params = {
        "lon": lon,
        "lat": lat,
        "property": REQUIRED_PROPERTIES,
        "depth": DEPTH_LAYERS,
        "value": ["mean"]
    }
    
    response = requests.get(SOILGRIDS_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
```

**Example response structure:**
```json
{
  "properties": {
    "layers": [
      {
        "name": "wv0033",
        "unit_measure": {
          "conversion_factor": 0.1,
          "d_factor": 10
        },
        "depths": [
          {
            "label": "0-5cm",
            "values": {"mean": 284}
          },
          {
            "label": "5-15cm",
            "values": {"mean": 298}
          }
        ]
      }
    ]
  }
}
```

The actual value = `284 * 0.1 = 28.4` cm³/100cm³ = 0.284 cm³/cm³

---

## 5. Parsing and Averaging Across Depths

For WOFOST, we need a **single representative value** per property, averaged over the effective root zone (typically 0–60 cm for most crops):

```python
def parse_soilgrids_response(raw: dict) -> dict:
    """Parse SoilGrids JSON into a flat property dict, averaged over root zone."""
    
    TARGET_DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm"]
    result = {}
    
    for layer in raw["properties"]["layers"]:
        name = layer["name"]
        factor = layer["unit_measure"]["conversion_factor"]
        
        values = []
        for depth in layer["depths"]:
            if depth["label"] in TARGET_DEPTHS:
                mean_val = depth["values"].get("mean")
                if mean_val is not None:
                    values.append(mean_val * factor)
        
        if values:
            result[name] = sum(values) / len(values)
    
    return result
```

---

## 6. Mapping SoilGrids to PCSE Parameters

After parsing, map to PCSE-expected parameter names:

```python
def soilgrids_to_pcse(sg: dict) -> dict:
    """Map SoilGrids properties to PCSE soil parameter dict."""
    
    # wv0033 = field capacity (pF 2.0) in cm³/100cm³ → cm³/cm³
    smfcf = sg.get("wv0033", 30.0) / 100.0
    
    # wv1500 = wilting point (pF 4.2) in cm³/100cm³ → cm³/cm³
    smw = sg.get("wv1500", 12.0) / 100.0
    
    # wv0010 = near saturation → proxy for SM0
    sm0 = sg.get("wv0010", 45.0) / 100.0
    
    # Ensure physical consistency: SMW < SMFCF < SM0
    smw = min(smw, smfcf - 0.02)
    sm0 = max(sm0, smfcf + 0.02)
    
    # Hydraulic conductivity — estimate from texture (pedotransfer)
    clay_frac = sg.get("clay", 200.0) / 1000.0  # g/kg → fraction
    sand_frac = sg.get("sand", 400.0) / 1000.0
    
    k0 = estimate_k0(clay_frac, sand_frac)   # see pedotransfer below
    
    pcse_soil = {
        "SMFCF": round(smfcf, 3),     # Field capacity (cm³/cm³)
        "SMW": round(smw, 3),          # Wilting point (cm³/cm³)
        "SM0": round(sm0, 3),          # Saturation (cm³/cm³)
        "CRAIRC": 0.060,               # Critical air content (fixed for MVP)
        "RDMSOL": 100.0,               # Max rootable depth (cm) — use fixed for MVP
        "SOPE": estimate_sope(k0),     # Max percolation rate root zone (cm/day)
        "KSUB": estimate_sope(k0),     # Max percolation subsoil (cm/day)
        "K0": k0,                      # Saturated hydraulic conductivity (cm/day)
    }
    
    return pcse_soil
```

---

## 7. Pedotransfer Functions (PTF)

When SoilGrids does not provide hydraulic conductivity directly, use simplified pedotransfer functions:

### Saturated Hydraulic Conductivity (K0)

Simple Cosby et al. (1984) approximation:
```python
def estimate_k0(clay_frac: float, sand_frac: float) -> float:
    """Estimate saturated hydraulic conductivity (cm/day) from texture.
    
    Based on Cosby et al. (1984) regression.
    clay_frac and sand_frac are fractions (0-1).
    """
    clay_pct = clay_frac * 100
    sand_pct = sand_frac * 100
    
    # Log10(K0) in inches/hour
    log_k = -0.60 + 0.0126 * sand_pct - 0.0064 * clay_pct
    k_inches_per_hour = 10 ** log_k
    
    # Convert inches/hour → cm/day
    k_cm_per_day = k_inches_per_hour * 2.54 * 24
    
    # Clip to reasonable range: 1 – 1000 cm/day
    return max(1.0, min(k_cm_per_day, 1000.0))


def estimate_sope(k0: float) -> float:
    """Estimate maximum percolation rate SOPE from K0.
    Typically SOPE ≈ K0 / 10 for WOFOST parameter."""
    return max(0.1, k0 / 10.0)
```

---

## 8. PCSE Soil Parameters — Full Reference

| PCSE Parameter | Description | Typical Range | Source |
|---|---|---|---|
| `SMFCF` | Field capacity | 0.20–0.45 | SoilGrids wv0033 |
| `SMW` | Wilting point | 0.05–0.25 | SoilGrids wv1500 |
| `SM0` | Saturation | 0.35–0.60 | SoilGrids wv0010 |
| `CRAIRC` | Critical air content | 0.04–0.10 | Fixed 0.06 for MVP |
| `RDMSOL` | Max rootable depth | 50–150 cm | Fixed 100 cm for MVP |
| `SOPE` | Max percolation rate (root zone) | 0.1–10 cm/day | Derived from K0 |
| `KSUB` | Max percolation rate (subsoil) | 0.1–10 cm/day | Same as SOPE for MVP |
| `K0` | Sat. hydraulic conductivity | 1–500 cm/day | PTF from texture |

---

## 9. MVP Simplifications

For the MVP, the following simplifications are acceptable:

1. **Fixed `RDMSOL = 100 cm`** — most crops root to 80–120 cm
2. **Fixed `CRAIRC = 0.06`** — typical value for loam/clay-loam soils
3. **Average over 0–60 cm depth** — representative of root zone
4. **`KSUB = SOPE`** — no subsoil heterogeneity modeled
5. **SoilGrids caching** — store results in DB, don't refetch for same lat/lon

---

## 10. Soil Service Module Pattern

```python
# services/soil_service.py

class SoilService:
    
    def get_soil_params(self, lat: float, lon: float, db: Session) -> dict:
        """Return PCSE-compatible soil parameter dict for given location."""
        
        # Check cache first
        cached = self._check_db_cache(lat, lon, db)
        if cached:
            return cached
        
        # Fetch from SoilGrids
        raw = fetch_soilgrids(lat, lon)
        parsed = parse_soilgrids_response(raw)
        pcse_params = soilgrids_to_pcse(parsed)
        
        # Store in DB
        self._store_soil(lat, lon, pcse_params, db)
        
        return pcse_params
```

---

## 11. Soil Validation Checks

Always validate before passing to WOFOST:

```python
def validate_soil_params(soil: dict) -> bool:
    assert soil["SMW"] < soil["SMFCF"] < soil["SM0"], "Soil moisture order violated"
    assert 0.0 < soil["SMW"] < 0.30, f"SMW out of range: {soil['SMW']}"
    assert 0.20 < soil["SMFCF"] < 0.60, f"SMFCF out of range: {soil['SMFCF']}"
    assert soil["RDMSOL"] > 20, f"RDMSOL too shallow: {soil['RDMSOL']}"
    assert soil["K0"] > 0, "K0 must be positive"
    return True
```
