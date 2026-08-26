# Adamas Robot SDK

The Adamas Robot SDK connects an existing robot or simulation to the Adamas
robot operations portal for teleoperation, fleet supervision, JSON telemetry,
and live video. Integrate one `AdamasRobotAdapter` into the application's
existing loop; the SDK handles authentication, retries, real-time networking,
heartbeats, latest-value buffering, and the control watchdog on a background
thread.

## Requirements

- Python 3.10 or newer
- An Adamas fleet ID and fleet key

## Install

From a clone of this repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
```

## Configure a robot

`RobotConfig` connects to the
[Adamas robot operations portal](https://robotics.adamasvr.com) by default.
Copy the fleet ID and fleet key from the fleet's **Robot connection** card.
Choose a stable, unique `robot_id` for routing and reconnecting the robot, and
use `name` for the human-readable label shown to operators:

```python
import os

from adamas_robot_sdk import RobotConfig


config = RobotConfig(
    fleet_id=os.environ["ADAMAS_FLEET_ID"],
    fleet_key=os.environ["ADAMAS_FLEET_KEY"],
    robot_id="robot-01",
    name="Robot 01",
)
```

Keep the fleet key outside source control. Included and subscription robot
connection plans use the Adamas-hosted operations service, so `platform_url`
should remain at its default value. Every account includes three concurrent
robot connections, with subscriptions available for additional capacity.
Dedicated deployments under a custom domain are offered only through custom
enterprise engagements. Customers provisioned with one should set
`platform_url` to the HTTPS endpoint supplied by Adamas.

Plain HTTP is supported only for a trusted local development server, for
example `platform_url="http://localhost:3000"`. Do not use HTTP across a network
because it transmits the fleet key without TLS protection.

## Implement an adapter

Register every stream in `__init__`, before the first `update()`. The returned
handle is the publication API:

```python
from adamas_robot_sdk import (
    AdamasRobotAdapter,
    ControlFrame,
    ControlInput,
    ControlSignal,
    RobotConfig,
    VideoFrame,
)


class MyRobotAdapter(AdamasRobotAdapter):
    def __init__(self, config: RobotConfig, robot) -> None:
        super().__init__(config)
        self.robot = robot
        self.state = self.add_telemetry("robot_state")
        self.camera = self.add_video("front_camera")

    def is_robot_connected(self) -> bool:
        return self.robot.is_connected

    def handle_control(self, control: ControlInput) -> None:
        if isinstance(control, ControlFrame):
            self.robot.apply_control(control)
        elif isinstance(control, ControlSignal):
            self.robot.apply_signal(control.type)

    def publish_state(self) -> None:
        self.state.publish({"positions": self.robot.joint_positions()})

    def publish_camera(self) -> None:
        width, height, rgba = self.robot.camera_rgba()
        self.camera.publish(VideoFrame(width, height, rgba))
```

Drive the adapter from the thread that owns the robot API. Publish each output
when the robot runtime produces it; telemetry and video do not need to share a
frequency:

```python
adapter = MyRobotAdapter(config, robot)

try:
    while robot.is_running:
        robot.step()
        adapter.update()
        if robot.state_sample_ready():
            adapter.publish_state()
        if robot.camera_frame_ready():
            adapter.publish_camera()
finally:
    adapter.close(timeout=5.0)
```

`update()` is non-blocking. It starts networking on the first call and delivers
received controls on the calling thread.

## Streams

- `add_telemetry(key)` registers JSON telemetry. `publish(value)` validates and
  serializes the value immediately.
- `add_video(key)` registers a video stream. `publish(VideoFrame(...))` accepts
  RGBA frame data and the current dimensions, so resolution may change while a
  track is live.
- The key is the stream's stable identity and the exact text displayed in the
  operations portal. Keys must be unique and contain at most 64 letters,
  numbers, dots, dashes, or underscores.
- Registration closes on the first `update()`. Create a new adapter connection
  to change the stream manifest.
- Each stream may publish independently at the cadence provided by the robot
  runtime. The SDK keeps only the latest pending value for each stream, so
  publishers do not block on network delivery. Publications made while
  disconnected are dropped.

Telemetry values must be JSON-serializable and are limited to 900 encoded bytes
per publication. Video frames are limited to 8,294,400 pixels per frame.

## Control

`ControlFrame` contains only `device_profile_id` and a generic `ControlState`:
`poses`, `axes`, `buttons`, and `joints`. Adamas supplies fixed key catalogs for
`keyboard-mouse`, `webxr`, and `gamepad`; integrations may define stable
`custom:...` device profiles. Robot adapters map those generic device fields to
native motion.

Wire versioning, timestamps, source IDs, and sequence numbers are handled
inside the SDK. Duplicate and out-of-order frames never reach
`handle_control()`. Reliable `stop`, `reset`, and `home` commands arrive as
`ControlSignal` values.

## Connection and safety

Only one active connection may use a robot ID. A conflicting process receives a
`RobotConnectionError`; ordinary network interruptions retry internally.
`fleet_connected` and `connection_error` are available for health reporting.
The SDK re-announces the current stream manifest when an operator joins, so an
active video or telemetry stream remains discoverable after the operations
portal is refreshed.

The default 400 ms watchdog delivers `ControlSignal("stop")` when active control
frames stop arriving. Set `control_timeout_s=None` only when the robot system
already has an equivalent or stronger policy. Hardware limits and an
independent emergency stop remain the integrator's responsibility.

## Dummy adapter and tests

`DummyRobotAdapter` publishes generated `front_camera` video and `robot_state`
telemetry using the same API.

```bash
python -m unittest discover -s tests -v
```

## License

Licensed under the BSD 2-Clause License. See [`LICENSE`](LICENSE).
