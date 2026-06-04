"""
diagnose_twso.py — TWSO=0 Investigation Script (READ-ONLY diagnostic)
=======================================================================

Runs Delhi wheat simulation step-by-step and extracts internal PCSE kiosk
variables each day to pinpoint why TWSO remains zero.

PUBLISHED KIOSK VARIABLES USED (verified from PCSE source):
  States:  DVS, LAI, TWSO, WSO, TAGP, GASST, MREST, FO (from partitioning)
  Rates:   DMI, ADMI (from wofost72.py line 143)

NOT published (cannot use get_variable):
  PGASS, MRES, ASRC, GASS — these are local rate variables in wofost72.py:125-127
  → Approximated by differencing GASST and MREST (cumulative published states)

Run from project root:
    cd /home/vini/Arena/AgriTwin
    source venv/bin/activate
    python backend/app/simulation/scratch/diagnose_twso.py
"""

import sys
import os
import datetime as dt

# Script lives at: backend/app/simulation/scratch/diagnose_twso.py
# Project root is 5 levels up
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", "..", "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from pcse.models import Wofost72_WLP_FD
from pcse.base import ParameterProvider

# Import using full path from project root
sys.path.insert(0, os.path.join(PROJECT_ROOT))
from backend.app.simulation.weather_provider import create_weather_provider
from backend.app.simulation.crop_provider import create_crop_provider
from backend.app.simulation.soil_provider import create_soil_params
from backend.app.simulation.site_provider import create_site_params
from backend.app.simulation.agromanagement import build_agromanagement

# ── Delhi simulation parameters ──────────────────────────────────────────────
LATITUDE   = 28.6139
LONGITUDE  = 77.2090
CROP       = "wheat"
VARIETY    = "Winter_wheat_101"
SOW_DATE   = dt.date(2020, 11, 1)   # Typical Delhi rabi sowing
HARVEST_DATE = dt.date(2021, 4, 30)
USE_NASA   = True   # Real weather

print("=" * 70)
print("TWSO DIAGNOSTIC: Delhi Wheat — Winter_wheat_101")
print(f"Location : ({LATITUDE}, {LONGITUDE})")
print(f"Period   : {SOW_DATE} → {HARVEST_DATE}")
print(f"Weather  : {'NASA POWER (real)' if USE_NASA else 'Synthetic'}")
print("=" * 70)

# ── Assemble providers ────────────────────────────────────────────────────────
print("\n[1/5] Creating weather provider...")
wdp = create_weather_provider(
    latitude=LATITUDE,
    longitude=LONGITUDE,
    start_year=SOW_DATE.year,
    end_year=HARVEST_DATE.year,
    use_nasa=USE_NASA,
)
print(f"      Weather: {wdp.first_date} → {wdp.last_date}")

print("[2/5] Loading crop parameters...")
cropd = create_crop_provider(CROP, VARIETY)
print(f"      Crop: {CROP}/{VARIETY}")

print("[3/5] Creating soil parameters (default medium loam)...")
soildata = create_soil_params()
print(f"      SMW={soildata['SMW']:.3f}, SMFCF={soildata['SMFCF']:.3f}, SM0={soildata['SM0']:.3f}")

print("[4/5] Creating site parameters...")
sitedata = create_site_params(wav=10.0)

print("[5/5] Assembling ParameterProvider + AgroManagement...")
params = ParameterProvider(cropdata=cropd, soildata=soildata, sitedata=sitedata)
agro = build_agromanagement(
    crop_name=CROP,
    variety_name=VARIETY,
    sow_date=SOW_DATE,
    harvest_date=HARVEST_DATE,
    max_duration=300,
)

# ── Initialize engine ─────────────────────────────────────────────────────────
wofost = Wofost72_WLP_FD(params, wdp, agro)
print("\n[OK] WOFOST engine initialized. Running step-by-step...\n")

# ── Day-by-day extraction ─────────────────────────────────────────────────────
VARS = ["DVS", "LAI", "TAGP", "TWSO", "WSO", "FO", "DMI", "ADMI", "GASST", "MREST", "RFTRA", "SM"]

