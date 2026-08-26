import ssl
import unittest
from unittest.mock import patch

from adamas_robot_sdk._platform import (
    PlatformClient,
    RobotIdentity,
    _create_ssl_context,
)


IDENTITY = RobotIdentity(
    robot_id="robot_01",
    fleet_key="afk_test",
    connection_id="connection_01",
    fleet_id="fleet_01",
    name="Robot 01",
)


class PlatformTlsTests(unittest.TestCase):
    def test_http_client_does_not_create_tls_context(self) -> None:
        with patch("adamas_robot_sdk._platform._create_ssl_context") as create:
            client = PlatformClient("http://localhost:3000", IDENTITY)

        create.assert_not_called()
        self.assertIsNone(client._ssl_context)

    def test_https_client_creates_tls_context(self) -> None:
        with patch("adamas_robot_sdk._platform._create_ssl_context") as create:
            PlatformClient("https://fleet.example", IDENTITY)

        create.assert_called_once_with()

    def test_default_context_loads_certifi_ca_bundle(self) -> None:
        context = _create_ssl_context()

        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

    def test_internal_bundle_fills_an_empty_system_store(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch(
            "adamas_robot_sdk._platform.ssl.create_default_context",
            return_value=context,
        ):
            resolved = _create_ssl_context()

        self.assertIs(resolved, context)
        self.assertGreater(resolved.cert_store_stats()["x509_ca"], 0)


if __name__ == "__main__":
    unittest.main()
