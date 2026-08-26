import unittest

from adamas_robot_sdk import ControlState
from adamas_robot_sdk.protocol import _WireControlFrame


VALID_FRAME = {
    "version": 2,
    "sequence": 7,
    "capturedAtUs": 1_234_000,
    "sourceId": "keyboard-1",
    "deviceProfileId": "keyboard-mouse",
    "state": {
        "axes": {
            "pair.ad": 0.5,
            "pair.ws": 0.0,
            "pair.hk": -1.0,
        },
        "buttons": {"key.z": False},
    },
}


class ControlFrameTests(unittest.TestCase):
    def test_parses_keyboard_frame(self) -> None:
        frame = _WireControlFrame.from_message(VALID_FRAME)

        self.assertEqual(frame.sequence, 7)
        self.assertEqual(frame.control.device_profile_id, "keyboard-mouse")
        self.assertEqual(frame.control.state.axes["pair.ad"], 0.5)

    def test_parses_webxr_with_temporarily_lost_controller(self) -> None:
        message = {
            **VALID_FRAME,
            "deviceProfileId": "webxr",
            "state": {
                "poses": {
                    "head": {
                        "positionM": [0, 1.7, 0],
                        "orientationXyzw": [0, 0, 0, 1],
                    },
                    "left.grip": None,
                    "right.grip": {
                        "positionM": [0.2, 1.2, -0.3],
                        "orientationXyzw": [0, 0, 0, 1],
                    },
                }
            },
        }

        frame = _WireControlFrame.from_message(message)

        self.assertIsNone(frame.control.state.poses["left.grip"])

    def test_accepts_gamepad_and_custom_profiles_with_generic_keys(self) -> None:
        for profile_id in ("gamepad", "custom:spacemouse-v1"):
            with self.subTest(profile_id=profile_id):
                frame = _WireControlFrame.from_message(
                    {
                        **VALID_FRAME,
                        "deviceProfileId": profile_id,
                        "state": {"joints": {"dial.0": 1.25}},
                    }
                )
                self.assertEqual(frame.control.state.joints["dial.0"], 1.25)

    def test_rejects_removed_data_glove_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "deviceProfileId"):
            _WireControlFrame.from_message(
                {**VALID_FRAME, "deviceProfileId": "data-glove"}
            )

    def test_rejects_invalid_version_axis_and_quaternion(self) -> None:
        with self.assertRaisesRegex(ValueError, "version"):
            _WireControlFrame.from_message({**VALID_FRAME, "version": 1})
        with self.assertRaisesRegex(ValueError, "between -1 and 1"):
            _WireControlFrame.from_message(
                {**VALID_FRAME, "state": {"axes": {"primary.x": 1.01}}}
            )
        with self.assertRaisesRegex(ValueError, "normalized"):
            _WireControlFrame.from_message(
                {
                    **VALID_FRAME,
                    "deviceProfileId": "webxr",
                    "state": {
                        "poses": {
                            name: {
                                "positionM": [0, 0, 0],
                                "orientationXyzw": [0, 0, 0, 2],
                            }
                            for name in ("head", "left.grip", "right.grip")
                        }
                    },
                }
            )

    def test_accepts_idle_state_for_supported_profiles(self) -> None:
        for profile_id in (
            "keyboard-mouse",
            "webxr",
            "gamepad",
            "custom:flight-stick",
        ):
            with self.subTest(device_profile_id=profile_id):
                frame = _WireControlFrame.from_message(
                    {**VALID_FRAME, "deviceProfileId": profile_id, "state": {}}
                )
                self.assertEqual(frame.control.state, ControlState())


if __name__ == "__main__":
    unittest.main()
