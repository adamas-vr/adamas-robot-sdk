import asyncio
import contextlib
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from ._platform import PlatformClient, PlatformError, RobotIdentity
from ._realtime import LiveKitTransport
from .config import RobotConfig
from .protocol import (
    ControlFrame,
    ControlInput,
    ControlSignal,
    SensorDescriptor,
    SensorKind,
    SensorReading,
    VideoFrame,
)


logger = logging.getLogger(__name__)
SDK_VERSION = "0.8.0"


class _NetworkSession:
    HEARTBEAT_SECONDS = 10.0

    def __init__(
        self,
        config: RobotConfig,
        sensors: tuple[SensorDescriptor, ...],
        receive_control: Callable[[ControlInput], None],
    ) -> None:
        identity = RobotIdentity(
            robot_id=config.robot_id,
            fleet_key=config.fleet_key,
            connection_id=str(uuid.uuid4()),
            fleet_id=config.fleet_id,
            name=config.name,
        )
        self.config = config
        self.identity = identity
        self.sensors = sensors
        self.platform = PlatformClient(config.platform_url, identity)
        self.transport = LiveKitTransport()
        self.transport.on_control(self._receive_control)
        self._deliver_control = receive_control
        self._tasks: list[asyncio.Task[None]] = []
        self._last_control_at = 0.0
        self._control_active = False
        self._stream_sequences: dict[str, int] = {}
        self.failed_error: Exception | None = None

    async def connect(self) -> None:
        response = await self.platform.connect(list(self.sensors), SDK_VERSION)
        try:
            await self.transport.connect(response["realtime"])
            await self.transport.publish_manifest(
                self.identity.robot_id,
                self.identity.connection_id,
                list(self.sensors),
            )
            await self.platform.heartbeat(list(self.sensors))
        except Exception:
            with contextlib.suppress(Exception):
                await self.platform.disconnect()
            raise

        self._tasks = [asyncio.create_task(self._heartbeat_loop())]
        if self.config.control_timeout_s is not None:
            self._tasks.append(asyncio.create_task(self._watchdog_loop()))

    async def close(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._control_active:
            self._control_active = False
            self._deliver_control(ControlSignal("stop"))
        with contextlib.suppress(Exception):
            await self.transport.disconnect()
        with contextlib.suppress(Exception):
            await self.platform.disconnect()

    async def set_sensors(self, sensors: tuple[SensorDescriptor, ...]) -> None:
        self.sensors = sensors
        await self.transport.publish_manifest(
            self.identity.robot_id,
            self.identity.connection_id,
            list(sensors),
        )
        await self.platform.heartbeat(list(sensors))

    async def publish(self, reading: SensorReading) -> None:
        if reading.sensor not in self.sensors:
            return
        if reading.sensor.kind is SensorKind.VIDEO:
            frame = reading.value
            if not isinstance(frame, VideoFrame):
                raise TypeError("Video sensors require VideoFrame values")
            await self.transport.publish_video_frame(reading.sensor.id, frame)
            return
        sequence = self._stream_sequences.get(reading.sensor.id, 0) + 1
        self._stream_sequences[reading.sensor.id] = sequence
        await self.transport.publish_stream_sample(
            self.identity.robot_id,
            self.identity.connection_id,
            reading.sensor,
            sequence,
            reading.value,
        )

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_SECONDS)
            try:
                await self.platform.heartbeat(list(self.sensors))
            except Exception as error:
                self.failed_error = error
                return

    async def _watchdog_loop(self) -> None:
        timeout = self.config.control_timeout_s
        if timeout is None:
            return
        while True:
            await asyncio.sleep(min(0.1, timeout))
            if (
                self._control_active
                and time.monotonic() - self._last_control_at > timeout
            ):
                self._control_active = False
                self._deliver_control(ControlSignal("stop"))

    def _receive_control(self, message: dict[str, Any], reliable: bool) -> None:
        try:
            if reliable:
                signal_type = message.get("type")
                if signal_type not in {"stop", "reset", "home"}:
                    return
                command: ControlInput = ControlSignal(signal_type)
                if signal_type == "stop":
                    self._control_active = False
            else:
                command = ControlFrame.from_message(message)
                self._last_control_at = time.monotonic()
                self._control_active = True
        except (KeyError, OverflowError, TypeError, ValueError):
            return
        self._deliver_control(command)


