# Adamas Robot SDK

The Adamas Robot SDK connects an existing robot or simulation to Adamas Cloud
for teleoperation, fleet supervision, and sensor streaming. Applications
integrate through one class: inherit `AdamasRobotAdapter`, implement the four
robot-facing hooks, and call `update()` from the application's existing loop.

The SDK owns fleet authentication, retries, LiveKit networking, heartbeats,
stream manifests, delivery backpressure, and the control watchdog. It does not
own the robot or simulation loop, and `update()` does not wait for network I/O.

## Requirements

- Python 3.11 or newer
- An Adamas fleet ID and fleet key

## Install from this repository

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Connect to Adamas Cloud

`RobotConfig` connects to [Adamas Cloud](https://robotics.adamasvr.com) by
default. Supply the fleet ID and fleet key from the fleet's **Robot connection**
card:

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

Treat the fleet key as a secret and keep it outside source control. The
`platform_url` option is only needed when Adamas provides a custom endpoint as
part of an enterprise deployment.

## Try the included dummy adapter

`DummyRobotAdapter` is included in the SDK and produces a generated camera feed
and robot-state stream. With the two environment variables above set, run this
script from the repository:

```python
import os
import time

from adamas_robot_sdk import DummyRobotAdapter, RobotConfig


adapter = DummyRobotAdapter(
    RobotConfig(
        fleet_id=os.environ["ADAMAS_FLEET_ID"],
        fleet_key=os.environ["ADAMAS_FLEET_KEY"],
        robot_id="sdk-dummy",
        name="SDK Dummy Robot",
    )
)

try:
    while True:
        adapter.update()
        time.sleep(1 / 60)
finally:
    adapter.close(timeout=5.0)
```

The implementation is available in
[`adamas_robot_sdk/adapters/dummy.py`](adamas_robot_sdk/adapters/dummy.py).

## Implement a robot adapter

The robot-facing contract consists of four hooks:

- `sensors` reports the sensors currently available. Changing this sequence
  updates the fleet's data-source manifest.
- `is_robot_connected()` determines whether the robot should appear online.
- `handle_control()` translates Adamas input into the robot's native commands.
- `read_sensors()` returns readings for the descriptors requested by the SDK.

```python
from collections.abc import Iterable, Sequence

from adamas_robot_sdk import (
    AdamasRobotAdapter,
    ControlFrame,
    ControlInput,
    ControlSignal,
    RobotConfig,
    SensorDescriptor,
    SensorKind,
    SensorReading,
)


class MyRobotAdapter(AdamasRobotAdapter):
    JOINT_STATE = SensorDescriptor(
        id="joint-state",
        name="Joint state",
        kind=SensorKind.DATA,
        rate_hz=30,
        format="adamas.joint-state.v1",
    )

    def __init__(self, config: RobotConfig, robot) -> None:
        super().__init__(config)
        self.robot = robot

    @property
    def sensors(self) -> Sequence[SensorDescriptor]:
        return (self.JOINT_STATE,)

    def is_robot_connected(self) -> bool:
        return self.robot.is_connected

    def handle_control(self, control: ControlInput) -> None:
        if isinstance(control, ControlFrame):
            self.robot.apply_control(control)
        elif isinstance(control, ControlSignal):
            self.robot.apply_signal(control.type)

    def read_sensors(
        self, requested: Sequence[SensorDescriptor]
    ) -> Iterable[SensorReading]:
        if self.JOINT_STATE not in requested:
            return ()
        return (
            SensorReading(
                self.JOINT_STATE,
                {"positions": self.robot.joint_positions()},
            ),
        )
```

Drive the adapter from the same thread as the robot API:

```python
adapter = MyRobotAdapter(config, robot)

try:
    while robot.is_running:
        robot.step()
        adapter.update()
finally:
    adapter.close(timeout=5.0)
```

All user-defined hooks run on the thread that calls `update()`. The first call
starts networking in a background thread. Later calls drain control and enqueue
sensor readings without blocking the host loop.

## Sensors

Sensor descriptors replace arbitrary topic strings. The SDK schedules each
descriptor at its declared `rate_hz` and accepts `SensorReading` values tied to
those descriptor objects.

Data sensors declare a stable `format`, JSON or binary encoding, and latest or
reliable delivery. Video sensors use `VideoFrame` and become LiveKit media
tracks. Stereo cameras can declare a shared group and left, right, or
side-by-side view.

## Control

`ControlFrame.state` exposes generic `poses`, `axes`, `buttons`, and `joints`
maps. Its `device_profile_id` identifies the field convention:
`keyboard-mouse`, `webxr`, `data-glove`, `gamepad`, or a documented
`custom:...` integration. Robot adapters decide how those fields map to native
motion.

ControlFrame version 2 describes devices rather than robot motion. Duplicate
and out-of-order frames are ignored per device source for each realtime
connection. Reliable `stop`, `reset`, and `home` signals are delivered as
`ControlSignal` values.

## Connection and safety behavior

Only one active connection may use a robot ID. A conflicting process receives a
`RobotConnectionError`; network interruptions otherwise retry internally.
`fleet_connected` and `connection_error` are available for health reporting.

The default 400 ms watchdog queues `ControlSignal("stop")` when active control
frames stop arriving. Set `control_timeout_s=None` only when the robot system
already provides an equivalent or stronger safety policy. Hardware limits and
an independent emergency-stop mechanism remain the robot integrator's
responsibility.

The platform connection always uses HTTPS. Certificate verification uses the
Python environment's trusted roots plus the CA bundle installed with the SDK.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

Licensed under the BSD 2-Clause License. See [`LICENSE`](LICENSE).
