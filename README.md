# Adamas robot adapter SDK

The SDK has one integration path: inherit `AdamasRobotAdapter`, implement the
robot-facing hooks, and call `update()` from the robot system's existing loop.
The adapter privately manages fleet authentication, retries, LiveKit,
heartbeats, stream manifests, delivery backpressure, and the control watchdog.

The SDK never owns the robot or simulation loop. `update()` performs no waiting
for network I/O, and all user-defined control and sensor hooks execute on the
thread that called `update()`. This keeps thread-affine frameworks such as Isaac
Sim in control of simulation state and lets LeRobot retain ownership of
`get_observation()` and `send_action()`.

## Install

Python 3.11 or newer is required.

```bash
cd adamas-robot-sdk
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Implement an adapter

All connection and robot identity values live in `RobotConfig`; robot-specific
environment variables are not required.

```python
from collections.abc import Iterable, Sequence

from adamas_robot_sdk import (
    AdamasRobotAdapter,
    ControlInput,
    ControlSignal,
    RobotConfig,
    SensorDescriptor,
    SensorKind,
    SensorReading,
    ControlFrame,
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
            self.robot.send_action(translate_control(control))
        elif isinstance(control, ControlSignal):
            self.robot.handle_signal(control.type)

    def read_sensors(
        self, requested: Sequence[SensorDescriptor]
    ) -> Iterable[SensorReading]:
        observation = self.robot.get_observation()
        return (SensorReading(self.JOINT_STATE, observation),)
```

The four hooks are the entire robot-facing contract:

- `sensors` reports the sensors currently available at runtime. Changing this
  sequence updates the fleet's data-source manifest.
- `is_robot_connected()` prevents disconnected or unavailable robots from being
  registered in the fleet.
- `handle_control()` translates Adamas control into the robot system's native
  action API.
- `read_sensors()` receives the descriptors due at the current update and
  returns structured `SensorReading` values.

Sensor descriptors replace publish/subscribe topic strings. The SDK schedules
each descriptor at its declared `rate_hz`; adapters return readings associated
with those descriptor objects instead of publishing to arbitrary names.
Data sensors declare a stable `format`, JSON or binary encoding, and latest or
reliable delivery. Video sensors become LiveKit media tracks. Stereo cameras
can declare a shared group and left, right, or side-by-side view.

Incoming `ControlFrame.state` exposes generic `poses`, `axes`, `buttons`, and
`joints` maps. Its `device_profile_id` identifies the field convention:
`keyboard-mouse`, `webxr`, `data-glove`, `gamepad`, or a documented
`custom:...` integration. The robot adapter translates supported fields into
its native actions. Device profiles catalog possible keys; frames may omit
every key while the operator is idle.

`ControlFrame` version 2 deliberately describes devices rather than robot
motion. The built-in keyboard profile emits `pair.ad`, `pair.ws`, `pair.qe`,
`pair.hk`, `pair.uj`, `pair.yi`, `pair.arrow-up-down`, and
`pair.arrow-left-right` axes plus `key.z`, `key.x`, `key.c`, and `key.v`
buttons. Positive values correspond to D, W, E, K, U, I, Up, and Right.
Adapters decide whether a pair means translation, rotation, locomotion, or
something else. The keyboard sends Home, R, and Escape through the reliable
`home`, `reset`, and `stop` signal channel.

The WebXR profile reports `head`, `left.grip`, `left.aim`, `right.grip`, and
`right.aim` tracking poses alongside controller triggers, squeezes,
thumbsticks, and buttons. These are raw local-floor device measurements;
robot adapters own calibration and retargeting.

## Use it in the host loop

```python
adapter = MyRobotAdapter(
    RobotConfig(
        platform_url="https://your-adamas-deployment.example",
        fleet_id="your-fleet-id",
        fleet_key="your-fleet-key",
        robot_id="robot-01",
        name="Robot 01",
    ),
    robot,
)

try:
    while robot_system_is_running():
        robot_system_step()
        adapter.update()
finally:
    adapter.close(timeout=5.0)
```

The first `update()` starts fleet networking in a background thread and returns
immediately. Later calls drain control onto the caller's thread and enqueue
sensor readings without waiting for delivery. `close()` is also non-blocking
unless a timeout is supplied for graceful shutdown.

HTTPS verification is resolved internally from the Python environment's
trusted roots and the CA bundle installed with the SDK. TLS trust configuration
is not part of the robot adapter's public interface.

Only one active connection may use a robot ID. If another process connects with
the same ID, its next `update()` raises `RobotConnectionError` with instructions
to stop the existing process or choose a different ID. Network interruptions
continue to retry internally.

`fleet_connected` and `connection_error` are available for health reporting.
The adapter only appears in the fleet while `is_robot_connected()` returns
`True`. The default 400 ms watchdog queues a `ControlSignal("stop")`; set
`control_timeout_s=None` in `RobotConfig` only when the robot framework already
owns an equivalent safety policy.

## Framework integration

For LeRobot, wrap its `Robot` instance in the adapter. Translate
`ControlFrame` inside `handle_control()` and call `robot.send_action()` there;
return selected values from `robot.get_observation()` in `read_sensors()`.

For Isaac Sim, wrap the articulation or simulation controller. Call
`adapter.update()` once per application or physics step. Control application and
sensor reads stay on Isaac Sim's caller thread; only fleet networking runs in
the background.

The separate `robot-sdk-examples` project contains the included dummy adapter
and a complete custom adapter implementation.
