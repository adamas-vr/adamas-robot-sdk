import asyncio
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import certifi

from .protocol import SensorDescriptor


class PlatformError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Platform request failed ({status}): {detail}")


@dataclass(frozen=True, slots=True)
class RobotIdentity:
    robot_id: str
    fleet_key: str
    connection_id: str
    fleet_id: str
    name: str


class PlatformClient:
    def __init__(self, api_url: str, identity: RobotIdentity) -> None:
        self.api_url = api_url.rstrip("/")
        self.identity = identity
        self._ssl_context = _create_ssl_context()

    async def connect(
        self,
        sensors: list[SensorDescriptor],
        sdk_version: str,
    ) -> dict[str, Any]:
        return await self._post(
            "/api/robots/connect",
            {
                **self._credentials(),
                "fleetId": self.identity.fleet_id,
                "name": self.identity.name,
                "streams": [sensor.to_message() for sensor in sensors],
                "sdkVersion": sdk_version,
            },
        )

    async def heartbeat(
        self, sensors: list[SensorDescriptor], status: str = "ready"
    ) -> None:
        await self._post(
            "/api/robots/heartbeat",
            {
                **self._credentials(),
                "fleetId": self.identity.fleet_id,
                "status": status,
                "streams": [sensor.to_message() for sensor in sensors],
            },
        )

    async def disconnect(self) -> None:
        await self._post(
            "/api/robots/disconnect",
            {**self._credentials(), "fleetId": self.identity.fleet_id},
        )

    def _credentials(self) -> dict[str, str]:
        return {
            "robotId": self.identity.robot_id,
            "fleetKey": self.identity.fleet_key,
            "connectionId": self.identity.connection_id,
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post_sync, path, payload)

    def _post_sync(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self.api_url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(
                request,
                timeout=15,
                context=self._ssl_context,
            ) as response:
                return json.loads(response.read())
        except HTTPError as error:
            detail = read_error_detail(error)
            raise PlatformError(error.code, detail) from error


def _create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def read_error_detail(error: HTTPError) -> str:
    body = error.read().decode(errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body or str(error.reason)
    detail = payload.get("error") if isinstance(payload, dict) else None
    return detail if isinstance(detail, str) and detail else body
