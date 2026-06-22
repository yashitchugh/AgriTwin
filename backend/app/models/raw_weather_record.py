from backend.app.db.base import Base, TimestampMixin
from sqlalchemy import Column,Date, Float, ForeignKey, String, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid

class RawWeatherRecord(TimestampMixin, Base):
    """
    SQLAlchemy model representing raw 7-day weather and atmospheric snapshots
    harvested from NASA POWER for a given field and specific date.
    """

    __tablename__="raw_weather_records"

    #Identifiers and Foreign key
    id=Column(UUID(as_uuid=True),primary_key=True, default=uuid.uuid4, index=True)
    field_id=Column(UUID(as_uuid=True), nullable=False, index=True, comment="Foreign key referencing the specific agricultural field")

    #Temporal or spatial metadata
    date=Column(Date, nullable=False, index=True, comment="The specific calendar date for this weather snapshot")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    #Data Lineage Metadata
    source=Column(String(50),nullable=False, default="nasa_power",comment="API Source identifier")
    api_status=Column(String(20),nullable=False, default="success",comment="Ingestion quality tracking status: 'success', 'partial', or 'failed'")

    #core weather parameter
    #All metrics allow Nullable=True so your validator can capture missing variables safely.
    temperature_2m=Column(Float,nullable=True, comment="Mean temperature [°C]")
    temperature_min=Column(Float, nullable=True, comment="Min temperature [°C]")
    temperature_max=Column(Float, nullable=True, comment="Max temperature [°C]")
    precipitation = Column(
        Float, nullable=True, comment="Precipitation [mm/day]"
    )
    radiation = Column(
        Float, nullable=True, comment="Shortwave radiation [MJ/m²/day]"
    )
    wind_speed = Column(Float, nullable=True, comment="Wind speed [m/s]")
    vapor_pressure = Column(
        Float, nullable=True, comment="Calculated vapor pressure [hPa]"
    )
    toa_radiation = Column(
        Float, nullable=True, comment="TOA radiation [MJ/m²/day]"
    )

    #Raw response payload storage
    raw_payload= Column(JSONB, nullable=False,comment="Stores full original NASA POWER JSON response dictionary for historical data lineage")

    #Constraints & Indexes
    __table_args__=(
        # Idempotency Guard: Ensures a field cannot have duplicate raw data for the same date.
        UniqueConstraint(
            "field_id", "date",name="uq_field_raw_weather_date_snapshot"
        ),
    )

    def __repr__(self) -> str:
        return(
            f"<RawWeatherRecord(field_id={self.field_id},date={self.date},T2M={self.temperature_2m},API_Status='{self.api_status}')>"
        )