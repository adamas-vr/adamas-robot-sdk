import ssl
import unittest
from unittest.mock import patch

from adamas_robot_sdk._platform import _create_ssl_context


class PlatformTlsTests(unittest.TestCase):
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
