import asyncio
import threading
import time
import unittest
from collections.abc import Iterable, Sequence
from unittest.mock import patch

import adamas_robot_sdk
from adamas_robot_sdk import (
    AdamasRobotAdapter,
    ControlInput,
    ControlSignal,
    DummyRobotAdapter,
    RobotConfig,
    RobotConnectionError,
    SensorDescriptor,
    SensorKind,
    SensorReading,
    ControlState,
    ControlFrame,
)
from adamas_robot_sdk._platform import PlatformError


CONFIG = RobotConfig(
    fleet_id="fleet_01",
    fleet_key="afk_test",
    robot_id="robot_01",
    name="Robot 01",
)
STATE_SENSOR = SensorDescriptor("state", "State", SensorKind.DATA)
CONTROL_FRAME = ControlFrame(
    sequence=1,
    captured_at_us=2_000,
    source_id="keyboard",
    device_profile_id="keyboard-mouse",
    state=ControlState(axes={"pair.ad": 0.0}),
)


class PublicApiTests(unittest.TestCase):
    def test_public_surface_has_one_adapter_integration_path(self) -> None:
        self.assertTrue(issubclass(DummyRobotAdapter, AdamasRobotAdapter))
        self.assertFalse(hasattr(adamas_robot_sdk, "RobotClient"))
        self.assertFalse(hasattr(adamas_robot_sdk, "AsyncRobotClient"))
        self.assertFalse(hasattr(adamas_robot_sdk, "run_robot"))
        self.assertFalse(hasattr(AdamasRobotAdapter, "publish"))
        self.assertFalse(hasattr(CONFIG, "capabilities"))
        self.assertFalse(hasattr(CONFIG, "kind"))
        self.assertFalse(hasattr(CONFIG, "tls_ca_file"))

    def test_update_runs_robot_hooks_on_the_callers_thread(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = RecordingAdapter(CONFIG)
        adapter._runtime.controls.append(CONTROL_FRAME)
        caller_thread = threading.get_ident()

        adapter.update()

        self.assertEqual(adapter.control_threads, [caller_thread])
        self.assertEqual(adapter.sensor_threads, [caller_thread])
        self.assertEqual(adapter._runtime.submitted[0][0].sensor, STATE_SENSOR)

    def test_disconnected_robot_is_not_exposed_to_the_fleet(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = RecordingAdapter(CONFIG)
        adapter.update()
        adapter.robot_connected = False

        adapter.update()

        self.assertEqual(adapter._runtime.robot_state, (False, ()))
        self.assertIsInstance(adapter.controls[-1], ControlSignal)
        self.assertEqual(adapter.controls[-1].type, "stop")

    def test_update_does_not_wait_for_network_when_robot_is_disconnected(self) -> None:
        adapter = RecordingAdapter(CONFIG)
        adapter.robot_connected = False
        started_at = time.monotonic()

        adapter.update()
        elapsed = time.monotonic() - started_at
        adapter.close(timeout=1.0)

        self.assertLess(elapsed, 0.05)

    def test_update_does_not_wait_for_a_slow_fleet_connection(self) -> None:
        SlowSession.started.clear()
        with patch("adamas_robot_sdk._runtime._NetworkSession", SlowSession):
            adapter = RecordingAdapter(CONFIG)
            started_at = time.monotonic()

            adapter.update()
            elapsed = time.monotonic() - started_at
            self.assertTrue(SlowSession.started.wait(1.0))
            adapter.close(timeout=1.0)

        self.assertLess(elapsed, 0.05)

    def test_duplicate_robot_id_raises_a_human_readable_error(self) -> None:
        ConflictSession.started.clear()
        ConflictSession.attempts = 0
        with patch("adamas_robot_sdk._runtime._NetworkSession", ConflictSession):
            adapter = RecordingAdapter(CONFIG)
            try:
                adapter.update()
                self.assertTrue(ConflictSession.started.wait(1.0))
                deadline = time.monotonic() + 1.0
                while adapter.connection_error is None and time.monotonic() < deadline:
                    time.sleep(0.01)

                with self.assertRaisesRegex(
                    RobotConnectionError,
                    'Robot ID "robot_01" is already connected',
                ):
                    adapter.update()
            finally:
                adapter.close(timeout=1.0)

        self.assertEqual(ConflictSession.attempts, 1)

    def test_included_dummy_uses_structured_sensor_readings(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = DummyRobotAdapter(CONFIG)

        adapter.update()

        readings = adapter._runtime.submitted[0]
        self.assertEqual({reading.sensor for reading in readings}, set(adapter.sensors))


class RecordingAdapter(AdamasRobotAdapter):
    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.robot_connected = True
        self.controls: list[ControlInput] = []
        self.control_threads: list[int] = []
        self.sensor_threads: list[int] = []

    @property
    def sensors(self) -> Sequence[SensorDescriptor]:
        return (STATE_SENSOR,)

    def is_robot_connected(self) -> bool:
        return self.robot_connected

    def handle_control(self, control: ControlInput) -> None:
        self.controls.append(control)
        self.control_threads.append(threading.get_ident())

    def read_sensors(
        self, requested: Sequence[SensorDescriptor]
    ) -> Iterable[SensorReading]:
        self.sensor_threads.append(threading.get_ident())
        return (SensorReading(STATE_SENSOR, {"ready": True}),)


class FakeRuntime:
    def __init__(self, _config: RobotConfig) -> None:
        self.fleet_connected = True
        self.last_error = None
        self.fatal_error = None
        self.controls: list[ControlInput] = []
        self.submitted: list[tuple[SensorReading, ...]] = []
        self.robot_state: tuple[bool, tuple[SensorDescriptor, ...]] | None = None

    def start(self) -> None:
        pass

    def close(self, _timeout: float | None = None) -> None:
        pass

    def set_robot_state(
        self, connected: bool, sensors: tuple[SensorDescriptor, ...]
    ) -> None:
        self.robot_state = (connected, sensors)

    def drain_controls(self) -> list[ControlInput]:
        controls, self.controls = self.controls, []
        return controls

    def clear_controls(self) -> None:
        self.controls = []

    def submit_readings(self, readings: tuple[SensorReading, ...]) -> None:
        self.submitted.append(readings)


class SlowSession:
    started = threading.Event()

    def __init__(self, _config, sensors, _receive_control) -> None:
        self.sensors = sensors
        self.failed_error = None

    async def connect(self) -> None:
        self.started.set()
        await asyncio.sleep(0.2)

    async def close(self) -> None:
        pass

    async def set_sensors(self, sensors) -> None:
        self.sensors = sensors

    async def publish(self, _reading) -> None:
        pass


class ConflictSession(SlowSession):
    attempts = 0

    async def connect(self) -> None:
        type(self).attempts += 1
        self.started.set()
        raise PlatformError(
            409,
            'Robot ID "robot_01" is already connected. '
            "Stop the existing robot process or choose a different robot ID.",
        )


if __name__ == "__main__":
    unittest.main()
