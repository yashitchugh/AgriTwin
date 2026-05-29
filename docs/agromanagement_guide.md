# agromanagement_guide.md
# AgroManagement — Complete Guide for AgriTwin

---

## 1. What is AgroManagement?

**AgroManagement** in PCSE is the system that defines:
- **When** a crop is sown and harvested
- **What management events** happen during the growing season (irrigation, fertilization, etc.)
- **How the crop calendar** progresses

It is expressed as a **YAML file** (or equivalent Python dict) that PCSE reads before simulation. It acts as the "farm management script" that drives the simulation timeline.

Without AgroManagement, WOFOST does not know when to start, what crop to grow, or when to harvest.

---

## 2. AgroManagement YAML Structure

```yaml
AgroManagement:
- <campaign_start_date>:
    CropCalendar:
      crop_name: <string>
      variety_name: <string>
      crop_start_date: <YYYY-MM-DD>
      crop_start_type: sowing          # or 'emergence'
      crop_end_date: <YYYY-MM-DD>
      crop_end_type: harvest           # or 'maturity' or 'death'
      max_duration: <int>              # max days to run if end not reached
    TimedEvents:
    - event_signal: irrigate
      name: irrigation_table
      comment: "Irrigation schedule"
      events_table:
      - <YYYY-MM-DD>: {amount: <mm>, efficiency: 0.7}
      - <YYYY-MM-DD>: {amount: <mm>, efficiency: 0.7}
    StateEvents: null
```

**Key fields explained:**

| Field | Meaning |
|---|---|
| `campaign_start_date` | Date when PCSE starts simulation (can be before sowing) |
| `crop_name` | Must match PCSE crop parameter file name exactly |
| `variety_name` | Must match variety key inside crop file |
| `crop_start_type` | `sowing` (most common) or `emergence` |
| `crop_end_type` | `harvest` (most common), `maturity`, or `death` |
| `max_duration` | Safety cap — model stops after this many days even without harvest signal |
| `TimedEvents` | Events triggered on specific calendar dates |
| `StateEvents` | Events triggered when a state variable crosses a threshold |

---

## 3. Minimal Winter Wheat Example (No Irrigation)

```yaml
AgroManagement:
- 2023-10-01:
    CropCalendar:
      crop_name: wheat
      variety_name: Winter_wheat_101
      crop_start_date: 2023-10-15
      crop_start_type: sowing
      crop_end_date: 2024-06-30
      crop_end_type: harvest
      max_duration: 300
    TimedEvents: null
    StateEvents: null
```

**Notes:**
- `campaign_start_date` (2023-10-01) is before `crop_start_date` (2023-10-15) — this is intentional. The gap allows soil initialization.
- `max_duration: 300` prevents infinite loops if harvest signal never fires.

---

## 4. Wheat Example With Irrigation

```yaml
AgroManagement:
- 2023-10-01:
    CropCalendar:
      crop_name: wheat
      variety_name: Winter_wheat_101
      crop_start_date: 2023-10-15
      crop_start_type: sowing
      crop_end_date: 2024-06-30
      crop_end_type: harvest
      max_duration: 300
    TimedEvents:
    - event_signal: irrigate
      name: Irrigation schedule
      comment: "3 irrigation events"
      events_table:
      - 2023-12-01: {amount: 25, efficiency: 0.7}
      - 2024-02-15: {amount: 30, efficiency: 0.7}
      - 2024-04-10: {amount: 20, efficiency: 0.7}
    StateEvents: null
```

**Irrigation fields:**

| Field | Unit | Description |
|---|---|---|
| `amount` | mm | Amount of water applied |
| `efficiency` | 0–1 | Fraction that reaches root zone (0.7 = 70%) |

---

## 5. Maize Example (Kharif Season)

```yaml
AgroManagement:
- 2023-06-01:
    CropCalendar:
      crop_name: maize
      variety_name: Maize_VanHeemst_1988
      crop_start_date: 2023-06-15
      crop_start_type: sowing
      crop_end_date: 2023-11-30
      crop_end_type: harvest
      max_duration: 180
    TimedEvents:
    - event_signal: irrigate
      name: Kharif irrigation
      comment: "Monsoon supplemental irrigation"
      events_table:
      - 2023-07-01: {amount: 40, efficiency: 0.8}
      - 2023-08-01: {amount: 35, efficiency: 0.8}
    StateEvents: null
```

---

## 6. State Events (Advanced — use later)

StateEvents trigger when a state variable crosses a threshold:

```yaml
StateEvents:
- event_signal: apply_npk
  name: N top-dressing at stem elongation
  comment: "Apply nitrogen when DVS >= 0.5"
  zero_condition: rising
  condition: DVS >= 0.5
  events_table:
  - apply_N: 40
    apply_P: 0
    apply_K: 0
```

