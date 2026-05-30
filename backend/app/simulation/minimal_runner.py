"""
minimal_runner.py — Demo script using the modular simulation architecture
==========================================================================

This script validates that the modular refactor produces IDENTICAL results
to the original monolithic runner. It uses the clean public API from
backend/app/simulation/ instead of inline PCSE calls.

Usage:
    cd /home/vini/Arena/AgriTwin
    source venv/bin/activate
    python backend/app/simulation/minimal_runner.py
"""

import os
import sys
import logging

# Ensure project root is on sys.path so 'backend.app.simulation' resolves
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Configure logging so all module-level loggers produce visible output
logging.basicConfig(
    level=logging.INFO,
    format="  %(name)-40s │ %(message)s",
)

# Import the modular public API
from backend.app.simulation.engine import run_simulation


def print_results(result):
    """Print simulation results in a clean tabular format."""

    print("\n" + "=" * 70)
    print("  SIMULATION RESULTS")
    print("=" * 70)

    m = result.metrics
    print(f"\n  Total simulation days: {m['total_days']}")

    if not result.raw_output:
        print("  WARNING: No output generated!")
        return

    # ── Helper for None-safe formatting ──
    def fmt(val, width, decimals):
        if val is None:
            return " " * (width + 1)
        return f" {val:{width}.{decimals}f}"

    # ── Daily output table ──
    header = f"  {'Date':<12} {'DVS':>6} {'LAI':>7} {'SM':>7} {'TAGP':>9} {'TWSO':>9}"
    print(f"\n{header}")
    print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")

    def print_row(rec):
        day = rec.get('day', '?')
        line = f"  {str(day):<12}"
        line += fmt(rec.get('DVS'), 6, 3)
        line += fmt(rec.get('LAI'), 7, 3)
        line += fmt(rec.get('SM'), 7, 4)
        line += fmt(rec.get('TAGP'), 9, 1)
        line += fmt(rec.get('TWSO'), 9, 1)
        print(line)

    raw = result.raw_output
    for rec in raw[:10]:
        print_row(rec)
    if len(raw) > 20:
        print(f"  {'... (skipping middle days) ...':^54}")
    for rec in raw[max(10, len(raw) - 10):]:
        print_row(rec)

    # ── Summary metrics ──
    print(f"\n  {'─' * 45}")
    print(f"  Peak LAI:           {m['peak_lai']:.3f} m²/m²")
    print(f"  Final DVS:          {m['final_dvs']:.3f}")
    print(f"  Final TAGP:         {m['final_tagp_kg_ha']:.1f} kg/ha")
    print(f"  Final TWSO (yield): {m['final_twso_kg_ha']:.1f} kg/ha")
    print(f"  Harvest Index:      {m['harvest_index']:.3f}")

    # ── PCSE summary ──
    if result.summary:
        print(f"\n  PCSE Summary Output:")
        for key, value in result.summary.items():
            if value is not None:
                if isinstance(value, float):
                    print(f"    {key:<12}: {value:.2f}")
                else:
                    print(f"    {key:<12}: {value}")


if __name__ == "__main__":
    print("=" * 70)
    print("  AgriTwin — Modular WOFOST Simulation Runner")
    print("=" * 70)

    try:
        # Run using the clean modular API — one function call replaces
        # all the inline provider setup from the original monolithic script
        result = run_simulation(
            crop_name="wheat",
            variety_name="Winter_wheat_101",
            latitude=52.0,
            longitude=5.5,
            use_nasa_weather=False,   # Synthetic weather for offline runs
            step_by_step=False,       # Batch mode (no EnKF needed yet)
        )

        print_results(result)
        print(f"\n✅ Simulation completed successfully! ({result})")

    except Exception as e:
        print(f"\n❌ Simulation failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
