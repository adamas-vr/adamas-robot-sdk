import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from livekit import rtc

from .protocol import VideoFrame, _StreamDescriptor


CONTROL_TOPIC = "adamas.control.v2"
SIGNAL_TOPIC = "adamas.signal.v1"
MANIFEST_TOPIC = "adamas.manifest.v1"
TELEMETRY_TOPIC = "adamas.telemetry.json.v1"
LOSSY_PACKET_LIMIT = 1_200
RELIABLE_PACKET_LIMIT = 14_000

ControlCallback = Callable[[dict[str, Any], bool], None]


class LiveKitTransport:
    """Private LiveKit transport between a robot adapter and its fleet room."""

    def __init__(self) -> None:
        self.room = rtc.Room()
        self._control_callback: ControlCallback = lambda _message, _reliable: None
        self._video_sources: dict[str, rtc.VideoSource] = {}
        self._manifest_data: bytes | None = None
        self._manifest_tasks: set[asyncio.Task[None]] = set()

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

        @self.room.on("participant_connected")
        def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            if not participant.identity.startswith("operator:"):
                return
            manifest_data = self._manifest_data
            if manifest_data is None:
                return
            task = asyncio.create_task(
                self._republish_manifest(manifest_data, participant.identity)
            )
            self._manifest_tasks.add(task)
            task.add_done_callback(self._manifest_tasks.discard)

    def on_control(self, callback: ControlCallback) -> None:
        self._control_callback = callback

    async def connect(self, config: dict[str, str]) -> None:
        await self.room.connect(config["url"], config["token"])

    async def disconnect(self) -> None:
        tasks, self._manifest_tasks = self._manifest_tasks, set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._manifest_data = None
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
        streams: list[_StreamDescriptor],
    ) -> None:
        manifest_data = encode_json(
            {
                "version": 1,
                "robotId": robot_id,
                "connectionId": connection_id,
                "streams": [stream.to_message() for stream in streams],
            }
        )
        self._manifest_data = manifest_data
        await self._publish_data(
            manifest_data, reliable=True, topic=MANIFEST_TOPIC
        )

    async def publish_telemetry_sample(
        self,
        robot_id: str,
        connection_id: str,
        stream_key: str,
        sequence: int,
        payload_json: bytes,
    ) -> None:
        header = encode_json(
            {
                "robotId": robot_id,
                "connectionId": connection_id,
                "streamKey": stream_key,
                "sequence": sequence,
                "capturedAtUs": time.time_ns() // 1_000,
            }
        )
        packet = header[:-1] + b',"payload":' + payload_json + b"}"
        await self._publish_data(
            packet, reliable=False, topic=TELEMETRY_TOPIC
        )

    async def publish_video_frame(
        self, stream_key: str, frame: VideoFrame
    ) -> None:
        source = self._video_sources.get(stream_key)
        if source is None:
            source = rtc.VideoSource(frame.width, frame.height)
            track = rtc.LocalVideoTrack.create_video_track(stream_key, source)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
            await self.room.local_participant.publish_track(track, options)
            self._video_sources[stream_key] = source
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
                f"Realtime packet is {len(data)} bytes; {topic} allows up to "
                f"{limit}"
            )
        await self.room.local_participant.publish_data(
            data, reliable=reliable, topic=topic
        )

    async def _republish_manifest(
        self, manifest_data: bytes, operator_identity: str
    ) -> None:
        try:
            await self.room.local_participant.publish_data(
                manifest_data,
                reliable=True,
                topic=MANIFEST_TOPIC,
                destination_identities=[operator_identity],
            )
        except Exception:
            # The initial manifest and the next operator join provide retries.
            return


def encode_json(value: Any) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
