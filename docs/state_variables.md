# state_variables.md
# AgriTwin — State Variables Reference

---

## Overview

State variables are the quantities that describe the current condition of the crop-soil system at any point in time. In AgriTwin, state variables serve three roles:

1. **Simulated** — computed by WOFOST at each daily timestep
2. **Observable** — can be estimated from remote sensing or field sensors
3. **Assimilated** — corrected by EnKF when observations are available

---

## Variable Summary Table

| Variable | Full Name | Units | Observable | Assimilated |
|---|---|---|---|---|
| `LAI` | Leaf Area Index | m²/m² | ✅ Yes | ✅ Yes |
| `SM` | Soil Moisture | cm³/cm³ | ✅ (future) | Future |
| `TAGP` | Total Above-Ground Biomass | kg/ha | ❌ Hidden | Predicted |
| `TWSO` | Total Weight Storage Organs | kg/ha | ❌ Hidden | Predicted |
| `DVS` | Development Stage | unitless | ❌ Hidden | Predicted |
| `TRA` | Actual Transpiration | cm/day | ❌ Hidden | Diagnostic |
| `RD` | Rooting Depth | cm | ❌ Hidden | Diagnostic |

---

## 1. LAI — Leaf Area Index

**Full Form:** Leaf Area Index

**Scientific Meaning:**
LAI is the total one-sided area of leaf tissue per unit ground surface area. It quantifies how much of the sky is covered by leaves when viewed from directly below.

```
LAI = total leaf area (m²) / ground area (m²)
```

- LAI = 0: bare soil, no crop canopy
- LAI = 1: leaves cover the ground surface area exactly once
- LAI = 4–6: dense crop canopy (typical peak for wheat/maize)

**Units:** m²/m² (dimensionless ratio)

**Why Important:**
- Controls light interception → drives photosynthesis → drives growth
- Primary satellite-observable crop variable (NDVI, EVI are proxies)
- Sensitive to water stress and nitrogen deficiency
- Key indicator of crop development stage

**Observable?** ✅ Yes — retrievable from Sentinel-2 via inversion of reflectance models (PROSAIL, etc.) or empirical NDVI→LAI relationships.

**Assimilated?** ✅ Yes — LAI is the **primary assimilation target** in AgriTwin MVP. When satellite LAI observations arrive, EnKF corrects simulated LAI toward the observed value.

**Typical trajectory:**
```
Sowing → 0.0
Emergence → 0.1–0.3
Tillering → 1.0–2.0
Heading → 3.5–5.0 (peak)
Maturity → 0.5 (senescence)
```

**PCSE access:**
```python
lai = wofost.get_variable('LAI')
```

---

## 2. SM — Soil Moisture

**Full Form:** Soil Moisture (volumetric water content)

**Scientific Meaning:**
SM is the fraction of soil volume occupied by water in the root zone. It is bounded by:
- `SMW` (wilting point) — minimum water plants can extract
- `SMFCF` (field capacity) — water held after free drainage
- `SM0` (saturation) — all pore spaces filled

```
SMW ≤ SM ≤ SM0
```

**Units:** cm³/cm³ (volumetric fraction, also expressed as m³/m³)

**Why Important:**
- Controls water availability to crops → water stress → reduced LAI and yield
- Drives transpiration calculation in WOFOST
- Affects root zone dynamics

**Water Stress Indicator:**
```
RFTRA = (SM - SMW) / (SMCR - SMW)
```
Where `SMCR` is critical soil moisture. RFTRA < 1 means crop is stressed.

**Observable?** ✅ Partially — Sentinel-1 SAR can estimate surface SM, but root zone SM requires modeling. For MVP, SM is **not assimilated** (LAI-only assimilation).

**Assimilated?** Future — will be assimilated when SM observations are integrated.

**PCSE access:**
```python
sm = wofost.get_variable('SM')
```

---

## 3. TAGP — Total Above-Ground Biomass

**Full Form:** Total Above-Ground Plant mass (dry weight)

**Scientific Meaning:**
TAGP is the total dry mass of all above-ground plant organs: leaves, stems, and storage organs (grain, tubers, etc.).

```
TAGP = WLV + WST + TWSO
```
Where:
- `WLV` = Weight of Leaves
- `WST` = Weight of Stems
- `TWSO` = Weight of Storage Organs

**Units:** kg/ha (dry matter per hectare)

**Why Important:**
- Overall indicator of crop productivity
- Directly related to carbon assimilation
- Proxy for yield potential
- Used in biomass estimation studies

**Observable?** ❌ No — cannot be directly observed from satellite. Only measurable by destructive field sampling.

**Assimilated?** Not directly — predicted by WOFOST from corrected LAI. When LAI is corrected by EnKF, TAGP implicitly improves.

**Typical values at harvest:**
- Wheat: 10,000–15,000 kg/ha
- Maize: 12,000–20,000 kg/ha

**PCSE access:**
```python
tagp = wofost.get_variable('TAGP')
```

---

## 4. TWSO — Total Weight of Storage Organs

**Full Form:** Total Weight of Storage Organs

**Scientific Meaning:**
TWSO is the dry weight of the economically harvestable part of the crop:
- **Wheat/barley/rice:** grain (kernel)
- **Maize:** cob grain
- **Potato:** tubers
- **Sugarbeet:** beet roots
- **Soybean:** pods/seeds