class _RobotRuntime:
    COMMAND_QUEUE_SIZE = 256
    MAX_PENDING_READINGS = 128

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self._controls: queue.Queue[ControlInput] = queue.Queue(
            maxsize=self.COMMAND_QUEUE_SIZE
        )
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._session: _NetworkSession | None = None
        self._close_requested = threading.Event()
        self._robot_connected = False
        self._sensors: tuple[SensorDescriptor, ...] = ()
        self._state_revision = 0
        self._fleet_connected = False
        self._last_error: Exception | None = None
        self._fatal_error: Exception | None = None
        self._pending_readings = 0

    @property
    def fleet_connected(self) -> bool:
        with self._lock:
            return self._fleet_connected

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._fatal_error or self._last_error

    @property
    def fatal_error(self) -> Exception | None:
        with self._lock:
            return self._fatal_error

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if self._fatal_error:
                return
            self._close_requested.clear()
            self._last_error = None
            self._thread = threading.Thread(
                target=self._thread_main,
                name=f"adamas-{self.config.robot_id}",
                daemon=True,
            )
            self._thread.start()

    def close(self, timeout: float | None = None) -> None:
        self._close_requested.set()
        with self._lock:
            loop = self._loop
            wake = self._wake
            thread = self._thread
        if loop and wake:
            loop.call_soon_threadsafe(wake.set)
        if (
            timeout is not None
            and thread
            and thread is not threading.current_thread()
        ):
            thread.join(timeout)

    def set_robot_state(
        self, connected: bool, sensors: tuple[SensorDescriptor, ...]
    ) -> None:
        with self._lock:
            changed = (
                connected != self._robot_connected or sensors != self._sensors
            )
            self._robot_connected = connected
            self._sensors = sensors
            if changed:
                self._state_revision += 1
            loop = self._loop
            wake = self._wake
        if changed and loop and wake:
            loop.call_soon_threadsafe(wake.set)

    def drain_controls(self) -> list[ControlInput]:
        controls: list[ControlInput] = []
        while True:
            try:
                controls.append(self._controls.get_nowait())
            except queue.Empty:
                return controls

    def clear_controls(self) -> None:
        self.drain_controls()

    def submit_readings(self, readings: tuple[SensorReading, ...]) -> None:
        for reading in readings:
            self._submit_reading(reading)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as error:
            logger.exception("Robot network runtime stopped")
            self._set_connection(False, error)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        with self._lock:
            self._loop = loop
            self._wake = wake

        retry_delay = 1.0
        try:
            while not self._close_requested.is_set():
                connected, sensors, revision = self._robot_state()
                session = self._session

                if not connected:
                    if session:
                        await session.close()
                        self._session = None
                    self._set_connection(False, None)
                    await self._pause(0.5, revision)
                    continue

                if not session:
                    candidate = _NetworkSession(
                        self.config, sensors, self._receive_control
                    )
                    try:
                        await candidate.connect()
                    except Exception as error:
                        with contextlib.suppress(Exception):
                            await candidate.close()
                        if isinstance(error, PlatformError) and error.status == 409:
                            self._set_fatal_error(error)
                            return
                        self._set_connection(False, error)
                        await self._pause(retry_delay, revision)
                        retry_delay = min(30.0, retry_delay * 2)
                        continue
                    self._session = candidate
                    self._set_connection(True, None)
                    retry_delay = 1.0
                    continue

                if session.failed_error:
                    error = session.failed_error
                    await session.close()
                    self._session = None
                    self._set_connection(False, error)
                    await self._pause(retry_delay, revision)
                    retry_delay = min(30.0, retry_delay * 2)
                    continue

                if sensors != session.sensors:
                    try:
                        await session.set_sensors(sensors)
                    except Exception as error:
                        session.failed_error = error
                        continue

                await self._pause(0.25, revision)
        finally:
            if self._session:
                await self._session.close()
                self._session = None
            self._set_connection(False, None)
            with self._lock:
                self._loop = None
                self._wake = None

    async def _pause(self, timeout: float, revision: int) -> None:
        wake = self._wake
        if not wake:
            await asyncio.sleep(timeout)
            return
        if self._state_changed(revision) or self._close_requested.is_set():
            return
        wake.clear()
        if self._state_changed(revision) or self._close_requested.is_set():
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(wake.wait(), timeout)

    async def _publish(self, reading: SensorReading) -> None:
        session = self._session
        if session and self.fleet_connected:
            await session.publish(reading)

    def _submit_reading(self, reading: SensorReading) -> bool:
        with self._lock:
            loop = self._loop
            if (
                not loop
                or not self._fleet_connected
                or self._pending_readings >= self.MAX_PENDING_READINGS
            ):
                return False
            self._pending_readings += 1
        future = asyncio.run_coroutine_threadsafe(self._publish(reading), loop)
        future.add_done_callback(self._reading_finished)
        return True

    def _reading_finished(self, future: Future[None]) -> None:
        with self._lock:
            self._pending_readings -= 1
        try:
            future.result()
        except Exception as error:
            logger.warning("Sensor delivery failed: %s", error)
            with self._lock:
                self._last_error = error

    def _receive_control(self, command: ControlInput) -> None:
        if self._controls.full():
            with contextlib.suppress(queue.Empty):
                self._controls.get_nowait()
        self._controls.put_nowait(command)

    def _robot_state(
        self,
    ) -> tuple[bool, tuple[SensorDescriptor, ...], int]:
        with self._lock:
            return self._robot_connected, self._sensors, self._state_revision

    def _state_changed(self, revision: int) -> bool:
        with self._lock:
            return revision != self._state_revision

    def _set_connection(
        self, connected: bool, error: Exception | None
    ) -> None:
        with self._lock:
            self._fleet_connected = connected
            self._last_error = error

    def _set_fatal_error(self, error: Exception) -> None:
        with self._lock:
            self._fleet_connected = False
            self._last_error = error
            self._fatal_error = error
