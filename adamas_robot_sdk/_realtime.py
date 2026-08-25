import asyncio
import json
import struct
import time
from collections.abc import Callable
from typing import Any

from livekit import rtc

from .protocol import (
    SensorDelivery,
    SensorDescriptor,
    SensorEncoding,
    VideoFrame,
)


CONTROL_TOPIC = "adamas.control.v2"
SIGNAL_TOPIC = "adamas.signal.v1"
MANIFEST_TOPIC = "adamas.manifest.v1"
JSON_DATA_TOPIC = "adamas.data.json.v1"
BINARY_DATA_TOPIC = "adamas.data.binary.v1"
LOSSY_PACKET_LIMIT = 1_200
RELIABLE_PACKET_LIMIT = 14_000

ControlCallback = Callable[[dict[str, Any], bool], None]


class LiveKitTransport:
    """Private LiveKit transport between a robot adapter and its fleet room."""

    def __init__(self) -> None:
        self.room = rtc.Room()
        self._control_callback: ControlCallback = lambda _message, _reliable: None
        self._video_sources: dict[str, rtc.VideoSource] = {}

        @self.room.on("data_received")
        def on_data_received(packet: rtc.DataPacket) -> None:
            reliable = packet.topic == SIGNAL_TOPIC
            if packet.topic not in {CONTROL_TOPIC, SIGNAL_TOPIC}:
                return
            participant = getattr(packet, "participant", None)
            identity = getattr(participant, "identity", "")
            if not identity.startswith("operator:"):
                return
            try:
                message = json.loads(packet.data)
                if isinstance(message, dict):
                    self._control_callback(message, reliable)
            except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
                return

    def on_control(self, callback: ControlCallback) -> None:
        self._control_callback = callback

    async def connect(self, config: dict[str, str]) -> None:
        await self.room.connect(config["url"], config["token"])

    async def disconnect(self) -> None:
        sources = list(self._video_sources.values())
        self._video_sources.clear()
        await asyncio.gather(
            *(source.aclose() for source in sources), return_exceptions=True
        )
        await self.room.disconnect()

    async def publish_manifest(
        self,
        robot_id: str,
        connection_id: str,
        sensors: list[SensorDescriptor],
    ) -> None:
        await self._publish_data(
            encode_json(
                {
                    "version": 1,
                    "robotId": robot_id,
                    "connectionId": connection_id,
                    "streams": [sensor.to_message() for sensor in sensors],
                }
            ),
            reliable=True,
            topic=MANIFEST_TOPIC,
        )

    async def publish_stream_sample(
        self,
        robot_id: str,
        connection_id: str,
        sensor: SensorDescriptor,
        sequence: int,
        data: Any,
    ) -> None:
        header = {
            "robotId": robot_id,
            "connectionId": connection_id,
            "streamId": sensor.id,
            "sequence": sequence,
            "capturedAtUs": time.time_ns() // 1_000,
        }
        reliable = sensor.delivery is SensorDelivery.RELIABLE
        if sensor.encoding is SensorEncoding.BINARY:
            header_bytes = encode_json(header)
            packet = struct.pack(">I", len(header_bytes)) + header_bytes + data
            topic = BINARY_DATA_TOPIC
        else:
            packet = encode_json({**header, "payload": data})
            topic = JSON_DATA_TOPIC
        await self._publish_data(packet, reliable=reliable, topic=topic)

    async def publish_video_frame(
        self, sensor_id: str, frame: VideoFrame
    ) -> None:
        source = self._video_sources.get(sensor_id)
        if source is None:
            source = rtc.VideoSource(frame.width, frame.height)
            track = rtc.LocalVideoTrack.create_video_track(sensor_id, source)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
            await self.room.local_participant.publish_track(track, options)
            self._video_sources[sensor_id] = source
        source.capture_frame(
            rtc.VideoFrame(
                frame.width,
                frame.height,
                rtc.VideoBufferType.RGBA,
                frame.rgba,
            )
        )

    async def _publish_data(
        self, data: bytes, *, reliable: bool, topic: str
    ) -> None:
        limit = RELIABLE_PACKET_LIMIT if reliable else LOSSY_PACKET_LIMIT
        if len(data) > limit:
            raise ValueError(
                f"Realtime packet is {len(data)} bytes; {topic} allows up to {limit}"
            )
        await self.room.local_participant.publish_data(
            data, reliable=reliable, topic=topic
        )


def encode_json(value: Any) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
