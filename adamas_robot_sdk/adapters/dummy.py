import threading
import time
from collections.abc import Iterable, Sequence

from ..adapter import AdamasRobotAdapter
from ..config import RobotConfig
from ..protocol import (
    ControlFrame,
    ControlInput,
    ControlSignal,
    SensorDescriptor,
    SensorKind,
    SensorReading,
    VideoFrame,
)


class DummyRobotAdapter(AdamasRobotAdapter):
    """Self-contained test adapter driven by the caller's update loop."""

    VIDEO_WIDTH = 640
    VIDEO_HEIGHT = 360
    FRONT_CAMERA = SensorDescriptor(
        id="front-camera",
        name="Front camera",
        kind=SensorKind.VIDEO,
        rate_hz=10,
    )
    ROBOT_STATE = SensorDescriptor(
        id="robot-state",
        name="Robot state",
        kind=SensorKind.DATA,
        rate_hz=10,
        format="adamas.robot-state.v1",
    )

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self._lock = threading.Lock()
        self._mode = "holding"
        self._translation = [0.0, 0.0, 0.0]
        self._rotation = [0.0, 0.0, 0.0]
        self._gripper = 0.0
        self._last_command_us: int | None = None

    @property
    def sensors(self) -> Sequence[SensorDescriptor]:
        return (self.FRONT_CAMERA, self.ROBOT_STATE)

    def is_robot_connected(self) -> bool:
        return True

    def handle_control(self, control: ControlInput) -> None:
        with self._lock:
            if isinstance(control, ControlSignal):
                if control.type == "stop":
                    self._mode = "holding"
                elif control.type in {"reset", "home"}:
                    self._reset()
                return
            self._apply_frame(control)

    def read_sensors(
        self, requested: Sequence[SensorDescriptor]
    ) -> Iterable[SensorReading]:
        with self._lock:
            translation = list(self._translation)
            rotation = list(self._rotation)
            gripper = self._gripper
            mode = self._mode
            last_command_us = self._last_command_us

        readings: list[SensorReading] = []
        if self.ROBOT_STATE in requested:
            readings.append(
                SensorReading(
                    self.ROBOT_STATE,
                    {
                        "timestampMs": int(time.time() * 1000),
                        "mode": mode,
                        "translation": translation,
                        "rotation": rotation,
                        "gripper": gripper,
                        "lastCommandUs": last_command_us,
                    },
                )
            )
        if self.FRONT_CAMERA in requested:
            readings.append(
                SensorReading(
                    self.FRONT_CAMERA,
                    VideoFrame(
                        width=self.VIDEO_WIDTH,
                        height=self.VIDEO_HEIGHT,
                        rgba=render_frame(
                            self.VIDEO_WIDTH,
                            self.VIDEO_HEIGHT,
                            translation[0],
                            translation[1],
                            gripper,
                            mode == "controlled",
                        ),
                    ),
                )
            )
        return readings

    def _apply_frame(self, frame: ControlFrame) -> None:
        axes = frame.state.axes
        buttons = frame.state.buttons
        if not axes and not buttons:
            return
        self._translation = [
            clamp(current + command * 0.015)
            for current, command in zip(
                self._translation,
                (
                    axes.get("pair.ad", 0.0),
                    axes.get("pair.ws", 0.0),
                    axes.get("pair.qe", 0.0),
                ),
                strict=True,
            )
        ]
        self._rotation = [
            clamp(current + command * 0.025)
            for current, command in zip(
                self._rotation,
                (
                    axes.get("pair.arrow-up-down", 0.0),
                    axes.get("pair.arrow-left-right", 0.0),
                    axes.get("pair.hk", 0.0),
                ),
                strict=True,
            )
        ]
        grasp_rate = float(buttons.get("key.z", False)) - float(
            buttons.get("key.x", False)
        )
        self._gripper = clamp(self._gripper + grasp_rate * 0.025)
        self._last_command_us = frame.captured_at_us
        self._mode = "controlled"

    def _reset(self) -> None:
        self._translation = [0.0, 0.0, 0.0]
        self._rotation = [0.0, 0.0, 0.0]
        self._gripper = 0.0
        self._last_command_us = None
        self._mode = "holding"


def render_frame(
    width: int,
    height: int,
    x: float,
    y: float,
    gripper: float,
    controlled: bool,
) -> bytes:
    pixels = bytearray(bytes((12, 25, 20, 255)) * (width * height))
    marker_x = int((x + 1) * 0.5 * (width - 1))
    marker_y = int((1 - (y + 1) * 0.5) * (height - 1))
    accent = bytes((42, 204, 122, 255) if controlled else (87, 112, 101, 255))

    for row in range(max(0, marker_y - 2), min(height, marker_y + 3)):
        start = (row * width) * 4
        pixels[start : start + width * 4] = accent * width
    for row in range(height):
        for column in range(max(0, marker_x - 2), min(width, marker_x + 3)):
            offset = (row * width + column) * 4
            pixels[offset : offset + 4] = accent

    gripper_width = int((gripper + 1) * 0.5 * width)
    for row in range(height - 10, height - 4):
        start = (row * width) * 4
        pixels[start : start + gripper_width * 4] = accent * gripper_width
    return bytes(pixels)


def clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))
