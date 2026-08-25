import json
import struct
import unittest

from adamas_robot_sdk import (
    SensorDelivery,
    SensorDescriptor,
    SensorEncoding,
    SensorKind,
)
from adamas_robot_sdk._realtime import (
    BINARY_DATA_TOPIC,
    JSON_DATA_TOPIC,
    MANIFEST_TOPIC,
    LiveKitTransport,
)


class FakeParticipant:
    def __init__(self) -> None:
        self.packets: list[tuple[bytes, bool, str]] = []

    async def publish_data(
        self, data: bytes, *, reliable: bool, topic: str
    ) -> None:
        self.packets.append((data, reliable, topic))


class FakeRoom:
    def __init__(self) -> None:
        self.local_participant = FakeParticipant()


class LiveKitWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.transport = LiveKitTransport.__new__(LiveKitTransport)
        self.transport.room = FakeRoom()

    async def test_manifest_uses_fixed_reliable_topic(self) -> None:
        sensor = SensorDescriptor("state", "State", SensorKind.DATA)

        await self.transport.publish_manifest("robot", "connection", [sensor])

        data, reliable, topic = self.transport.room.local_participant.packets[0]
        message = json.loads(data)
        self.assertEqual(topic, MANIFEST_TOPIC)
        self.assertTrue(reliable)
        self.assertEqual(message["version"], 1)
        self.assertEqual(message["streams"][0]["kind"], "data")

    async def test_json_and_binary_frames_use_fixed_topics(self) -> None:
        json_sensor = SensorDescriptor("state", "State", SensorKind.DATA)
        binary_sensor = SensorDescriptor(
            "points",
            "Points",
            SensorKind.DATA,
            encoding=SensorEncoding.BINARY,
            delivery=SensorDelivery.RELIABLE,
            format="application/octet-stream",
        )

        await self.transport.publish_stream_sample(
            "robot", "connection", json_sensor, 1, {"ready": True}
        )
        await self.transport.publish_stream_sample(
            "robot", "connection", binary_sensor, 2, b"abc"
        )

        json_data, json_reliable, json_topic = (
            self.transport.room.local_participant.packets[0]
        )
        self.assertEqual(json_topic, JSON_DATA_TOPIC)
        self.assertFalse(json_reliable)
        self.assertEqual(json.loads(json_data)["payload"], {"ready": True})

        binary_data, binary_reliable, binary_topic = (
            self.transport.room.local_participant.packets[1]
        )
        header_size = struct.unpack(">I", binary_data[:4])[0]
        header = json.loads(binary_data[4 : 4 + header_size])
        self.assertEqual(binary_topic, BINARY_DATA_TOPIC)
        self.assertTrue(binary_reliable)
        self.assertEqual(header["sequence"], 2)
        self.assertEqual(binary_data[4 + header_size :], b"abc")


if __name__ == "__main__":
    unittest.main()