**Units:** kg/ha

**Why Important:**
- **Primary yield proxy** — TWSO at harvest date ≈ grain yield
- Final target variable for yield forecasting
- Partitioning to storage organs increases after DVS > 1.0

**Observable?** ❌ No — not directly measurable until harvest.

**Assimilated?** Not directly — predicted. Correcting LAI during the season improves TWSO at harvest through the causal chain:
```
Better LAI → Better photosynthesis estimate → Better biomass → Better TWSO
```

**Harvest Index:**
```
HI = TWSO / TAGP
```
Typical HI: 0.4–0.5 for modern wheat varieties.

**PCSE access:**
```python
twso = wofost.get_variable('TWSO')
```

---

## 5. DVS — Development Stage

**Full Form:** Development Stage (also called Crop Development Stage)

**Scientific Meaning:**
DVS is a continuous index from 0 to 2 that describes where the crop is in its lifecycle, driven primarily by **temperature accumulation** (thermal time / growing degree days):

| DVS | Stage | Description |
|---|---|---|
| 0.0 | Sowing | Start of simulation |
| 0.0–1.0 | Vegetative | Leaf growth, tillering, stem elongation |
| 1.0 | Anthesis/Flowering | End of vegetative phase |
| 1.0–2.0 | Reproductive | Grain filling, storage organ growth |
| 2.0 | Maturity | Harvest trigger |

**Units:** Unitless (continuous 0 to 2)

**Why Important:**
- Controls biomass partitioning between leaves, stems, and storage organs
- Determines when harvest signal fires (DVS = 2.0)
- Temperature-driven: warm seasons → fast development
- Independent of water stress (DVS progression is thermal-time based)

**Observable?** ❌ No — cannot be observed from satellite directly. Some studies use SAR backscatter as proxy.

**Assimilated?** No — DVS is not corrected by EnKF. It progresses only from thermal time accumulation. Resetting DVS would violate phenological consistency.

**Thermal time calculation:**
```
TSUM1 = sum of effective temperature from sowing to anthesis
TSUM2 = sum of effective temperature from anthesis to maturity
DVS = TSUM_accumulated / TSUM1  (if < 1.0)
DVS = 1 + TSUM_accumulated_post_anthesis / TSUM2  (if > 1.0)
```

**PCSE access:**
```python
dvs = wofost.get_variable('DVS')
```

---

## 6. TRA — Actual Transpiration

**Full Form:** Actual Crop Transpiration Rate

**Scientific Meaning:**
TRA is the daily amount of water evaporated through plant stomata into the atmosphere. It is the actual (water-stressed) transpiration, as opposed to potential transpiration.

```
TRA = potential_transpiration × RFTRA (reduction factor for water stress)
```

When SM is above critical threshold → TRA ≈ potential_transpiration
When SM drops below critical → TRA decreases → water stress occurs

**Units:** cm/day

**Why Important:**
- Diagnostic variable for water stress severity
- Links soil water balance to crop growth
- High TRA deficit → stomatal closure → reduced CO₂ uptake → reduced growth
- Useful for irrigation scheduling recommendations

**Observable?** ❌ Not directly. Eddy covariance towers measure ET but not standard for individual fields.

**Assimilated?** No — diagnostic output only.

**PCSE access:**
```python
tra = wofost.get_variable('TRA')
```

---

## 7. RD — Rooting Depth

**Full Form:** Rooting Depth

**Scientific Meaning:**
RD is the current depth of the active root zone. It starts at a minimum depth at emergence and increases until it reaches the maximum rootable depth (`RDMSOL`) or maximum crop rooting depth (`RDMAX`).

Deeper roots → access to more soil water → reduced drought stress.

**Units:** cm

**Why Important:**
- Determines effective soil water storage volume
- Affects resilience to short drought periods
- Increases during vegetative phase, stabilizes at anthesis

**Observable?** ❌ No — requires physical soil coring.

**Assimilated?** No — diagnostic output.

**PCSE access:**
```python
rd = wofost.get_variable('RD')
```

---

## 8. Disease Risk Proxy (Future Variable)

Not a native WOFOST variable. Calculated externally using:
- Temperature + humidity combinations (leaf wetness hours)
- DVS threshold (disease risk usually highest between DVS 0.5–1.0)
- LAI (dense canopies trap moisture)

```python
def disease_risk_proxy(tmin, tmax, rain, lai, dvs):
    """Simple heuristic disease risk score 0-1."""
    humidity_risk = 1.0 if (tmax - tmin < 10 and rain > 5) else 0.3
    canopy_risk = min(lai / 5.0, 1.0)
    season_risk = 1.0 if (0.5 < dvs < 1.2) else 0.5
    return (humidity_risk * canopy_risk * season_risk)
```

---

## 9. State Variable Extraction Pattern (for simulation_service.py)

```python
TRACKED_VARIABLES = ['LAI', 'SM', 'TAGP', 'TWSO', 'DVS', 'TRA', 'RD']

def extract_state(wofost, date) -> dict:
    state = {"date": date.isoformat()}
    for var in TRACKED_VARIABLES:
        try:
            state[var] = wofost.get_variable(var)
        except Exception:
            state[var] = None
    return state
```
