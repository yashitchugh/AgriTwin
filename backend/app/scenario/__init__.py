"""
backend/app/scenario/__init__.py
=================================

Scenario Engine — Core Models Package
======================================

This package contains the data models for AgriTwin's Scenario Engine.
A "scenario" in AgriTwin is a structured what-if analysis:

  "Given a baseline simulation, what happens if I vary parameter X
   across values [v1, v2, ..., vN]?"

Typical questions a scenario answers:
  - "Which sowing date gives the highest yield for wheat in Delhi?"
  - "How many irrigations are needed to avoid water stress?"
  - "Does changing from variety apache to Winter_wheat_101 improve HI?"

Package contents:
  models/scenario_definition.py  — ORM: describes the scenario setup
  models/scenario_run.py         — ORM: one executed simulation within a scenario
  models/scenario_comparison.py  — ORM: derived comparison metrics across runs
  schemas/scenario.py            — Pydantic v2 schemas for API requests/responses

NOT implemented here:
  - Scenario execution service (service layer — future)
  - Parameter generators (SOWING_DATE grid, IRRIGATION schedule builder — future)
  - API routes (FastAPI router — future)
  - Celery tasks or async execution
  - Optimisation or recommendation logic
  - Machine learning
"""
