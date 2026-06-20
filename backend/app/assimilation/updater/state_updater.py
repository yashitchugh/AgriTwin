"""
assimilation/updater/state_updater.py — EnKF State Injection
=============================================================

StateUpdater injects EnKF-corrected state variables back into running WOFOST
ensemble members via the PCSE engine's ``set_variable()`` API.

Scientific rationale:
    After the EnKF analysis step computes a corrected state vector x_a for each
    ensemble member, the updated values must be fed back into the PCSE engine so
    that subsequent forecast steps use the corrected state rather than the free-
    running model trajectory.  This "state injection" is what closes the
    assimilation loop and prevents model drift.

    Only variables where (a) observations are available and (b) corrections are
    physically meaningful are injected.  We deliberately do NOT inject DVS
    (development stage) unless explicitly requested — advancing or retarding
    phenology has irreversible downstream consequences (e.g. skipping grain fill).

PCSE ``set_variable()`` mechanics:
    - Implemented in ``pcse/base/engine.py`` (line 389).
    - Routes the call to the correct sub-model via the VariableKiosk.
    - Only registered state variables can be set; unregistered keys are silently
      ignored by PCSE.
    - Units must exactly match the PCSE internal convention (same as
      ``get_variable()`` return values — see TRACKED_VARIABLES in output_parser.py).

Supported injectable variables (StateVector lowercase → PCSE uppercase):
    lai   → LAI   [m²/m²]        Leaf Area Index
    sm    → SM    [cm³/cm³]      Volumetric soil moisture in root zone
    tagp  → TAGP  [kg/ha]        Total above-ground production
    twso  → TWSO  [kg/ha]        Total weight of storage organs
    twlv  → TWLV  [kg/ha]        Total weight of leaves
    twst  → TWST  [kg/ha]        Total weight of stems
    twrt  → TWRT  [kg/ha]        Total weight of roots
    rftra → RFTRA [-]            Relative transpiration factor

NOT injected by default:
    dvs   — Phenological clock. Resetting DVS jumps phenological stage and
             disrupts downstream thermal-unit integration. Excluded to avoid
             irreversible model corruption. Set inject_dvs=True explicitly.
    rd    — Root depth. Governed by RDMAX and growth rate; back-setting can
             create negative growth rates if a corrected value < current depth.

Physical bounds enforcement:
    All injected values are clamped to physically meaningful ranges before
    calling set_variable(). A value outside bounds indicates either a bad
    assimilation update (EnKF diverged) or a unit mismatch, both of which
    must be caught before they corrupt the PCSE state.

Usage pattern (in the EnKF assimilation loop):
    >>> updater = StateUpdater()
    >>> result = updater.inject(member.wofost, corrected_state)
    >>> # then advance: member.wofost.run(days=1)
"""

from __future__ import annotations

import logging
import dataclasses
import datetime
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pcse.models import Wofost72_WLP_FD

logger = logging.getLogger(__name__)


# ── Injectable variable registry ──────────────────────────────────────────────
# Maps StateVector lowercase field names → PCSE uppercase set_variable() keys.
# Variables NOT in this map cannot be injected (by design).

PCSE_KEY_MAP: dict[str, str] = {
    "lai":   "LAI",
    "sm":    "SM",
    "tagp":  "TAGP",
    "twso":  "TWSO",
    "twlv":  "TWLV",
    "twst":  "TWST",
    "twrt":  "TWRT",
    "rftra": "RFTRA",
    # dvs and rd intentionally omitted from the default map.
    # Use inject_dvs=True or inject_rd=True to allow them.
}

# Tuple of supported variable names for external inspection
INJECTABLE_VARIABLES: tuple[str, ...] = tuple(PCSE_KEY_MAP.keys())

# Physical bounds: (min_inclusive, max_inclusive)
# Values outside these bounds are clamped with a warning.
# Bounds are conservative — they reflect biological maxima, not mathematical limits.
_BOUNDS: dict[str, tuple[float, float]] = {
    "lai":   (0.0,    20.0),    # m²/m²: LAI > 20 is biologically impossible for field crops
    "sm":    (0.0,     1.0),    # cm³/cm³: bounded by porosity (typically < 0.6)
    "tagp":  (0.0,  50000.0),   # kg/ha: extreme upper bound for above-ground biomass
    "twso":  (0.0,  20000.0),   # kg/ha: extreme upper bound for storage organs
    "twlv":  (0.0,  20000.0),   # kg/ha
    "twst":  (0.0,  20000.0),   # kg/ha
    "twrt":  (0.0,  10000.0),   # kg/ha
    "rftra": (0.0,    1.0),     # [-]: bounded by definition
    # dvs and rd bounds (used when inject_dvs/inject_rd are enabled)
    "dvs":   (0.0,    3.0),     # [-]: 0=emergence, 2=maturity, 3=harvest
    "rd":    (1.0,  200.0),     # cm: minimum 1 cm to avoid zero-division in waterbalance
}