records = []
prev_gasst = 0.0
prev_mrest = 0.0
current_date = SOW_DATE - dt.timedelta(days=14)  # campaign starts 14d before sowing

day_count = 0
MAX_DAYS = 310

while not wofost.flag_terminate and day_count < MAX_DAYS:
    wofost.run(days=1)
    day_count += 1

    row = {"date": current_date + dt.timedelta(days=day_count)}
    for v in VARS:
        val = wofost.get_variable(v)
        row[v] = float(val) if val is not None else None

    # Compute daily GASS and MRES from cumulative published states
    gasst = row["GASST"] or 0.0
    mrest = row["MREST"] or 0.0
    row["GASS_daily"] = gasst - prev_gasst   # approximation of GASS (= PGASS × RFTRA)
    row["MRES_daily"] = mrest - prev_mrest   # approximation of MRES
    row["ASRC_daily"] = row["GASS_daily"] - row["MRES_daily"]  # net assimilates
    prev_gasst = gasst
    prev_mrest = mrest

    records.append(row)

print(f"Simulation completed: {day_count} steps, terminate={wofost.flag_terminate}\n")

# ── Analysis ──────────────────────────────────────────────────────────────────

# Filter to days when crop is actually growing (DVS not None)
crop_days = [r for r in records if r.get("DVS") is not None]

if not crop_days:
    print("ERROR: No crop days found — crop never emerged. Check sowing date and weather coverage.")
    sys.exit(1)

# 1. First day DVS > 1.0 (anthesis)
anthesis_day = next((r for r in crop_days if (r["DVS"] or 0) > 1.0), None)
print("=" * 70)
print("QUESTION 1: First day DVS > 1.0 (anthesis)")
if anthesis_day:
    print(f"  → Date: {anthesis_day['date']}, DVS={anthesis_day['DVS']:.4f}")
    print(f"    LAI={anthesis_day['LAI']}, TAGP={anthesis_day['TAGP']}, TWSO={anthesis_day['TWSO']}")
else:
    print("  → DVS never exceeded 1.0 — crop never reached anthesis!")

# 2. FO after anthesis
print("\nQUESTION 2: FO (fraction to storage organs) after DVS > 1.0")
if anthesis_day:
    post_anthesis = [r for r in crop_days if (r["DVS"] or 0) > 1.0]
    fo_vals = [(r["date"], r["DVS"], r["FO"]) for r in post_anthesis[:10]]
    print(f"  First 10 days post-anthesis:")
    for date, dvs, fo in fo_vals:
        print(f"    {date}  DVS={dvs:.3f}  FO={fo}")
    fo_nonzero = [r for r in post_anthesis if (r["FO"] or 0) > 0.001]
    print(f"  → FO > 0 on {len(fo_nonzero)}/{len(post_anthesis)} post-anthesis days")
else:
    print("  → Cannot evaluate — anthesis not reached")

# 3. WSO ever > 0
print("\nQUESTION 3: Does WSO ever become > 0?")
wso_positive = [r for r in crop_days if (r["WSO"] or 0) > 0.0]
if wso_positive:
    print(f"  → YES: WSO > 0 on {len(wso_positive)} days")
    print(f"    First: {wso_positive[0]['date']}, WSO={wso_positive[0]['WSO']:.3f}")
    print(f"    Peak:  WSO={max(r['WSO'] for r in wso_positive):.3f}")
else:
    print("  → NO: WSO = 0 throughout entire simulation")

