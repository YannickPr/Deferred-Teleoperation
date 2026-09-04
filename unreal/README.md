# Unreal Engine bootstrap

The host project is `unreal/DeferredTeleopDemo` and contains one project plugin with one compiled runtime module:

```text
DeferredTeleopDemo/
└── Plugins/DeferredTeleop/
    └── Source/DeferredTeleopRuntime/
```

The plugin exposes the M0 protocol-version function, the M1 read-only Mission client and
visualization actor, and the M2 canonical kinematics core. Its network boundary is limited to the
local Mission view endpoint (`ws://127.0.0.1:8772` by default). It contains no Field or Robot
connection, robot assets, or hardware control.

## Local verification

These files are not compiled by GitHub CI. The M0 skeleton was verified on 4 September 2026
with Unreal Engine 5.8.2 (build 56702186), Windows 11 25H2, Visual Studio 2022 toolchain
14.44.35228 and Windows SDK 10.0.26100.0:

1. associated `DeferredTeleopDemo.uproject` with UE 5.8;
2. accepted the editor's automatic regeneration and rebuild;
3. built the Development Editor target for Win64;
4. opened the project and verified that `DeferredTeleop` was enabled;
5. observed `DeferredTeleopRuntime dtt/0 loaded` in the log;
6. called `Get Deferred Teleop Protocol Version` in a test Blueprint and confirmed `dtt/0`.

The full result and visible evidence are recorded on pull request #2. No hardware was used and
no hardware-control path was enabled.

## M1 visualization

Repository-owned assets under `Plugins/DeferredTeleop/Content` provide:

- `BP_M1DeferredStates`, a Blueprint child of the visualization actor;
- `M1_DeferredStates`, a minimal desktop example level;
- opaque confirmed, translucent arrival/target, and trajectory materials.

Regenerate them idempotently with `unreal/Scripts/generate_m1_visualization_assets.py`. The
strict client rejects malformed/version-incompatible frames, retains its last valid state across
disconnects, and exposes state, connection, and rejection events to Blueprint. See the
[reproducible M1 proof](../docs/m1/UNREAL_VISUALIZATION.md) for exact generate, build, test,
run, and capture commands.

## M2 canonical kinematics

`DeferredTeleopRuntime` contains a strict parser for the generated robot-description subset,
explicit right-handed metre/radian transforms, generic fixed/revolute tree FK, tool-frame
propagation, and the single canonical-to-Unreal basis boundary. The API is also exposed to
Blueprint without moving the mathematical core into Blueprint graphs.

See the [M2 canonical kinematics guide](../docs/m2/CANONICAL_KINEMATICS.md) for the contract,
headless build and Automation Test commands, and the capabilities deliberately left for later
increments.
