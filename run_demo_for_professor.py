#!/usr/bin/env python3
"""
run_demo_for_professor.py
=========================

Automated execution script for demonstrating the closed-loop Ensemble Kalman Filter (EnKF)
data assimilation workflow for Rice (IR64) with irrigation.
"""

import sys
import time
import requests

BASE_URL = "http://127.0.0.1:8000"


def main():
    print("=" * 80)
    print(" AGRITWIN ENKF CLOSED-LOOP ASSIMILATION DEMONSTRATION FOR PROFESSOR")
    print("=" * 80)

    # Step 1: Create Farm & Field
    print("\n[Step 1] Creating Farm & Field...")
    field_payload = {
        "name": "Demo Rice Field",
        "latitude": 26.8,
        "longitude": 80.9,
        "boundary_geojson": {
            "type": "Polygon",
            "coordinates": [[
                [80.89, 26.79],
                [80.91, 26.79],
                [80.91, 26.81],
                [80.89, 26.81],
                [80.89, 26.79]
            ]]
        },
        "farm_name": "Demo Farm"
    }
    resp = requests.post(f"{BASE_URL}/fields", json=field_payload)
    if resp.status_code not in (200, 201):
        print(f"Error creating field: {resp.text}")
        sys.exit(1)
    
    field_data = resp.json()
    field_id = field_data["field_id"]
    print(f"Successfully registered Farm: 'Demo Farm'")
    print(f"Successfully registered Field: 'Demo Rice Field' (UUID: {field_id})")

    # Step 2: Ingest Satellite Observations
    print("\n[Step 2] Ingesting Synthetic Satellite LAI Observations...")
    # Sowing: 2020-06-20, Harvest: 2020-11-10
    lai_url = (
        f"{BASE_URL}/satellite/lai?field_id={field_id}"
        f"&start_date=2020-06-20&end_date=2020-11-10"
        f"&index_name=NDVI&uncertainty=0.3"
    )
    resp = requests.get(lai_url)
    if resp.status_code != 200:
        print(f"Error ingesting observations: {resp.text}")
        sys.exit(1)
    
    obs_list = resp.json()
    print(f"Ingested {len(obs_list)} valid Sentinel-2 LAI observations over the season.")

    # Step 3: Run Baseline Open-Loop Simulation
    print("\n[Step 3] Running Open-Loop Crop Simulation (Rainfed + Timed Irrigation)...")
    sim_payload = {
        "latitude": 26.8,
        "longitude": 80.9,
        "crop": "rice",
        "variety": "Rice_IR64",
        "sowing_date": "2020-06-20",
        "harvest_date": "2020-11-10",
        "max_duration": 220,
        "use_real_weather": True,
        "use_real_soil": True,
        "field_id": field_id,
        "irrigation_events": [
            {"date": "2020-07-05", "amount_mm": 50},
            {"date": "2020-07-20", "amount_mm": 50},
            {"date": "2020-08-05", "amount_mm": 50},
            {"date": "2020-08-20", "amount_mm": 50}
        ]
    }
    
    resp = requests.post(f"{BASE_URL}/simulate", json=sim_payload)
    if resp.status_code != 200:
        print(f"Error running simulation: {resp.text}")
        sys.exit(1)
        
    sim_data = resp.json()
    sim_id = sim_data["simulation_id"]
    summary = sim_data["summary"]
    metrics = sim_data["metrics"]
    print(f"Baseline Simulation successfully run (UUID: {sim_id})")
    print(f"  * Baseline Yield: {metrics['final_twso_kg_ha']:.1f} kg/ha")
    print(f"  * Baseline Peak LAI: {metrics['peak_lai']:.3f}")
    print(f"  * Sowing Date: {summary['doe']}")
    print(f"  * Harvest Date: {summary['doh']}")

    # Step 4: Run Closed-Loop EnKF Assimilation
    print("\n[Step 4] Launching Ensemble Kalman Filter (EnKF) Seasonal Loop...")
    run_payload = {
        "simulation_id": sim_id,
        "field_id": field_id,
        "ensemble_size": 25
    }
    
    start_time = time.time()
    resp = requests.post(f"{BASE_URL}/assimilation/run", json=run_payload)
    elapsed = time.time() - start_time
    
    if resp.status_code != 200:
        print(f"Error running EnKF loop: {resp.text}")
        sys.exit(1)
        
    run_data = resp.json()
    run_id = run_data["assimilation_run_id"]
    print(f"EnKF loop executed in {elapsed:.2f} seconds.")
    print(f"Assimilation Run Status: {run_data['status']}")
    print(f"  * Run UUID: {run_id}")
    print(f"  * Executed Cycles: {run_data['executed_cycles']}")
    print(f"  * Observations Assimilated: {run_data['observations_assimilated']}")

    # Step 5: Fetch Status of the Assimilation
    print("\n[Step 5] Fetching Run Status Details...")
    resp = requests.get(f"{BASE_URL}/assimilation/status/{sim_id}")
    if resp.status_code == 200:
        status_data = resp.json()
        print(f"Latest status for Simulation {sim_id}:")
        print(f"  - Status: {status_data['status']}")
        print(f"  - Ensemble Size: {status_data['ensemble_size']}")
        print(f"  - Total Cycles Discovered: {status_data['total_cycles']}")
        print(f"  - Executed Cycles: {status_data['executed_cycles']}")
        print(f"  - Latest Cycle Date: {status_data['latest_cycle_date']}")

    # Step 6: Fetch History (Step-by-step Updates)
    print("\n[Step 6] EnKF Cycle History (Audit Trail) - Sample:")
    resp = requests.get(f"{BASE_URL}/assimilation/{sim_id}/history")
    if resp.status_code == 200:
        history = resp.json()
        print(f"Total cycles recorded: {len(history)}")
        print(f"{'Cycle #':<9}{'Date':<12}{'Observed LAI':<15}{'Prior LAI':<12}{'Posterior LAI':<15}{'Innovation':<12}{'Quality Score':<15}")
        print("-" * 84)
        for h in history[:5]:  # Display first 5 cycles
            obs_lai = h["observation_vector"].get("LAI")
            prior_lai = h["prior_state"].get("LAI")
            post_lai = h["posterior_state"].get("LAI")
            innov_lai = h["innovation"].get("LAI")
            q_score = h["quality_score"]

            obs_str = f"{obs_lai:.3f}" if obs_lai is not None else "None"
            prior_str = f"{prior_lai:.3f}" if prior_lai is not None else "None"
            post_str = f"{post_lai:.3f}" if post_lai is not None else "None"
            innov_str = f"{innov_lai:.3f}" if innov_lai is not None else "None"
            q_str = f"{q_score:.1f}" if q_score is not None else "None"

            print(
                f"{h['cycle_number']:<9}"
                f"{h['cycle_date']:<12}"
                f"{obs_str:<15}"
                f"{prior_str:<12}"
                f"{post_str:<15}"
                f"{innov_str:<12}"
                f"{q_str:<15}"
            )
        if len(history) > 5:
            print("... [truncated for display] ...")

    # Step 7: Fetch Yield Evolution
    print("\n[Step 7] Yield Projection (TWSO) Evolution over Cycles:")
    resp = requests.get(f"{BASE_URL}/assimilation/{sim_id}/yield-evolution")
    if resp.status_code == 200:
        y_evolution = resp.json()
        print(f"{'Date':<15}{'Predicted Yield (kg/ha)':<30}")
        print("-" * 45)
        for pt in y_evolution:
            val = pt["predicted_yield_kg_ha"]
            print(f"{pt['date']:<15}{f'{val:.2f}' if val is not None else 'None':<30}")

    # Step 8: Fetch Timeseries Comparison
    print("\n[Step 8] Timeseries Comparison (Open-Loop vs EnKF Assimilated vs Obs) - Sample:")
    resp = requests.get(f"{BASE_URL}/assimilation/{sim_id}/timeseries")
    if resp.status_code == 200:
        ts = resp.json()
        lai_ts = ts.get("LAI", [])
        print(f"{'Date':<12}{'Open-Loop LAI':<15}{'Assimilated LAI':<20}{'Observation LAI':<15}")
        print("-" * 62)
        # Show a few dates around the peak of the season
        for pt in lai_ts[40:55]:
            open_val = pt["open_loop"]
            assim_val = pt["assimilated"]
            obs_val = pt["observation"]

            open_str = f"{open_val:.3f}" if open_val is not None else "None"
            assim_str = f"{assim_val:.3f}" if assim_val is not None else "None"
            obs_str = f"{obs_val:.3f}" if obs_val is not None else "None"

            print(
                f"{pt['date']:<12}"
                f"{open_str:<15}"
                f"{assim_str:<20}"
                f"{obs_str:<15}"
            )

    print("\n" + "=" * 80)
    print(" DEMO COMPLETED SUCCESSFULLY. SCRIPT AND APIS READY FOR PRESENTATION.")
    print("=" * 80)


if __name__ == "__main__":
    main()