**For MVP: Set `StateEvents: null`** — not needed for initial build.

---

## 7. Valid Crop Names in PCSE

Use exactly these strings in `crop_name`:

| crop_name string | Crop |
|---|---|
| `wheat` | Wheat |
| `maize` | Maize / Corn |
| `soybean` | Soybean |
| `rice` | Rice |
| `potato` | Potato |
| `sugarbeet` | Sugar Beet |
| `sunflower` | Sunflower |
| `barley` | Barley |

Check available varieties:
```python
from pcse.input import YAMLCropDataProvider  # NOT pcse.fileinput (deprecated)
cropd = YAMLCropDataProvider()
cv = cropd.get_crops_varieties()  # dict: {crop_name: [variety_names]}
print(list(cv.keys()))            # list all available crops
print(list(cv['wheat']))          # list wheat varieties
```

---

## 8. Dynamic YAML Generation in FastAPI

The `agro_service.py` module should generate AgroManagement YAML from API input:

```python
from datetime import date, timedelta
import yaml

def build_agromanagement(
    crop_name: str,
    variety_name: str,
    sow_date: date,
    harvest_date: date,
    irrigation_events: list[dict],  # [{"date": "2023-12-01", "amount": 25, "efficiency": 0.7}]
    max_duration: int = 365
) -> dict:
    """Build AgroManagement dict from user inputs."""
    
    campaign_start = sow_date - timedelta(days=14)  # 2 weeks before sowing
    
    # Build irrigation events table
    timed_events = None
    if irrigation_events:
        events_table = []
        for ev in irrigation_events:
            events_table.append({
                ev["date"]: {
                    "amount": ev["amount"],
                    "efficiency": ev.get("efficiency", 0.7)
                }
            })
        timed_events = [{
            "event_signal": "irrigate",
            "name": "Irrigation schedule",
            "comment": "User-defined irrigation",
            "events_table": events_table
        }]
    
    agro_dict = {
        "AgroManagement": [{
            campaign_start.isoformat(): {
                "CropCalendar": {
                    "crop_name": crop_name,
                    "variety_name": variety_name,
                    "crop_start_date": sow_date.isoformat(),
                    "crop_start_type": "sowing",
                    "crop_end_date": harvest_date.isoformat(),
                    "crop_end_type": "harvest",
                    "max_duration": max_duration
                },
                "TimedEvents": timed_events,
                "StateEvents": None
            }
        }]
    }
    return agro_dict


def agro_dict_to_yaml_string(agro_dict: dict) -> str:
    return yaml.dump(agro_dict, default_flow_style=False)
```

**Usage in simulation_service.py:**
```python
agro_dict = build_agromanagement(
    crop_name="wheat",
    variety_name="Winter_wheat_101",
    sow_date=date(2023, 10, 15),
    harvest_date=date(2024, 6, 30),
    irrigation_events=[
        {"date": "2023-12-01", "amount": 25, "efficiency": 0.7}
    ]
)

# Pass directly to PCSE (it accepts dict):
wofost = Wofost72_WLP_FD(params, wdp, agro_dict["AgroManagement"])
```

---

## 9. Common Mistakes

### Mistake 1: Wrong date format
```yaml
# WRONG
crop_start_date: 15-10-2023

# CORRECT
crop_start_date: 2023-10-15
```

### Mistake 2: Campaign start after sow date
```yaml
# WRONG — campaign must start before or equal to crop_start_date
- 2023-10-20:
    CropCalendar:
      crop_start_date: 2023-10-15  # ← earlier than campaign start!

# CORRECT
- 2023-10-01:
    CropCalendar:
      crop_start_date: 2023-10-15
```

### Mistake 3: Wrong crop_name string
```python
# WRONG
crop_name: "Wheat"  # capital W

# CORRECT
crop_name: "wheat"  # lowercase
```

### Mistake 4: Irrigation date outside growing season
Irrigation events must fall between `campaign_start_date` and `crop_end_date`. PCSE silently ignores out-of-range events.

### Mistake 5: Missing `max_duration`
Without `max_duration`, if harvest conditions are never met, the simulation runs indefinitely.

---

## 10. AgroManagement Validation Checklist

Before running WOFOST, validate:
- [ ] `campaign_start_date` < `crop_start_date`
- [ ] `crop_start_date` < `crop_end_date`
- [ ] `crop_name` matches PCSE database exactly (lowercase)
- [ ] `variety_name` exists for that crop
- [ ] All irrigation dates are within campaign window
- [ ] `max_duration` > expected growing season length
- [ ] Irrigation `amount` in mm (not cm or liters)