@dataclass
class InjectionResult:
    """Record of what StateUpdater.inject() did for one ensemble member.

    Attributes:
        date:           The assimilation date at which injection occurred.
        injected:       Dict of variable_name → injected value (after clamping).
        skipped_none:   Variables that were None in the StateVector (no injection needed).
        skipped_nan:    Variables where the analysis value was NaN (EnKF produced
                        no update for this variable — forecast retained).
        clamped:        Variables whose values were clamped to physical bounds.
                        Format: {variable: (original_value, clamped_value)}.
        errors:         Variables for which set_variable() raised an exception.
                        These are silently skipped — the PCSE engine retains its
                        internal value for the affected variable.
        read_back:      get_variable() values read back after injection (verification).
                        None entries mean PCSE did not expose the variable for reading.
    """
    date: Optional[datetime.date] = field(default=None)
    injected:     dict[str, float]              = field(default_factory=dict)
    skipped_none: list[str]                     = field(default_factory=list)
    skipped_nan:  list[str]                     = field(default_factory=list)
    clamped:      dict[str, tuple[float, float]] = field(default_factory=dict)
    errors:       dict[str, str]                = field(default_factory=dict)
    read_back:    dict[str, Optional[float]]    = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """True if at least one variable was injected without errors."""
        return bool(self.injected) and not self.errors

    @property
    def injection_count(self) -> int:
        """Number of variables successfully injected."""
        return len(self.injected)

    def summary(self) -> str:
        """Human-readable single-line summary for logging."""
        return (
            f"InjectionResult[date={self.date} "
            f"injected={list(self.injected.keys())} "
            f"skipped_none={self.skipped_none} "
            f"skipped_nan={self.skipped_nan} "
            f"clamped={list(self.clamped.keys())} "
            f"errors={list(self.errors.keys())}]"
        )


