import asyncio
import threading
import time
import unittest
from dataclasses import fields
from unittest.mock import patch

import adamas_robot_sdk
from adamas_robot_sdk import (
    AdamasRobotAdapter,
    ControlFrame,
    ControlInput,
    ControlSignal,
    ControlState,
    DummyRobotAdapter,
    RobotConfig,
    RobotConnectionError,
)
from adamas_robot_sdk._platform import PlatformError


CONFIG = RobotConfig(
    fleet_id="fleet_01",
    fleet_key="afk_test",
    robot_id="robot_01",
    name="Robot 01",
)
CONTROL_FRAME = ControlFrame(
    device_profile_id="keyboard-mouse",
    state=ControlState(axes={"pair.ad": 0.0}),
)


class PublicApiTests(unittest.TestCase):
    def test_public_surface_uses_stream_publishers(self) -> None:
        self.assertTrue(issubclass(DummyRobotAdapter, AdamasRobotAdapter))
        for removed in (
            "SensorDescriptor",
            "SensorReading",
            "SensorKind",
            "SensorEncoding",
            "SensorDelivery",
            "StereoVideo",
            "StereoView",
        ):
            self.assertFalse(hasattr(adamas_robot_sdk, removed))
        self.assertTrue(hasattr(AdamasRobotAdapter, "add_telemetry"))
        self.assertTrue(hasattr(AdamasRobotAdapter, "add_video"))
        self.assertFalse(hasattr(AdamasRobotAdapter, "read_sensors"))

    def test_public_control_frame_keeps_generic_state_without_wire_metadata(self) -> None:
        self.assertEqual(
            [field.name for field in fields(ControlFrame)],
            ["device_profile_id", "state"],
        )
        self.assertEqual(
            [field.name for field in fields(ControlState)],
            ["poses", "axes", "buttons", "joints"],
        )

    def test_update_runs_control_hook_on_callers_thread(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = RecordingAdapter(CONFIG)
        adapter._runtime.controls.append(CONTROL_FRAME)
        caller_thread = threading.get_ident()

        adapter.update()

        self.assertEqual(adapter.control_threads, [caller_thread])
        self.assertEqual(adapter.controls, [CONTROL_FRAME])
        self.assertEqual(
            [stream.to_message() for stream in adapter._runtime.robot_state[1]],
            [{"key": "state", "kind": "telemetry"}],
        )

    def test_publishers_validate_and_submit_latest_values(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = RecordingAdapter(CONFIG)
        adapter.update()

        adapter.state.publish({"ready": True})

        self.assertEqual(len(adapter._runtime.submitted), 1)
        self.assertEqual(adapter._runtime.submitted[0].stream.key, "state")
        with self.assertRaisesRegex(TypeError, "valid JSON"):
            adapter.state.publish({"invalid": object()})

    def test_streams_are_unique_and_frozen_after_start(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = RecordingAdapter(CONFIG)
        with self.assertRaisesRegex(ValueError, "Duplicate stream key"):
            adapter.add_video("state")

        adapter.update()

        with self.assertRaisesRegex(RuntimeError, "before the first update"):
            adapter.add_video("late_camera")

    def test_disconnected_robot_is_not_exposed_to_fleet(self) -> None:
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

    def test_update_does_not_wait_for_slow_fleet_connection(self) -> None:
        SlowSession.started.clear()
        with patch("adamas_robot_sdk._runtime._NetworkSession", SlowSession):
            adapter = RecordingAdapter(CONFIG)
            started_at = time.monotonic()

            adapter.update()
            elapsed = time.monotonic() - started_at
            self.assertTrue(SlowSession.started.wait(1.0))
            adapter.close(timeout=1.0)

        self.assertLess(elapsed, 0.05)

    def test_duplicate_robot_id_raises_human_readable_error(self) -> None:
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

    def test_account_connection_limit_raises_human_readable_error(self) -> None:
        QuotaSession.started.clear()
        QuotaSession.attempts = 0
        with patch("adamas_robot_sdk._runtime._NetworkSession", QuotaSession):
            adapter = RecordingAdapter(CONFIG)
            try:
                adapter.update()
                self.assertTrue(QuotaSession.started.wait(1.0))
                deadline = time.monotonic() + 1.0
                while adapter.connection_error is None and time.monotonic() < deadline:
                    time.sleep(0.01)

                with self.assertRaisesRegex(
                    RobotConnectionError,
                    "reached its concurrent robot connection limit of 3",
                ):
                    adapter.update()
            finally:
                adapter.close(timeout=1.0)

        self.assertEqual(QuotaSession.attempts, 1)

    def test_included_dummy_publishes_telemetry_and_video(self) -> None:
        with patch("adamas_robot_sdk.adapter._RobotRuntime", FakeRuntime):
            adapter = DummyRobotAdapter(CONFIG)

        adapter.update()

        self.assertEqual(
            {sample.stream.key for sample in adapter._runtime.submitted},
            {"front_camera", "robot_state"},
        )


class RecordingAdapter(AdamasRobotAdapter):
    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.state = self.add_telemetry("state")
        self.robot_connected = True
        self.controls: list[ControlInput] = []
        self.control_threads: list[int] = []

    def is_robot_connected(self) -> bool:
        return self.robot_connected

    def handle_control(self, control: ControlInput) -> None:
        self.controls.append(control)
        self.control_threads.append(threading.get_ident())


class FakeRuntime:
    def __init__(self, _config: RobotConfig) -> None:
        self.fleet_connected = True
        self.last_error = None
        self.fatal_error = None
        self.controls: list[ControlInput] = []
        self.submitted = []
        self.robot_state = None

    def start(self) -> None:
        pass

    def close(self, _timeout: float | None = None) -> None:
        pass

    def set_robot_state(self, connected, streams) -> None:
        self.robot_state = (connected, streams)

    def drain_controls(self) -> list[ControlInput]:
        controls, self.controls = self.controls, []
        return controls

    def clear_controls(self) -> None:
        self.controls = []

    def submit_sample(self, sample) -> None:
        self.submitted.append(sample)


class SlowSession:
    started = threading.Event()

    def __init__(self, _config, streams, _receive_control) -> None:
        self.streams = streams
        self.failed_error = None

    async def connect(self) -> None:
        self.started.set()
        await asyncio.sleep(0.2)

    async def close(self) -> None:
        pass

    async def publish(self, _sample) -> None:
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


class QuotaSession(SlowSession):
    attempts = 0

    async def connect(self) -> None:
        type(self).attempts += 1
        self.started.set()
        raise PlatformError(
            402,
            "This account has reached its concurrent robot connection limit of 3. "
            "Disconnect another robot or change the account's robot connection plan.",
        )


if __name__ == "__main__":
    unittest.main()
