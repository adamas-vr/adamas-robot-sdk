import unittest

from adamas_robot_sdk import RobotConfig


VALID_CONFIG = {
    "fleet_id": "fleet_01",
    "fleet_key": "afk_test",
    "robot_id": "robot_01",
    "name": "Robot 01",
}


class RobotConfigTests(unittest.TestCase):
    def test_defaults_to_adamas_cloud(self) -> None:
        config = RobotConfig(**VALID_CONFIG)

        self.assertEqual(config.platform_url, "https://robotics.adamasvr.com")

    def test_accepts_enterprise_https_platform_url(self) -> None:
        config = RobotConfig(
            platform_url="https://fleet.example:8443/platform/",
            **VALID_CONFIG,
        )

        self.assertEqual(config.platform_url, "https://fleet.example:8443/platform")

    def test_accepts_http_platform_url(self) -> None:
        config = RobotConfig(
            platform_url="http://localhost:3000/",
            **VALID_CONFIG,
        )

        self.assertEqual(config.platform_url, "http://localhost:3000")

    def test_rejects_invalid_or_credentialed_platform_url(self) -> None:
        invalid_urls = (
            "https://",
            "localhost:3000",
            "ftp://fleet.example",
            "https://user:password@fleet.example",
            "https://fleet.example?token=value",
            "https://fleet.example#fragment",
        )
        for platform_url in invalid_urls:
            with self.subTest(platform_url=platform_url):
                with self.assertRaises(ValueError):
                    RobotConfig(platform_url=platform_url, **VALID_CONFIG)


if __name__ == "__main__":
    unittest.main()
