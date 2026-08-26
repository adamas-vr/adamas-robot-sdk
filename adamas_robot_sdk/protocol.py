import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


STREAM_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
CUSTOM_DEVICE_PROFILE_PATTERN = re.compile(
    r"^custom:[a-z0-9][a-z0-9._-]{0,55}$"
)
FIELD_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
MAX_TELEMETRY_JSON_BYTES = 900
MAX_VIDEO_PIXELS = 3_840 * 2_160
ControlSignalType = Literal["stop", "reset", "home"]
DeviceProfileId = str


class _StreamKind(str, Enum):
    TELEMETRY = "telemetry"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class _StreamDescriptor:
    key: str
    kind: _StreamKind

    def __post_init__(self) -> None:
        if not STREAM_KEY_PATTERN.fullmatch(self.key):
            raise ValueError(
                "Stream keys may contain letters, numbers, dots, dashes, and underscores"
            )
        object.__setattr__(self, "kind", _StreamKind(self.kind))

    def to_message(self) -> dict[str, str]:
        return {"key": self.key, "kind": self.kind.value}


@dataclass(frozen=True, slots=True)
class VideoFrame:
    width: int
    height: int
    rgba: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Video frame dimensions must be positive")
        if self.width * self.height > MAX_VIDEO_PIXELS:
            raise ValueError(
                f"Video frames cannot exceed {MAX_VIDEO_PIXELS} pixels"
            )
        if len(self.rgba) != self.width * self.height * 4:
            raise ValueError("VideoFrame requires four RGBA bytes per pixel")


@dataclass(frozen=True, slots=True)
class _TelemetrySample:
    stream: _StreamDescriptor
    payload_json: bytes


@dataclass(frozen=True, slots=True)
class _VideoSample:
    stream: _StreamDescriptor
    frame: VideoFrame


_StreamSample = _TelemetrySample | _VideoSample


def encode_telemetry(value: Any) -> bytes:
    try:
        payload = json.dumps(
            value, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise TypeError("Telemetry values must be valid JSON") from error
    if len(payload) > MAX_TELEMETRY_JSON_BYTES:
        raise ValueError(
            f"Telemetry JSON cannot exceed {MAX_TELEMETRY_JSON_BYTES} bytes"
        )
    return payload


@dataclass(frozen=True, slots=True)
class Pose:
    position_m: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class ControlState:
    poses: dict[str, Pose | None] = field(default_factory=dict)
    axes: dict[str, float] = field(default_factory=dict)
    buttons: dict[str, bool] = field(default_factory=dict)
    joints: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ControlFrame:
    device_profile_id: DeviceProfileId
    state: ControlState


@dataclass(frozen=True, slots=True)
class _WireControlFrame:
    sequence: int
    captured_at_us: int
    source_id: str
    control: ControlFrame
    version: Literal[2] = 2

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "_WireControlFrame":
        if not isinstance(message, dict) or message.get("version") != 2:
            raise ValueError("Control frame version must be 2")
        source_id = message.get("sourceId")
        if not isinstance(source_id, str) or not 1 <= len(source_id) <= 128:
            raise ValueError("sourceId must contain 1 to 128 characters")
        device_profile_id = message.get("deviceProfileId")
        if not isinstance(device_profile_id, str) or not is_valid_device_profile_id(
            device_profile_id
        ):
            raise ValueError(
                "deviceProfileId is not a recognized or valid custom device profile"
            )
        return cls(
            sequence=nonnegative_int(message.get("sequence"), "sequence"),
            captured_at_us=nonnegative_int(
                message.get("capturedAtUs"), "capturedAtUs"
            ),
            source_id=source_id,
            control=ControlFrame(
                device_profile_id=device_profile_id,
                state=parse_control_state(message.get("state")),
            ),
        )


@dataclass(frozen=True, slots=True)
class ControlSignal:
    type: ControlSignalType


ControlInput = ControlFrame | ControlSignal


def parse_control_state(value: Any) -> ControlState:
    item = require_dict(value, "state")
    unexpected = set(item) - {"poses", "axes", "buttons", "joints"}
    if unexpected:
        raise ValueError("state contains an unsupported control group")
    poses_message = optional_dict(item.get("poses"), "state.poses")
    axes_message = optional_dict(item.get("axes"), "state.axes")
    buttons_message = optional_dict(item.get("buttons"), "state.buttons")
    joints_message = optional_dict(item.get("joints"), "state.joints")
    for name, group in (
        ("poses", poses_message),
        ("axes", axes_message),
        ("buttons", buttons_message),
        ("joints", joints_message),
    ):
        validate_field_names(group, name)

    poses = {
        key: None if pose is None else parse_pose(pose, f"state.poses.{key}")
        for key, pose in poses_message.items()
    }
    axes = {
        key: bounded_float(axis, f"state.axes.{key}")
        for key, axis in axes_message.items()
    }
    buttons: dict[str, bool] = {}
    for key, button in buttons_message.items():
        if not isinstance(button, bool):
            raise ValueError(f"state.buttons.{key} must be boolean")
        buttons[key] = button
    joints = {
        key: finite_float(joint, f"state.joints.{key}")
        for key, joint in joints_message.items()
    }
    return ControlState(poses=poses, axes=axes, buttons=buttons, joints=joints)


def parse_pose(value: Any, name: str) -> Pose:
    item = require_dict(value, name)
    position = vector(item.get("positionM"), 3, f"{name}.positionM")
    orientation = vector(
        item.get("orientationXyzw"), 4, f"{name}.orientationXyzw"
    )
    norm = math.sqrt(sum(component * component for component in orientation))
    if not 0.99 <= norm <= 1.01:
        raise ValueError(f"{name}.orientationXyzw must be normalized")
    return Pose(
        position_m=(position[0], position[1], position[2]),
        orientation_xyzw=(
            orientation[0],
            orientation[1],
            orientation[2],
            orientation[3],
        ),
    )


def is_valid_device_profile_id(value: str) -> bool:
    return value in {"keyboard-mouse", "webxr", "gamepad"} or bool(
        CUSTOM_DEVICE_PROFILE_PATTERN.fullmatch(value)
    )


def validate_field_names(group: dict[str, Any], name: str) -> None:
    if len(group) > 64:
        raise ValueError(f"state.{name} cannot exceed 64 fields")
    if any(not FIELD_PATTERN.fullmatch(key) for key in group):
        raise ValueError(f"state.{name} contains an invalid field name")


def optional_dict(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    return require_dict(value, name)


def require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def vector(value: Any, length: int, name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    return tuple(finite_float(component, name) for component in value)


def finite_float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def bounded_float(value: Any, name: str) -> float:
    result = finite_float(value, name)
    if not -1.0 <= result <= 1.0:
        raise ValueError(f"{name} must be between -1 and 1")
    return result


def nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