# 4. GASS after anthesis
print("\nQUESTION 4: Is daily GASS (gross assimilation) positive after anthesis?")
if anthesis_day:
    post = [r for r in crop_days if (r["DVS"] or 0) > 1.0]
    gass_positive = [r for r in post if (r["GASS_daily"] or 0) > 0.01]
    gass_zero = [r for r in post if (r["GASS_daily"] or 0) <= 0.01]
    print(f"  GASS > 0.01 on {len(gass_positive)}/{len(post)} days after anthesis")
    print(f"  GASS ≈ 0    on {len(gass_zero)}/{len(post)} days after anthesis")
    if gass_zero:
        print(f"  First GASS collapse: {gass_zero[0]['date']}, DVS={gass_zero[0]['DVS']:.3f}, LAI={gass_zero[0]['LAI']}")
    if gass_positive:
        print(f"  Max GASS post-anthesis: {max(r['GASS_daily'] for r in gass_positive):.2f} kg CH2O/ha/day")
else:
    print("  → Cannot evaluate — anthesis not reached")

# 5. Assimilation collapse before grain fill?
print("\nQUESTION 5: Does assimilation collapse before grain fill window?")
print("  (Checking GASS_daily and DMI across full crop period)")
if anthesis_day:
    pre_anthesis = [r for r in crop_days if (r["DVS"] or 0) <= 1.0]
    post_anthesis = [r for r in crop_days if (r["DVS"] or 0) > 1.0]

    max_gass_pre = max((r["GASS_daily"] or 0) for r in pre_anthesis) if pre_anthesis else 0
    max_gass_post = max((r["GASS_daily"] or 0) for r in post_anthesis) if post_anthesis else 0
    max_dmi_pre  = max((r["DMI"] or 0) for r in pre_anthesis) if pre_anthesis else 0
    max_dmi_post = max((r["DMI"] or 0) for r in post_anthesis) if post_anthesis else 0
    max_admi_post = max((r["ADMI"] or 0) for r in post_anthesis) if post_anthesis else 0

    print(f"  Max GASS pre-anthesis  : {max_gass_pre:.2f} kg CH2O/ha/day")
    print(f"  Max GASS post-anthesis : {max_gass_post:.2f} kg CH2O/ha/day")
    print(f"  Max DMI  pre-anthesis  : {max_dmi_pre:.2f} kg DM/ha/day")
    print(f"  Max DMI  post-anthesis : {max_dmi_post:.2f} kg DM/ha/day")
    print(f"  Max ADMI post-anthesis : {max_admi_post:.2f} kg DM/ha/day")

    # RFTRA after anthesis (water stress factor: 1=no stress, 0=full stress)
    rftra_vals = [(r["date"], r["DVS"], r["RFTRA"]) for r in post_anthesis if r["RFTRA"] is not None]
    if rftra_vals:
        min_rftra = min(v[2] for v in rftra_vals)
        print(f"  Min RFTRA post-anthesis: {min_rftra:.3f} (1=no stress, 0=full stress)")

print("\n" + "=" * 70)
print("FULL DAILY TABLE (crop period, showing key columns)")
print("=" * 70)
print(f"{'Date':12s} {'DVS':6s} {'LAI':6s} {'FO':5s} {'GASS':8s} {'MRES':8s} {'ASRC':8s} {'DMI':7s} {'WSO':7s} {'TWSO':7s} {'RFTRA':6s}")
print("-" * 90)

for r in crop_days:
    dvs  = r["DVS"]  or 0
    lai  = r["LAI"]  or 0
    fo   = r["FO"]   or 0
    gass = r["GASS_daily"] or 0
    mres = r["MRES_daily"] or 0
    asrc = r["ASRC_daily"] or 0
    dmi  = r["DMI"]  or 0
    wso  = r["WSO"]  or 0
    twso = r["TWSO"] or 0
    rftra = r["RFTRA"] or 0

    # Highlight key transitions
    marker = ""
    if abs(dvs - 1.0) < 0.05:
        marker = " ← ANTHESIS"
    elif abs(dvs - 2.0) < 0.05:
        marker = " ← MATURITY"

    print(f"{str(r['date']):12s} {dvs:6.3f} {lai:6.3f} {fo:5.3f} {gass:8.2f} {mres:8.2f} {asrc:8.2f} {dmi:7.2f} {wso:7.2f} {twso:7.2f} {rftra:6.3f}{marker}")

print("\n[DONE]")
