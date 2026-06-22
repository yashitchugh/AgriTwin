"""
assimilation/services/assimilation_visualization_service.py
===========================================================

Service layer handling read-only database queries for EnKF assimilation visualization.
"""

import datetime
import uuid
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.models.simulation_run import SimulationRun
from backend.app.models.assimilation_run import AssimilationRun
from backend.app.assimilation.models.assimilation_state import AssimilationState
from backend.app.assimilation.models.observation import Observation
from backend.app.models.daily_output import DailyOutput


class AssimilationVisualizationService:
    """Service to prepare comparative and historical datasets for EnKF visualization."""

    def __init__(self, db: Session):
        self.db = db

    def get_history(self, simulation_id: uuid.UUID) -> List[dict]:
        """Fetch step-by-step history of EnKF updates for the latest assimilation run.

        Returns:
            A list of cycle detail dicts matching CycleHistoryItem schema structure.
        """
        # Verify the simulation run exists
        sim_run = self.db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
        if not sim_run:
            return []

        # Fetch the latest assimilation run associated with this simulation
        latest_run = (
            self.db.query(AssimilationRun)
            .filter(AssimilationRun.simulation_id == simulation_id)
            .order_by(AssimilationRun.started_at.desc())
            .first()
        )
        if not latest_run:
            return []

        # Fetch all assimilation state updates sorted chronologically
        states = (
            self.db.query(AssimilationState)
            .filter(AssimilationState.assimilation_run_id == latest_run.id)
            .order_by(AssimilationState.assimilation_time.asc())
            .all()
        )

        history = []
        for idx, state in enumerate(states):
            cycle_date = state.assimilation_time.date()

            # Format state vectors with uppercase keys for external presentation
            prior = {k.upper(): v for k, v in state.forecast_state_vector.items()}
            posterior = {k.upper(): v for k, v in state.updated_state_vector.items()}
            obs_vec = {k.upper(): v for k, v in state.observation_vector.items()}
            innov = {k.upper(): v for k, v in state.innovation_vector.items()}

            # Determine which variables were actually updated during this cycle
            variables_updated = [k for k, v in obs_vec.items() if v is not None]

            # Query average quality score of valid observations used on this cycle date
            quality_scores = (
                self.db.query(Observation.quality_score)
                .filter(
                    Observation.field_id == state.field_id,
                    func.date(Observation.timestamp) == cycle_date,
                    Observation.status == "VALID"
                )
                .all()
            )
            valid_scores = [q[0] for q in quality_scores if q[0] is not None]
            avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else None

            history.append({
                "cycle_date": cycle_date,
                "variables_updated": variables_updated,
                "observation_vector": obs_vec,
                "prior_state": prior,
                "posterior_state": posterior,
                "innovation": innov,
                "quality_score": avg_score,
                "cycle_number": idx + 1
            })

        return history

    def get_timeseries(self, simulation_id: uuid.UUID) -> dict:
        """Get comparative timeseries of open-loop, assimilated, and observed values.

        EnKF updates only occur on cycle dates. To construct a continuous daily assimilated
        curve, state vector adjustments (posterior - prior) are propagated forward as an
        offset correction added to the baseline daily open-loop outputs.
        """
        vars_of_interest = ["LAI", "SM", "TAGP", "TWSO", "RFTRA"]
        result = {v: [] for v in vars_of_interest}

        # Fetch the simulation run and its baseline daily outputs
        sim_run = self.db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
        if not sim_run:
            return result

        daily_outputs = (
            self.db.query(DailyOutput)
            .filter(DailyOutput.simulation_run_id == simulation_id)
            .order_by(DailyOutput.date.asc())
            .all()
        )
        if not daily_outputs:
            return result

        # Retrieve the latest assimilation run and index its states by date
        latest_run = (
            self.db.query(AssimilationRun)
            .filter(AssimilationRun.simulation_id == simulation_id)
            .order_by(AssimilationRun.started_at.desc())
            .first()
        )

        states_by_date = {}
        if latest_run:
            states = (
                self.db.query(AssimilationState)
                .filter(AssimilationState.assimilation_run_id == latest_run.id)
                .all()
            )
            for s in states:
                states_by_date[s.assimilation_time.date()] = s

        # Retrieve all valid observations for this field and group them by date + variable
        obs_by_date_and_var = {}
        if sim_run.field_id:
            obs = (
                self.db.query(Observation)
                .filter(
                    Observation.field_id == sim_run.field_id,
                    Observation.status == "VALID"
                )
                .all()
            )
            for o in obs:
                o_date = o.timestamp.date()
                if o_date not in obs_by_date_and_var:
                    obs_by_date_and_var[o_date] = {}
                obs_by_date_and_var[o_date][o.variable_name.upper()] = o.value

        # Track correction offsets for each variable across dates
        offsets = {v.lower(): 0.0 for v in vars_of_interest}

        for row in daily_outputs:
            curr_date = row.date
            state = states_by_date.get(curr_date)

            # Update offsets on assimilation cycle dates
            if state:
                for v in vars_of_interest:
                    v_lower = v.lower()
                    prior = state.forecast_state_vector.get(v_lower)
                    posterior = state.updated_state_vector.get(v_lower)
                    if posterior is not None and prior is not None:
                        offsets[v_lower] = posterior - prior

            # Append the data point for each variable
            for v in vars_of_interest:
                v_lower = v.lower()
                open_loop_val = getattr(row, v_lower, None)

                # Set assimilated value:
                # - On cycle date, use exact posterior state
                # - Otherwise, add the accumulated offset to open-loop value
                if state and state.updated_state_vector.get(v_lower) is not None:
                    assimilated_val = state.updated_state_vector.get(v_lower)
                else:
                    assimilated_val = (open_loop_val + offsets[v_lower]) if open_loop_val is not None else None

                obs_val = obs_by_date_and_var.get(curr_date, {}).get(v)

                result[v].append({
                    "date": curr_date,
                    "open_loop": open_loop_val,
                    "assimilated": assimilated_val,
                    "observation": obs_val
                })

        return result

    def get_yield_evolution(self, simulation_id: uuid.UUID) -> List[dict]:
        """Fetch predicted yield (TWSO) evolution over each assimilation cycle.

        Returns:
            A list of dicts with date and predicted yield in kg/ha.
        """
        sim_run = self.db.query(SimulationRun).filter(SimulationRun.id == simulation_id).first()
        if not sim_run:
            return []

        latest_run = (
            self.db.query(AssimilationRun)
            .filter(AssimilationRun.simulation_id == simulation_id)
            .order_by(AssimilationRun.started_at.desc())
            .first()
        )
        if not latest_run:
            return []

        states = (
            self.db.query(AssimilationState)
            .filter(AssimilationState.assimilation_run_id == latest_run.id)
            .order_by(AssimilationState.assimilation_time.asc())
            .all()
        )

        evolution = []
        for state in states:
            twso = state.updated_state_vector.get("twso")
            evolution.append({
                "date": state.assimilation_time.date(),
                "predicted_yield_kg_ha": twso
            })

        return evolution
