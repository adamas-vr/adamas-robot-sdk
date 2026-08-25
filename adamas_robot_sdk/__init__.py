"""Adamas robot adapter SDK."""

from .adapter import AdamasRobotAdapter, RobotConnectionError
from .adapters import DummyRobotAdapter
from .config import RobotConfig
from .protocol import (
    ControlFrame,
    ControlInput,
    ControlSignal,
    ControlState,
    Pose,
    SensorDelivery,
    SensorDescriptor,
    SensorEncoding,
    SensorKind,
    SensorReading,
    StereoVideo,
    StereoView,
    VideoFrame,
)

__all__ = [
    "AdamasRobotAdapter",
    "ControlFrame",
    "ControlInput",
    "ControlSignal",
    "ControlState",
    "DummyRobotAdapter",
    "Pose",
    "RobotConfig",
    "RobotConnectionError",
    "SensorDelivery",
    "SensorDescriptor",
    "SensorEncoding",
    "SensorKind",
    "SensorReading",
    "StereoVideo",
    "StereoView",
    "VideoFrame",
]
