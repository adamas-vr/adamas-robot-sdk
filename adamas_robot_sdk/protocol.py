import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


SENSOR_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
FORMAT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/+:-]{0,79}$")
CUSTOM_DEVICE_PROFILE_PATTERN = re.compile(
    r"^custom:[a-z0-9][a-z0-9._-]{0,55}$"
)
FIELD_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
ControlSignalType = Literal["stop", "reset", "home"]
DeviceProfileId = str


class SensorKind(StrEnum):
    VIDEO = "video"
    DATA = "data"


class SensorEncoding(StrEnum):
    JSON = "json"
    BINARY = "binary"


class SensorDelivery(StrEnum):
    LATEST = "latest"
    RELIABLE = "reliable"


class StereoView(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    SIDE_BY_SIDE = "side-by-side"


@dataclass(frozen=True, slots=True)
class StereoVideo:
    group_id: str
    view: StereoView

    def __post_init__(self) -> None:
        if not SENSOR_ID_PATTERN.fullmatch(self.group_id):
            raise ValueError("Stereo group IDs must use a valid sensor ID")
        object.__setattr__(self, "view", StereoView(self.view))


@dataclass(frozen=True, slots=True)
class SensorDescriptor:
    id: str
    name: str
    kind: SensorKind
    rate_hz: float = 10.0
    format: str = "adamas.json.v1"
    encoding: SensorEncoding = SensorEncoding.JSON
    delivery: SensorDelivery = SensorDelivery.LATEST
    stereo: StereoVideo | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        kind = SensorKind(self.kind)
        encoding = SensorEncoding(self.encoding)
        delivery = SensorDelivery(self.delivery)
        if not SENSOR_ID_PATTERN.fullmatch(self.id):
            raise ValueError(
                "Sensor IDs may contain letters, numbers, dots, dashes, and underscores"
            )
        if not name or len(name) > 80:
            raise ValueError("Sensor names must contain between 1 and 80 characters")
        if not math.isfinite(self.rate_hz) or not 0 < self.rate_hz <= 120:
            raise ValueError("Sensor rate_hz must be between 0 and 120")
        if kind is SensorKind.DATA and not FORMAT_PATTERN.fullmatch(self.format):
            raise ValueError("Data sensor format must be a valid format identifier")
        if kind is SensorKind.VIDEO and self.stereo is not None:
            StereoVideo(self.stereo.group_id, self.stereo.view)
        if kind is SensorKind.DATA and self.stereo is not None:
            raise ValueError("Stereo metadata can only be used with video sensors")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "encoding", encoding)
        object.__setattr__(self, "delivery", delivery)

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "id": self.id,
            "label": self.name,
            "kind": self.kind.value,
            "rateHz": self.rate_hz,
        }
        if self.kind is SensorKind.DATA:
            message.update(
                format=self.format,
                encoding=self.encoding.value,
                delivery=self.delivery.value,
            )
        elif self.stereo:
            message["stereo"] = {
                "groupId": self.stereo.group_id,
                "view": self.stereo.view.value,
            }
        return message


@dataclass(frozen=True, slots=True)
class VideoFrame:
    width: int
    height: int
    rgba: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Video frame dimensions must be positive")
        if len(self.rgba) != self.width * self.height * 4:
            raise ValueError("VideoFrame requires four RGBA bytes per pixel")


SensorValue = Any | VideoFrame


@dataclass(frozen=True, slots=True)
class SensorReading:
    sensor: SensorDescriptor
    value: SensorValue

    def __post_init__(self) -> None:
        if self.sensor.kind is SensorKind.VIDEO:
            if not isinstance(self.value, VideoFrame):
                raise TypeError("Video sensors require a VideoFrame value")
            return
        if isinstance(self.value, VideoFrame):
            raise TypeError("VideoFrame values require a video sensor")
        if self.sensor.encoding is SensorEncoding.BINARY and not isinstance(
            self.value, bytes
        ):
            raise TypeError("Binary data sensors require a bytes value")


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
    sequence: int
    captured_at_us: int
    source_id: str
    device_profile_id: DeviceProfileId
    state: ControlState
    version: Literal[2] = 2

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "ControlFrame":
        if not isinstance(message, dict) or message.get("version") != 2:
            raise ValueError("ControlFrame version must be 2")
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
        state = parse_control_state(message.get("state"))
        return cls(
            sequence=nonnegative_int(message.get("sequence"), "sequence"),
            captured_at_us=nonnegative_int(
                message.get("capturedAtUs"), "capturedAtUs"
            ),
            source_id=source_id,
            device_profile_id=device_profile_id,
            state=state,
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
    return value in {"keyboard-mouse", "webxr", "data-glove", "gamepad"} or bool(
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
    result = tuple(finite_float(component, name) for component in value)
    return result


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
