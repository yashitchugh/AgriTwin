# backend/app/twin/__init__.py
"""
backend/app/twin — Digital Twin State Abstractions
====================================================

This package provides the state container layer for future AgriTwin Digital Twin
capabilities. It decouples the simulation engine outputs from any future data
assimilation, observation injection, or scenario management modules.

Current contents:
  field_state.py  — FieldState: virtual state of one agricultural field at one point in time.

Design principles:
  1. Pure data containers — no business logic, no DB access, no external calls.
  2. All fields optional — a FieldState can be partially populated (e.g. only
     soil moisture from a satellite pass, before a simulation has run).
  3. Factory methods — FieldState.from_daily_output() and from_simulation()
     are the only correct ways to construct a FieldState from persistence layer objects.
  4. Future modules (EnKF, scenario engine, etc.) consume FieldState, not raw
     DailyOutput ORM objects.  This decoupling means the assimilation layer never
     needs to know the database schema.

NOT implemented here:
  - EnKF (Ensemble Kalman Filter)
  - Satellite image ingestion
  - Scenario / what-if engine
  - Optimization or recommendation logic
  - Machine learning
"""
from backend.app.twin.field_state import FieldState

__all__ = ["FieldState"]
