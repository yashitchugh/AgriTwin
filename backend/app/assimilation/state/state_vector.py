"""
assimilation/state/state_vector.py — EnKF State Vector
========================================================

StateVector is the numerical representation of the WOFOST crop state
used by the Ensemble Kalman Filter (EnKF) assimilation engine.

Scientific context:
    In data assimilation, the "state vector" x is the set of model variables
    that the filter is allowed to update.  Only variables that:
      (a) can be observed (directly or indirectly via an observation operator H)
      (b) drive future model evolution if corrected

    ...belong in the state vector.  For WOFOST 7.2 the canonical choices are:

        LAI   — Leaf Area Index: directly observable by Sentinel-2
        SM    — Soil Moisture: directly observable by SMAP / soil sensors
        TAGP  — Total Above-Ground Production: proxy for SAR backscatter
        TWSO  — Total Weight Storage Organs: end-of-season target
        RFTRA — Relative Transpiration: water stress indicator
        TWLV  — Total Weight Leaves: partitioning state
        TWST  — Total Weight Stems: partitioning state
        TWRT  — Total Weight Roots: water uptake capacity
        DVS   — Development Stage: phenological clock
        RD    — Root Depth: soil volume for water extraction

Design rules:
    1. NO WOFOST code here.  StateVector is a pure Python / NumPy container.
       It knows nothing about PCSE internals.
    2. NO database access.  The repository layer builds StateVector from ORM
       objects; StateVector never queries the DB directly.
    3. ALL fields are Optional[float] — a partial state is valid (e.g. only
       LAI + SM from a satellite observation, rest None).
    4. IMMUTABLE after construction.  EnKF produces NEW StateVectors for each
       analysis step rather than mutating an existing one.
    5. to_numpy() / from_numpy() use a FIXED variable order defined by
       STATE_VARIABLES.  This guarantees that ensemble matrix columns are
       always in the same order, preventing silent index mismatches.

Relationship to FieldState (twin/field_state.py):
    FieldState is the *digital twin* abstraction — it carries identity context
    (field_id, simulation_id, source tag) alongside the physics variables.
    StateVector is the *mathematical* abstraction — a plain vector for linear
    algebra. The factory method StateVector.from_field_state() bridges them.

    FieldState  →  for API responses, DB persistence, human-readable state
    StateVector →  for EnKF ensemble matrices, analysis step arithmetic

Units (WOFOST conventions):
    LAI   [m² leaf / m² ground]
    SM    [cm³ water / cm³ soil]
    TAGP  [kg dry matter / ha]
    TWSO  [kg dry matter / ha]
    RFTRA [-]  (0 = full stress, 1 = no stress)
    TWLV  [kg dry matter / ha]
    TWST  [kg dry matter / ha]
    TWRT  [kg dry matter / ha]
    DVS   [-]  (0 = emergence, 1 = anthesis, 2 = maturity)
    RD    [cm]
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from backend.app.models.daily_output import DailyOutput
    from backend.app.twin.field_state import FieldState


# ── Canonical variable order ──────────────────────────────────────────────────
# This tuple defines the index mapping for to_numpy() / from_numpy().
# The order is fixed forever — adding new variables appends to the END.
# Never insert in the middle or the EnKF ensemble matrices will silently
# use wrong indices for existing saved ensembles.

STATE_VARIABLES: tuple[str, ...] = (
    "lai",    # 0
    "sm",     # 1
    "tagp",   # 2
    "twso",   # 3
    "rftra",  # 4
    "twlv",   # 5
    "twst",   # 6
    "twrt",   # 7
    "dvs",    # 8
    "rd",     # 9
)

# Dimensionality of the state vector (number of variables)
STATE_DIM: int = len(STATE_VARIABLES)

# Mapping: variable name → column index in the state vector array
STATE_INDEX: dict[str, int] = {v: i for i, v in enumerate(STATE_VARIABLES)}


@dataclass(frozen=True)
class StateVector:
    """Numerical representation of the WOFOST crop state for EnKF.

    Frozen dataclass — all fields are set at construction and cannot be
    modified.  Mutation produces a NEW StateVector via dataclasses.replace()
    or the factory methods.

    Attributes:
        date:   Calendar date this state represents (ISO date).
        lai:    Leaf Area Index [m²/m²].
        sm:     Volumetric soil moisture in the root zone [cm³/cm³].
        tagp:   Total Above-Ground Production [kg/ha].
        twso:   Total Weight of Storage Organs [kg/ha].
        rftra:  Relative Transpiration factor [-] (0=full stress, 1=no stress).
        twlv:   Total Weight of Leaves [kg/ha].
        twst:   Total Weight of Stems [kg/ha].
        twrt:   Total Weight of Roots [kg/ha].
        dvs:    Development Stage [-] (0=emergence, 1=anthesis, 2=maturity).
        rd:     Root Depth [cm].

    Canonical variable order (STATE_VARIABLES):
        Index 0: lai
        Index 1: sm
        Index 2: tagp
        Index 3: twso
        Index 4: rftra
        Index 5: twlv
        Index 6: twst
        Index 7: twrt
        Index 8: dvs
        Index 9: rd
    """

    # ── Temporal anchor ───────────────────────────────────────────────────
    date: Optional[datetime.date] = field(default=None)

    # ── State variables ───────────────────────────────────────────────────
    lai:   Optional[float] = field(default=None)
    sm:    Optional[float] = field(default=None)
    tagp:  Optional[float] = field(default=None)
    twso:  Optional[float] = field(default=None)
    rftra: Optional[float] = field(default=None)
    twlv:  Optional[float] = field(default=None)
    twst:  Optional[float] = field(default=None)
    twrt:  Optional[float] = field(default=None)
    dvs:   Optional[float] = field(default=None)
    rd:    Optional[float] = field(default=None)

    # ── Factory methods ───────────────────────────────────────────────────

    @classmethod
    def from_daily_output(cls, row: "DailyOutput") -> "StateVector":
        """Construct a StateVector from a persisted DailyOutput ORM row.

        This is the primary construction path in the assimilation pipeline:
        the repository loads DailyOutput rows, and this method converts each
        row to a StateVector for use in the EnKF ensemble.

        Args:
            row: A DailyOutput ORM instance loaded from the daily_outputs table.
                 All variable columns are Optional — None values are preserved
                 in the StateVector (sparse state is valid).

        Returns:
            StateVector populated from the database row.

        Example:
            rows = daily_repo.get_daily_outputs(sim_run_id)
            states = [StateVector.from_daily_output(r) for r in rows]
        """
        return cls(
            date=row.date,
            lai=row.lai,
            sm=row.sm,
            tagp=row.tagp,
            twso=row.twso,
            rftra=row.rftra,
            twlv=row.twlv,
            twst=row.twst,
            twrt=row.twrt,
            dvs=row.dvs,
            rd=row.rd,
        )

    @classmethod
    def from_field_state(cls, state: "FieldState") -> "StateVector":
        """Construct a StateVector from a FieldState object.

        Bridges the digital twin layer (FieldState — carries identity context)
        to the mathematical assimilation layer (StateVector — pure numerics).

        Args:
            state: A FieldState from twin/field_state.py.

        Returns:
            StateVector with the same physics variables as the FieldState.
            Identity context (field_id, simulation_id, source) is discarded —
            StateVector is physics-only.

        Example:
            field_state = FieldState.from_daily_output(row, field_id=fid)
            sv = StateVector.from_field_state(field_state)
        """
        return cls(
            date=state.current_date,
            lai=state.lai,
            sm=state.sm,
            tagp=state.tagp,
            twso=state.twso,
            rftra=state.rftra,
            twlv=state.twlv,
            twst=state.twst,
            twrt=state.twrt,
            dvs=state.dvs,
            rd=state.rd,
        )

    @classmethod
    def from_numpy(
        cls,
        array: np.ndarray,
        *,
        date: Optional[datetime.date] = None,
    ) -> "StateVector":
        """Reconstruct a StateVector from a 1-D NumPy array.

        Inverse of to_numpy().  Uses STATE_VARIABLES to map array indices back
        to named fields.  NaN values in the array are converted to None.

        Args:
            array: 1-D NumPy array of length STATE_DIM (10).
                   Must follow the canonical STATE_VARIABLES ordering.
            date:  Optional calendar date to attach to the reconstructed state.

        Returns:
            StateVector reconstructed from the array.

        Raises:
            ValueError: If array.shape != (STATE_DIM,).

        Example:
            x = sv.to_numpy()
            x[0] += 0.1  # perturb LAI
            sv_perturbed = StateVector.from_numpy(x, date=sv.date)
        """
        if array.shape != (STATE_DIM,):
            raise ValueError(
                f"StateVector.from_numpy expects shape ({STATE_DIM},), "
                f"got {array.shape}."
            )

        def _val(idx: int) -> Optional[float]:
            v = float(array[idx])
            return None if np.isnan(v) else v

        return cls(
            date=date,
            **{var: _val(idx) for var, idx in STATE_INDEX.items()},
        )

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_numpy(self, *, fill_value: float = np.nan) -> np.ndarray:
        """Serialise to a 1-D NumPy array in canonical STATE_VARIABLES order.

        None values are replaced by `fill_value` (default NaN).  This is the
        correct convention for EnKF ensemble arithmetic: NaN propagates through
        linear algebra and signals missing variables, which the analysis step
        can detect and handle (e.g. by skipping that ensemble member for the
        affected variable).

        Args:
            fill_value: Value to substitute for None fields.
                        Default: np.nan (IEEE 754 NaN, propagates through math).
                        Use 0.0 when constructing an initial ensemble of zeros.

        Returns:
            np.ndarray of shape (STATE_DIM,) = (10,), dtype float64.
            Index mapping is defined by STATE_VARIABLES / STATE_INDEX.

        Example:
            sv = StateVector(date=date(2024, 3, 15), lai=2.4, sm=0.28)
            x = sv.to_numpy()
            # x[0] == 2.4  (LAI at index 0)
            # x[1] == 0.28 (SM at index 1)
            # x[2:] == nan (unpopulated variables)
        """
        values = [
            getattr(self, var) for var in STATE_VARIABLES
        ]
        return np.array(
            [v if v is not None else fill_value for v in values],
            dtype=np.float64,
        )

    def to_dict(self) -> dict:
        """Serialise to a plain Python dict for logging, API responses, or JSON export.

        The dict includes the `date` field (ISO string or None) and all 10
        state variables.  None values are preserved — they are not replaced by
        NaN (unlike to_numpy).  This gives a human-readable representation
        that maps directly to the API observation schema variable names.

        Returns:
            dict with keys: 'date', and all STATE_VARIABLES names.

        Example:
            sv.to_dict()
            # {
            #   'date': '2024-03-15',
            #   'lai': 2.4, 'sm': 0.28, 'tagp': 1200.0, 'twso': None,
            #   'rftra': 0.95, 'twlv': 300.0, 'twst': 250.0,
            #   'twrt': 80.0, 'dvs': 0.55, 'rd': 42.0,
            # }
        """
        return {
            "date": self.date.isoformat() if self.date is not None else None,
            **{var: getattr(self, var) for var in STATE_VARIABLES},
        }

    # ── Introspection helpers ─────────────────────────────────────────────

    @property
    def populated_variables(self) -> list[str]:
        """Return names of variables that have non-None values.

        Useful for the EnKF analysis step to determine which variables have
        observations available and can be updated.

        Returns:
            List of variable names (subset of STATE_VARIABLES) with values.

        Example:
            sv = StateVector(lai=2.4, sm=0.28)
            sv.populated_variables  # ['lai', 'sm']
        """
        return [var for var in STATE_VARIABLES if getattr(self, var) is not None]

    @property
    def is_complete(self) -> bool:
        """True if all STATE_VARIABLES are populated (no None values).

        A complete state vector can be directly used as an ensemble member
        without any imputation.  Incomplete vectors require handling (e.g.
        fill with ensemble mean, skip, or impute from model climatology).
        """
        return all(getattr(self, var) is not None for var in STATE_VARIABLES)

    @property
    def missing_variables(self) -> list[str]:
        """Return names of variables that are None.

        Complements populated_variables — together they partition STATE_VARIABLES.
        """
        return [var for var in STATE_VARIABLES if getattr(self, var) is None]

    def get(self, variable: str) -> Optional[float]:
        """Get the value of a state variable by name.

        Args:
            variable: Variable name (must be in STATE_VARIABLES).

        Returns:
            float value, or None if the variable is not populated.

        Raises:
            KeyError: If variable is not in STATE_VARIABLES.

        Example:
            sv.get("lai")  # 2.4
            sv.get("twso")  # None (not yet populated)
        """
        if variable not in STATE_INDEX:
            raise KeyError(
                f"{variable!r} is not a valid state variable. "
                f"Valid variables: {STATE_VARIABLES}."
            )
        return getattr(self, variable)

    def __repr__(self) -> str:
        pop = len(self.populated_variables)
        dvs_s  = f"{self.dvs:.3f}"  if self.dvs  is not None else "None"
        lai_s  = f"{self.lai:.3f}"  if self.lai  is not None else "None"
        sm_s   = f"{self.sm:.3f}"   if self.sm   is not None else "None"
        twso_s = f"{self.twso:.1f}" if self.twso is not None else "None"
        return (
            f"<StateVector "
            f"date={self.date} "
            f"dvs={dvs_s} lai={lai_s} sm={sm_s} twso={twso_s} "
            f"[{pop}/{STATE_DIM} vars populated]>"
        )
