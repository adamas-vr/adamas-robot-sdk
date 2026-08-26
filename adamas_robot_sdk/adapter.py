from abc import ABC, abstractmethod

from ._runtime import _RobotRuntime
from .config import RobotConfig
from .protocol import (
    ControlInput,
    ControlSignal,
    _StreamDescriptor,
    _StreamKind,
)
from .streams import TelemetryPublisher, VideoPublisher


class RobotConnectionError(RuntimeError):
    """A terminal fleet connection error requiring user action."""


class AdamasRobotAdapter(ABC):
    """Robot-facing integration point driven by the host application's loop."""

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self._runtime = _RobotRuntime(config)
        self._streams: dict[str, _StreamDescriptor] = {}
        self._registration_closed = False
        self._robot_was_connected = False
        self._closed = False

    @abstractmethod
    def is_robot_connected(self) -> bool:
        """Return whether the underlying robot or simulation is available."""

    @abstractmethod
    def handle_control(self, control: ControlInput) -> None:
        """Translate and apply one Adamas control input on the caller's thread."""

    def add_telemetry(self, key: str) -> TelemetryPublisher:
        """Register one JSON telemetry stream before the first update."""
        stream = self._register_stream(key, _StreamKind.TELEMETRY)
        return TelemetryPublisher(stream, self._runtime.submit_sample)

    def add_video(self, key: str) -> VideoPublisher:
        """Register one video stream before the first update."""
        stream = self._register_stream(key, _StreamKind.VIDEO)
        return VideoPublisher(stream, self._runtime.submit_sample)

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
        self._registration_closed = True
        self._raise_fatal_connection_error()
        self._runtime.start()

        robot_connected = bool(self.is_robot_connected())
        streams = tuple(self._streams.values()) if robot_connected else ()
        self._runtime.set_robot_state(robot_connected, streams)
        self._raise_fatal_connection_error()

        if robot_connected:
            for control in self._runtime.drain_controls():
                self.handle_control(control)
        else:
            self._runtime.clear_controls()
            if self._robot_was_connected:
                self.handle_control(ControlSignal("stop"))
        self._robot_was_connected = robot_connected

    def _register_stream(
        self, key: str, kind: _StreamKind
    ) -> _StreamDescriptor:
        if self._registration_closed:
            raise RuntimeError("Streams must be registered before the first update")
        if key in self._streams:
            raise ValueError(f'Duplicate stream key: "{key}"')
        if len(self._streams) >= 32:
            raise ValueError("A robot can expose up to 32 streams")
        stream = _StreamDescriptor(key, kind)
        self._streams[key] = stream
        return stream

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
