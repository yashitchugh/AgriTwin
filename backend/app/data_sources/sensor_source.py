"""
backend/app/data_sources/sensor_source.py — Abstract IoT Sensor Source
========================================================================

Defines the SensorSource interface for future in-situ sensor observation inputs.
This is a STUB — no IoT or sensor pipeline is implemented here.

Purpose:
  Establish the contract so that when field sensors (soil moisture probes,
  rain gauges, leaf area meters, weather stations) are integrated, the
  rest of the codebase (FieldState, EnKF, reporting) already knows the
  shape of sensor data.

NOT implemented here:
  - MQTT / AMQP message brokers
  - LoRaWAN / NB-IoT network servers
  - Sensor calibration and quality control
  - Real-time streaming pipelines
  - EnKF integration with sensor noise models
  - Machine learning anomaly detection on sensor time series

When to implement:
  Create a concrete SensorSource subclass for each sensor backend.
  The class must only implement get_observations() — all other behaviour
  (caching, aggregation, QC) belongs inside the concrete class.

Example future implementation:
    class SoilProbeSource(SensorSource):
        def get_observations(self, field_id, start_date, end_date, variables):
            # Query MQTT broker or local SQLite sensor log
            # Return list of SensorObservation
            ...
"""

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class SensorObservation:
    """A single in-situ sensor measurement for one field at one timestamp.

    Attributes:
        timestamp:   UTC datetime of the measurement.
        variable:    WOFOST-compatible variable name (e.g. 'SM', 'TRA', 'RD').
                     May also be a sensor-specific key (e.g. 'soil_temp_5cm').
        value:       Measured value in SI / WOFOST units.
        uncertainty: Measurement uncertainty (1-sigma).  Required for EnKF R matrix.
        depth_cm:    Sensor installation depth below surface [cm].
                     Relevant for soil moisture and temperature probes.
        sensor_id:   Unique sensor identifier (hardware serial number or label).
        field_id:    UUID of the Field this sensor belongs to.
        quality_flag: 0 = good, 1 = suspect, 2 = bad.  Consumers should skip
                      observations with quality_flag >= 2.
    """
    timestamp: datetime.datetime
    variable: str
    value: Optional[float] = None
    uncertainty: Optional[float] = None
    depth_cm: Optional[float] = None
    sensor_id: Optional[str] = None
    field_id: Optional[object] = None     # uuid.UUID when populated
    quality_flag: int = 0                  # 0=good, 1=suspect, 2=bad


class SensorSource(ABC):
    """Abstract base class for in-situ field sensor observation sources.

    All concrete sensor backends must subclass this and implement
    get_observations().  The assimilation module and digital twin services
    consume SensorSource, not any specific sensor brand or protocol.

    This enables:
      - Swapping between MQTT, REST, and CSV sensor backends
      - Using synthetic sensor data in unit tests
      - Mixing multiple sensor networks for the same field

    Current implementations:
        None — this is a stub for future implementation.

    Future implementations:
        MqttSensorSource      — subscribe to MQTT broker for real-time data
        CsvSensorSource       — read historical sensor CSV logs
        SyntheticSensorSource — deterministic synthetic readings for testing
    """

    @abstractmethod
    def get_observations(
        self,
        latitude: float,
        longitude: float,
        start_date: datetime.date,
        end_date: datetime.date,
        *,
        variables: Optional[list[str]] = None,
        field_id: Optional[object] = None,
    ) -> list[SensorObservation]:
        """Retrieve sensor observations for a field over a time window.

        Args:
            latitude:    Field centroid latitude [decimal degrees WGS84].
                         Used to locate the nearest sensor network node.
            longitude:   Field centroid longitude [decimal degrees WGS84].
            start_date:  Start of the observation window (inclusive).
            end_date:    End of the observation window (inclusive).
            variables:   List of variable names to retrieve.
                         None = all available variables from this source.
            field_id:    Optional UUID of the Field.  When provided, the
                         implementation can use field-sensor registration
                         tables to look up the exact sensor IDs.

        Returns:
            List of SensorObservation records sorted by timestamp ASC.
            May be empty if no sensors are registered or the window has
            no data.
        """
        ...

    def get_source_name(self) -> str:
        """Human-readable source name (e.g. 'MQTT broker @ broker.example.com')."""
        return self.__class__.__name__
