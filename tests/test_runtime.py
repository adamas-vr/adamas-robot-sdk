import unittest
from unittest.mock import patch

from adamas_robot_sdk import ControlFrame, ControlInput, RobotConfig
from adamas_robot_sdk._runtime import _NetworkSession


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

        self.assertEqual(
            [
                control.sequence
                for control in self.controls
                if isinstance(control, ControlFrame)
            ],
            [2, 3],
        )

    def test_tracks_sequences_independently_per_source(self) -> None:
        self.session._receive_control(control_frame(4, "controller-a"), reliable=False)
        self.session._receive_control(control_frame(1, "controller-b"), reliable=False)

        self.assertEqual(
            [
                (control.source_id, control.sequence)
                for control in self.controls
                if isinstance(control, ControlFrame)
            ],
            [("controller-a", 4), ("controller-b", 1)],
        )

    def test_stale_frame_does_not_refresh_watchdog(self) -> None:
        with patch(
            "adamas_robot_sdk._runtime.time.monotonic", return_value=10.0
        ) as now:
            self.session._receive_control(control_frame(2), reliable=False)
            self.session._receive_control(control_frame(2), reliable=False)

        self.assertEqual(now.call_count, 1)
        self.assertEqual(self.session._last_control_at, 10.0)


class FakeTransport:
    def on_control(self, callback) -> None:
        self.control_callback = callback


if __name__ == "__main__":
    unittest.main()
