"""initial_schema

Revision ID: ca28c2635b9d
Revises:
Create Date: 2026-06-09 22:15:17.628912

This is the baseline migration that creates the complete AgriTwin schema.

Tables created (in FK dependency order):
  1. farms            — top-level organisational unit
  2. fields           — GPS-located plots belonging to a farm
  3. simulation_runs  — one WOFOST execution per field/season
  4. daily_outputs    — daily WOFOST state for a simulation run

Cascade relationships:
  Farm  --< Field         (farm deleted → fields deleted)
  Field --< SimulationRun (field deleted → runs deleted)
  SimulationRun --< DailyOutput (run deleted → daily rows deleted)

SQLite compatibility:
  `render_as_batch=True` is set in env.py, enabling Alembic to simulate
  ALTER TABLE on SQLite by recreating tables.  All migration ops use
  standard Alembic ops and are compatible with both SQLite and PostgreSQL.

To apply to a fresh database:
    alembic upgrade head

To verify current revision:
    alembic current
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = "ca28c2635b9d"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the full AgriTwin schema from scratch."""

    # ── 1. farms ──────────────────────────────────────────────────────────────
    op.create_table(
        "farms",
        sa.Column("id",           sa.CHAR(32),     nullable=False,  primary_key=True),
        sa.Column("name",         sa.String(256),  nullable=False),
        sa.Column("description",  sa.Text(),       nullable=True),
        sa.Column("owner_name",   sa.String(256),  nullable=True),
        sa.Column("country_code", sa.String(3),    nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── 2. fields ─────────────────────────────────────────────────────────────
    op.create_table(
        "fields",
        sa.Column("id",           sa.CHAR(32),     nullable=False,  primary_key=True),
        sa.Column("farm_id",      sa.CHAR(32),     nullable=False),
        sa.Column("name",         sa.String(256),  nullable=False),
        sa.Column("description",  sa.Text(),       nullable=True),
        sa.Column("latitude",     sa.Float(),      nullable=False),
        sa.Column("longitude",    sa.Float(),      nullable=False),
        sa.Column("area_ha",      sa.Float(),      nullable=True),
        sa.Column("elevation_m",  sa.Float(),      nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_fields_farm_id",  "fields", ["farm_id"])
    op.create_index("ix_fields_lat_lon",  "fields", ["latitude", "longitude"])

    # ── 3. simulation_runs ────────────────────────────────────────────────────
    op.create_table(
        "simulation_runs",
        sa.Column("id",               sa.CHAR(32),      nullable=False, primary_key=True),
        sa.Column("field_id",         sa.CHAR(32),      nullable=True),
        sa.Column("run_type",         sa.String(64),    nullable=False),
        sa.Column("status",           sa.String(32),    nullable=False),
        sa.Column("error_message",    sa.Text(),        nullable=True),
        sa.Column("model_name",       sa.String(128),   nullable=False),
        sa.Column("model_version",    sa.String(64),    nullable=False),
        sa.Column("latitude",         sa.Float(),       nullable=False),
        sa.Column("longitude",        sa.Float(),       nullable=False),
        sa.Column("crop",             sa.String(64),    nullable=False),
        sa.Column("variety",          sa.String(128),   nullable=False),
        sa.Column("sowing_date",      sa.Date(),        nullable=False),
        sa.Column("harvest_date",     sa.Date(),        nullable=True),
        sa.Column("use_real_weather", sa.Boolean(),     nullable=False),
        sa.Column("use_real_soil",    sa.Boolean(),     nullable=False),
        sa.Column("yield_kg_ha",      sa.Float(),       nullable=True),
        sa.Column("peak_lai",         sa.Float(),       nullable=True),
        sa.Column("harvest_index",    sa.Float(),       nullable=True),
        sa.Column("final_tagp",       sa.Float(),       nullable=True),
        sa.Column("final_twso",       sa.Float(),       nullable=True),
        sa.Column("total_days",       sa.Integer(),     nullable=True),
        sa.Column("dos",              sa.Date(),        nullable=True),
        sa.Column("doe",              sa.Date(),        nullable=True),
        sa.Column("doa",              sa.Date(),        nullable=True),
        sa.Column("dom",              sa.Date(),        nullable=True),
        sa.Column("doh",              sa.Date(),        nullable=True),
        sa.Column("request_payload",  sa.JSON(),        nullable=True),
        sa.Column("metrics_payload",  sa.JSON(),        nullable=True),
        sa.Column("summary_payload",  sa.JSON(),        nullable=True),
        sa.Column("weather_snapshot", sa.JSON(),        nullable=True),
        sa.Column("soil_snapshot",    sa.JSON(),        nullable=True),
        sa.Column("warnings",         sa.JSON(),        nullable=True),
        sa.Column("notes",            sa.Text(),        nullable=True),
        sa.Column("created_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["fields.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_simrun_field_sowing",    "simulation_runs", ["field_id", "sowing_date"])
    op.create_index("ix_simrun_crop_variety",     "simulation_runs", ["crop", "variety"])
    op.create_index("ix_simrun_lat_lon",          "simulation_runs", ["latitude", "longitude"])
    op.create_index("ix_simrun_status",           "simulation_runs", ["status"])
    op.create_index("ix_simulation_runs_field_id","simulation_runs", ["field_id"])

    # ── 4. daily_outputs ──────────────────────────────────────────────────────
    op.create_table(
        "daily_outputs",
        sa.Column("id",                 sa.Integer(),  nullable=False, autoincrement=True, primary_key=True),
        sa.Column("simulation_run_id",  sa.CHAR(32),   nullable=False),
        sa.Column("date",               sa.Date(),     nullable=False),
        sa.Column("dvs",                sa.Float(),    nullable=True),
        sa.Column("lai",                sa.Float(),    nullable=True),
        sa.Column("tagp",               sa.Float(),    nullable=True),
        sa.Column("twso",               sa.Float(),    nullable=True),
        sa.Column("twlv",               sa.Float(),    nullable=True),
        sa.Column("twst",               sa.Float(),    nullable=True),
        sa.Column("twrt",               sa.Float(),    nullable=True),
        sa.Column("sm",                 sa.Float(),    nullable=True),
        sa.Column("rftra",              sa.Float(),    nullable=True),
        sa.Column("tra",                sa.Float(),    nullable=True),
        sa.Column("evs",                sa.Float(),    nullable=True),
        sa.Column("rd",                 sa.Float(),    nullable=True),
        sa.ForeignKeyConstraint(["simulation_run_id"], ["simulation_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_daily_run_date", "daily_outputs", ["simulation_run_id", "date"])


def downgrade() -> None:
    """Drop all AgriTwin tables in reverse dependency order."""
    op.drop_table("daily_outputs")
    op.drop_table("simulation_runs")
    op.drop_table("fields")
    op.drop_table("farms")
