from collections.abc import Callable
from typing import Any

from .protocol import (
    VideoFrame,
    _StreamDescriptor,
    _StreamSample,
    _TelemetrySample,
    _VideoSample,
    encode_telemetry,
)


class TelemetryPublisher:
    """Non-blocking publisher for one JSON telemetry stream."""

    def __init__(
        self,
        stream: _StreamDescriptor,
        submit: Callable[[_StreamSample], None],
    ) -> None:
        self._stream = stream
        self._submit = submit

    @property
    def key(self) -> str:
        return self._stream.key

    def publish(self, value: Any) -> None:
        self._submit(_TelemetrySample(self._stream, encode_telemetry(value)))


class VideoPublisher:
    """Non-blocking publisher for one mono RGBA video stream."""

    def __init__(
        self,
        stream: _StreamDescriptor,
        submit: Callable[[_StreamSample], None],
    ) -> None:
        self._stream = stream
        self._submit = submit

    @property
    def key(self) -> str:
        return self._stream.key

    def publish(self, frame: VideoFrame) -> None:
        if not isinstance(frame, VideoFrame):
            raise TypeError("Video streams require a VideoFrame")
        self._submit(_VideoSample(self._stream, frame))
