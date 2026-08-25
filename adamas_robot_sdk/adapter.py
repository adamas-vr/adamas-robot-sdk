import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from ._runtime import _RobotRuntime
from .config import RobotConfig
from .protocol import (
    ControlInput,
    ControlSignal,
    SensorDescriptor,
    SensorReading,
)


class RobotConnectionError(RuntimeError):
    """A terminal fleet connection error requiring user action."""


class AdamasRobotAdapter(ABC):
    """Robot-facing integration point driven by the host application's loop."""

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self._runtime = _RobotRuntime(config)
        self._next_sensor_at: dict[str, float] = {}
        self._robot_was_connected = False
        self._closed = False

    @property
    @abstractmethod
    def sensors(self) -> Sequence[SensorDescriptor]:
        """Sensors currently available from the robot runtime."""

    @abstractmethod
    def is_robot_connected(self) -> bool:
        """Return whether the underlying robot or simulation is available."""

    @abstractmethod
    def handle_control(self, control: ControlInput) -> None:
        """Translate and apply one Adamas control input on the caller's thread."""

    @abstractmethod
    def read_sensors(
        self, requested: Sequence[SensorDescriptor]
    ) -> Iterable[SensorReading]:
        """Read the requested sensors on the caller's thread."""

    @property
    def fleet_connected(self) -> bool:
        return self._runtime.fleet_connected

    @property
    def connection_error(self) -> Exception | None:
        return self._runtime.last_error

    def update(self) -> None:
        """Perform one non-blocking SDK update from the host's existing loop."""
        if self._closed:
            raise RuntimeError("Cannot update a closed robot adapter")
        self._raise_fatal_connection_error()
        self._runtime.start()

        robot_connected = bool(self.is_robot_connected())
        sensors = validate_sensors(self.sensors) if robot_connected else ()
        self._runtime.set_robot_state(robot_connected, sensors)
        self._raise_fatal_connection_error()

        if robot_connected:
            for control in self._runtime.drain_controls():
                self.handle_control(control)
        else:
            self._runtime.clear_controls()
            if self._robot_was_connected:
                self.handle_control(ControlSignal("stop"))
        self._robot_was_connected = robot_connected

        if not robot_connected or not self._runtime.fleet_connected:
            return
        requested = self._sensors_due(sensors)
        if not requested:
            return
        readings = tuple(self.read_sensors(requested))
        validate_readings(readings, requested)
        self._runtime.submit_readings(readings)

    def _raise_fatal_connection_error(self) -> None:
        error = self._runtime.fatal_error
        if error is None:
            return
        detail = getattr(error, "detail", str(error))
        raise RobotConnectionError(detail) from error

    def close(self, timeout: float | None = None) -> None:
        """Request network shutdown, optionally waiting up to timeout seconds."""
        if self._closed:
            return
        self._closed = True
        if self._robot_was_connected:
            self.handle_control(ControlSignal("stop"))
        self._runtime.set_robot_state(False, ())
        self._runtime.close(timeout)

    def _sensors_due(
        self, sensors: tuple[SensorDescriptor, ...]
    ) -> tuple[SensorDescriptor, ...]:
        now = time.monotonic()
        active_ids = {sensor.id for sensor in sensors}
        self._next_sensor_at = {
            sensor_id: due_at
            for sensor_id, due_at in self._next_sensor_at.items()
            if sensor_id in active_ids
        }
        requested: list[SensorDescriptor] = []
        for sensor in sensors:
            if now < self._next_sensor_at.get(sensor.id, 0.0):
                continue
            requested.append(sensor)
            self._next_sensor_at[sensor.id] = now + 1.0 / sensor.rate_hz
        return tuple(requested)


def validate_sensors(
    sensors: Sequence[SensorDescriptor],
) -> tuple[SensorDescriptor, ...]:
    normalized = tuple(sensors)
    if len(normalized) > 32:
        raise ValueError("A robot can expose up to 32 sensors")
    ids = [sensor.id for sensor in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("Sensor IDs must be unique")
    return normalized


def validate_readings(
    readings: tuple[SensorReading, ...],
    requested: tuple[SensorDescriptor, ...],
) -> None:
    requested_set = set(requested)
    seen: set[SensorDescriptor] = set()
    for reading in readings:
        if reading.sensor not in requested_set:
            raise ValueError(f"Sensor was not requested: {reading.sensor.id}")
        if reading.sensor in seen:
            raise ValueError(f"Duplicate sensor reading: {reading.sensor.id}")
        seen.add(reading.sensor)