class StateUpdater:
    """Injects EnKF-corrected states into running WOFOST ensemble members.

    Stateless — holds no PCSE engine references.  One StateUpdater instance
    can serve all N ensemble members (just call inject() once per member).

    Args:
        inject_dvs: Allow injection of DVS (development stage).
                    Default False — DVS injection can irreversibly jump
                    phenological stage and disrupt thermal-unit integration.
                    Enable only in controlled experiments.
        inject_rd:  Allow injection of RD (root depth).
                    Default False — root depth is bounded below by its own
                    growth rate; back-setting below current depth creates
                    negative growth rates in the waterbalance.
        verify:     If True (default), call get_variable() after each
                    set_variable() to confirm the value was accepted by PCSE.
                    Useful for debugging; adds ~N get_variable() calls per step.

    Example:
        updater = StateUpdater()
        for member, x_a_col in zip(manager.members, X_a.T):
            corrected_sv = StateVector.from_numpy(x_a_col, date=assimilation_date)
            result = updater.inject(member.wofost, corrected_sv)
            logger.debug(result.summary())
    """

    def __init__(
        self,
        *,
        inject_dvs: bool = False,
        inject_rd:  bool = False,
        verify:     bool = True,
    ) -> None:
        self.inject_dvs = inject_dvs
        self.inject_rd  = inject_rd
        self.verify     = verify

        # Build the effective key map (optionally extend with dvs / rd)
        self._key_map: dict[str, str] = dict(PCSE_KEY_MAP)
        if inject_dvs:
            self._key_map["dvs"] = "DVS"
        if inject_rd:
            self._key_map["rd"] = "RD"

    # ── Core injection method ─────────────────────────────────────────────

    def inject(
        self,
        wofost: "Wofost72_WLP_FD",
        state: object,
        *,
        variables: Optional[list[str]] = None,
    ) -> InjectionResult:
        """Inject a corrected StateVector into a running WOFOST instance.

        Processes each variable in the effective key map:
          1. Skip if the StateVector value is None (no EnKF update produced).
          2. Skip if the StateVector value is NaN (analysis diverged for this var).
          3. Clamp to physical bounds if needed (log a warning).
          4. Call wofost.set_variable(pcse_key, value).
          5. Optionally read back with wofost.get_variable() for verification.

        Args:
            wofost:    Running Wofost72_WLP_FD PCSE engine instance.
            state:     StateVector (or any object with lowercase float attributes
                       matching STATE_VARIABLES — duck-typed for testability).
            variables: Optional whitelist of variable names to inject.
                       If None, all variables in the effective key map are attempted.
                       Example: ['lai', 'sm'] to inject only LAI and SM.

        Returns:
            InjectionResult with full record of what was injected, skipped,
            clamped, and any errors.
        """
        import math

        result = InjectionResult(
            date=getattr(state, "date", None)
        )

        # Determine which variables to attempt
        target_map = {
            k: v for k, v in self._key_map.items()
            if variables is None or k in variables
        }

        for sv_key, pcse_key in target_map.items():
            raw_value = getattr(state, sv_key, None)

            # ── Step 1: skip None ─────────────────────────────────────────
            if raw_value is None:
                result.skipped_none.append(sv_key)
                continue

            # ── Step 2: skip NaN ──────────────────────────────────────────
            if isinstance(raw_value, float) and math.isnan(raw_value):
                result.skipped_nan.append(sv_key)
                continue

            value = float(raw_value)

            # ── Step 3: clamp to physical bounds ──────────────────────────
            bounds = _BOUNDS.get(sv_key)
            if bounds is not None:
                lo, hi = bounds
                if value < lo or value > hi:
                    clamped = max(lo, min(hi, value))
                    logger.warning(
                        "StateUpdater: %s=%+.4f out of bounds [%.4f, %.4f] → clamped to %.4f",
                        sv_key, value, lo, hi, clamped,
                    )
                    result.clamped[sv_key] = (value, clamped)
                    value = clamped

            # ── Step 4: inject via set_variable() ────────────────────────
            try:
                wofost.set_variable(pcse_key, value)
                result.injected[sv_key] = value
                logger.debug(
                    "StateUpdater.inject: %s(%s)=%.4f → accepted",
                    pcse_key, sv_key, value,
                )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                result.errors[sv_key] = err_msg
                logger.error(
                    "StateUpdater.inject: %s(%s)=%.4f FAILED — %s",
                    pcse_key, sv_key, value, err_msg,
                )
                continue

            # ── Step 5: verify via get_variable() ────────────────────────
            if self.verify:
                try:
                    read_back = wofost.get_variable(pcse_key)
                    result.read_back[sv_key] = float(read_back) if read_back is not None else None
                    if read_back is not None:
                        diff = abs(float(read_back) - value)
                        if diff > 1e-6:
                            logger.warning(
                                "StateUpdater: %s read-back mismatch: set=%.6f got=%.6f (Δ=%.6f)",
                                sv_key, value, float(read_back), diff,
                            )
                except Exception as exc:
                    result.read_back[sv_key] = None
                    logger.debug(
                        "StateUpdater: get_variable(%s) after inject raised %s",
                        pcse_key, exc,
                    )

        logger.info(
            "StateUpdater.inject: date=%s injected=%d skipped=%d errors=%d",
            result.date,
            result.injection_count,
            len(result.skipped_none) + len(result.skipped_nan),
            len(result.errors),
        )
        return result

    # ── Ensemble-level injection ──────────────────────────────────────────

    def inject_ensemble(
        self,
        members: list,
        analysis_states: list,
        *,
        variables: Optional[list[str]] = None,
    ) -> list[InjectionResult]:
        """Inject corrected states into all ensemble members at once.

        This is the primary interface for the EnKF analysis step.  After
        ``enkf_update()`` produces X_a (the analysis ensemble matrix), call
        ``inject_ensemble()`` to route each column of X_a back into the
        corresponding WOFOST instance.

        Args:
            members:         List of EnsembleMember objects (must have a
                             ``.wofost`` attribute that is a Wofost72_WLP_FD instance).
            analysis_states: List of StateVector objects (one per member),
                             in the same order as ``members``.
                             Typically built via::

                                 [StateVector.from_numpy(X_a[:, i], date=t)
                                  for i in range(N)]

            variables:       Optional whitelist of variable names to inject.
                             Passed through to inject() for each member.

        Returns:
            List of InjectionResult objects, one per member, in member order.

        Raises:
            ValueError: If ``len(members) != len(analysis_states)``.

        Example:
            X_a, _, _ = enkf_update(X_f, y_obs, R)
            states = [StateVector.from_numpy(X_a[:, i], date=today)
                      for i in range(N)]
            results = updater.inject_ensemble(manager.members, states)
        """
        if len(members) != len(analysis_states):
            raise ValueError(
                f"inject_ensemble: len(members)={len(members)} != "
                f"len(analysis_states)={len(analysis_states)}."
            )

        results: list[InjectionResult] = []
        for member, state in zip(members, analysis_states):
            result = self.inject(member.wofost, state, variables=variables)
            results.append(result)

        total_injected = sum(r.injection_count for r in results)
        total_errors   = sum(len(r.errors)     for r in results)
        logger.info(
            "StateUpdater.inject_ensemble: %d members, %d total injections, %d errors",
            len(members), total_injected, total_errors,
        )
        return results

    # ── Read-only state snapshot ──────────────────────────────────────────

    @staticmethod
    def read_state(wofost: "Wofost72_WLP_FD") -> dict[str, Optional[float]]:
        """Read the current WOFOST state for all injectable variables.

        Useful for verifying the PCSE state before and after injection without
        constructing a full StateVector.

        Args:
            wofost: Running Wofost72_WLP_FD instance.

        Returns:
            Dict of {sv_key: current_value_or_None} for all variables in
            PCSE_KEY_MAP plus DVS and RD (always read regardless of inject flags).
        """
        all_keys = {**PCSE_KEY_MAP, "dvs": "DVS", "rd": "RD"}
        state: dict[str, Optional[float]] = {}
        for sv_key, pcse_key in all_keys.items():
            raw = wofost.get_variable(pcse_key)
            state[sv_key] = float(raw) if raw is not None else None
        return state
