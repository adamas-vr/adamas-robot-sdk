import unittest
from unittest.mock import patch

from adamas_robot_sdk import ControlFrame, ControlInput, RobotConfig
from adamas_robot_sdk._runtime import _NetworkSession, _RobotRuntime
from adamas_robot_sdk.protocol import (
    _StreamDescriptor,
    _StreamKind,
    _TelemetrySample,
)


CONFIG = RobotConfig(
    fleet_id="fleet_01",
    fleet_key="afk_test",
    robot_id="robot_01",
    name="Robot 01",
)


def control_frame(sequence: int, source_id: str = "controller-a") -> dict:
    return {
        "version": 2,
        "sequence": sequence,
        "capturedAtUs": sequence * 1_000,
        "sourceId": source_id,
        "deviceProfileId": "gamepad",
        "state": {"axes": {"primary.x": 0.5}},
    }


class ControlFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controls: list[ControlInput] = []
        with patch("adamas_robot_sdk._runtime.LiveKitTransport", FakeTransport):
            self.session = _NetworkSession(CONFIG, (), self.controls.append)

    def test_rejects_duplicate_and_out_of_order_frames(self) -> None:
        for sequence in (2, 1, 2, 3):
            self.session._receive_control(control_frame(sequence), reliable=False)

        controls = [
            control for control in self.controls if isinstance(control, ControlFrame)
        ]
        self.assertEqual(len(controls), 2)
        self.assertEqual(
            self.session._last_control_sequences["controller-a"], 3
        )

    def test_tracks_sequences_independently_per_source(self) -> None:
        self.session._receive_control(control_frame(4, "controller-a"), reliable=False)
        self.session._receive_control(control_frame(1, "controller-b"), reliable=False)

        self.assertEqual(
            self.session._last_control_sequences,
            {"controller-a": 4, "controller-b": 1},
        )

    def test_stale_frame_does_not_refresh_watchdog(self) -> None:
        with patch(
            "adamas_robot_sdk._runtime.time.monotonic", return_value=10.0
        ) as now:
            self.session._receive_control(control_frame(2), reliable=False)
            self.session._receive_control(control_frame(2), reliable=False)

        self.assertEqual(now.call_count, 1)
        self.assertEqual(self.session._last_control_at, 10.0)


class LatestSampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = _RobotRuntime(CONFIG)
        self.stream = _StreamDescriptor("state", _StreamKind.TELEMETRY)
        self.runtime._streams = (self.stream,)

    def test_keeps_only_latest_pending_sample_per_stream(self) -> None:
        self.runtime._fleet_connected = True
        self.runtime.submit_sample(_TelemetrySample(self.stream, b'"first"'))
        self.runtime.submit_sample(_TelemetrySample(self.stream, b'"second"'))

        samples = self.runtime._take_pending_samples()

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].payload_json, b'"second"')

    def test_drops_samples_while_disconnected(self) -> None:
        self.runtime.submit_sample(_TelemetrySample(self.stream, b'"value"'))

        self.assertEqual(self.runtime._take_pending_samples(), ())


class FakeTransport:
    def on_control(self, callback) -> None:
        self.control_callback = callback


if __name__ == "__main__":
    unittest.main()
