import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from adamas_robot_sdk import VideoFrame
from adamas_robot_sdk._realtime import (
    MANIFEST_TOPIC,
    TELEMETRY_TOPIC,
    LiveKitTransport,
)
from adamas_robot_sdk.protocol import _StreamDescriptor, _StreamKind


class FakeParticipant:
    def __init__(self) -> None:
        self.packets: list[tuple[bytes, bool, str, list[str]]] = []
        self.tracks = []

    async def publish_data(
        self,
        data: bytes,
        *,
        reliable: bool,
        topic: str,
        destination_identities: list[str] | None = None,
    ) -> None:
        self.packets.append(
            (data, reliable, topic, destination_identities or [])
        )

    async def publish_track(self, track, options) -> None:
        self.tracks.append((track, options))


class FakeRoom:
    def __init__(self) -> None:
        self.local_participant = FakeParticipant()
        self.handlers = {}

    def on(self, event):
        def register(callback):
            self.handlers[event] = callback
            return callback

        return register


class FakeVideoSource:
    def __init__(self, width: int, height: int) -> None:
        self.initial_size = (width, height)
        self.frames = []

    def capture_frame(self, frame) -> None:
        self.frames.append(frame)


class LiveKitWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        with patch("adamas_robot_sdk._realtime.rtc.Room", FakeRoom):
            self.transport = LiveKitTransport()

    async def test_manifest_contains_only_key_and_kind(self) -> None:
        stream = _StreamDescriptor("state", _StreamKind.TELEMETRY)

        await self.transport.publish_manifest("robot", "connection", [stream])

        data, reliable, topic, destinations = (
            self.transport.room.local_participant.packets[0]
        )
        message = json.loads(data)
        self.assertEqual(topic, MANIFEST_TOPIC)
        self.assertTrue(reliable)
        self.assertEqual(destinations, [])
        self.assertEqual(
            message["streams"], [{"key": "state", "kind": "telemetry"}]
        )

    async def test_manifest_is_republished_when_operator_joins(self) -> None:
        stream = _StreamDescriptor("camera", _StreamKind.VIDEO)
        await self.transport.publish_manifest("robot", "connection", [stream])
        self.transport.room.local_participant.packets.clear()

        self.transport.room.handlers["participant_connected"](
            SimpleNamespace(identity="operator:user")
        )
        await asyncio.gather(*self.transport._manifest_tasks)

        packets = self.transport.room.local_participant.packets
        self.assertEqual(len(packets), 1)
        message = json.loads(packets[0][0])
        self.assertEqual(packets[0][2], MANIFEST_TOPIC)
        self.assertEqual(packets[0][3], ["operator:user"])
        self.assertEqual(message["connectionId"], "connection")

    async def test_manifest_is_not_republished_for_another_robot(self) -> None:
        stream = _StreamDescriptor("camera", _StreamKind.VIDEO)
        await self.transport.publish_manifest("robot", "connection", [stream])
        self.transport.room.local_participant.packets.clear()

        self.transport.room.handlers["participant_connected"](
            SimpleNamespace(identity="robot:other")
        )
        await asyncio.sleep(0)

        self.assertEqual(self.transport.room.local_participant.packets, [])

    async def test_telemetry_uses_fixed_lossy_json_topic(self) -> None:
        await self.transport.publish_telemetry_sample(
            "robot", "connection", "state", 2, b'{"ready":true}'
        )

        data, reliable, topic, destinations = (
            self.transport.room.local_participant.packets[0]
        )
        message = json.loads(data)
        self.assertEqual(topic, TELEMETRY_TOPIC)
        self.assertFalse(reliable)
        self.assertEqual(destinations, [])
        self.assertEqual(message["streamKey"], "state")
        self.assertEqual(message["sequence"], 2)
        self.assertEqual(message["payload"], {"ready": True})

    async def test_video_keeps_one_track_across_resolution_changes(self) -> None:
        fake_rtc = SimpleNamespace(
            VideoSource=FakeVideoSource,
            LocalVideoTrack=SimpleNamespace(
                create_video_track=lambda key, source: (key, source)
            ),
            TrackPublishOptions=lambda **values: values,
            TrackSource=SimpleNamespace(SOURCE_CAMERA="camera"),
            VideoFrame=lambda width, height, buffer_type, rgba: (
                width,
                height,
                buffer_type,
                rgba,
            ),
            VideoBufferType=SimpleNamespace(RGBA="rgba"),
        )
        first = VideoFrame(2, 2, bytes(16))
        resized = VideoFrame(4, 2, bytes(32))

        with patch("adamas_robot_sdk._realtime.rtc", fake_rtc):
            await self.transport.publish_video_frame("camera", first)
            await self.transport.publish_video_frame("camera", resized)

        source = self.transport._video_sources["camera"]
        self.assertEqual(source.initial_size, (2, 2))
        self.assertEqual([(frame[0], frame[1]) for frame in source.frames], [(2, 2), (4, 2)])
        self.assertEqual(len(self.transport.room.local_participant.tracks), 1)


if __name__ == "__main__":
    unittest.main()
