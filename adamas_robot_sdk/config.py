import math
import re
from dataclasses import dataclass


IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True, kw_only=True)
class RobotConfig:
    platform_url: str
    fleet_id: str
    fleet_key: str
    robot_id: str
    name: str
    control_timeout_s: float | None = 0.4

    def __post_init__(self) -> None:
        platform_url = self.platform_url.strip().rstrip("/")
        fleet_id = self.fleet_id.strip()
        fleet_key = self.fleet_key.strip()
        robot_id = self.robot_id.strip()
        name = self.name.strip()

        if not platform_url.startswith(("http://", "https://")):
            raise ValueError("platform_url must start with http:// or https://")
        if not IDENTIFIER_PATTERN.fullmatch(fleet_id):
            raise ValueError(
                "fleet_id may contain letters, numbers, dots, dashes, and underscores"
            )
        if not fleet_key or len(fleet_key) > 200:
            raise ValueError("fleet_key must contain between 1 and 200 characters")
        if not IDENTIFIER_PATTERN.fullmatch(robot_id):
            raise ValueError(
                "robot_id may contain letters, numbers, dots, dashes, and underscores"
            )
        if not name or len(name) > 80:
            raise ValueError("name must contain between 1 and 80 characters")
        if self.control_timeout_s is not None and (
            not math.isfinite(self.control_timeout_s) or self.control_timeout_s <= 0
        ):
            raise ValueError("control_timeout_s must be finite and positive or None")

        object.__setattr__(self, "platform_url", platform_url)
        object.__setattr__(self, "fleet_id", fleet_id)
        object.__setattr__(self, "fleet_key", fleet_key)
        object.__setattr__(self, "robot_id", robot_id)
        object.__setattr__(self, "name", name)
