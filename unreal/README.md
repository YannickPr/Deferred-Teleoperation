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

M2.2 covers the articulated robot-state and model-reference protocol (#14). `DeferredTeleopRuntime`
contains a schema-scoped parser for the generated description subset, and M2.3 implements
explicit right-handed metre/radian transforms, generic fixed/revolute tree FK, tool-frame
propagation, and the single canonical-to-Unreal basis boundary (#15). The parser checks source
metadata structurally and only checks visual entries as JSON objects; neither is retained or used
by FK. The API is also exposed to Blueprint without moving the mathematical core into Blueprint
graphs.

The generated SO-101 integration test checks the committed artifact's identity and structure,
without asserting FK coordinates. It does not implement the M2.2 articulated-state protocol or
the cross-language numerical oracle in M2.4 (#16). Linux and Win64 UE 5.8.2 build and Automation
evidence is recorded in the M2 guide; both targets pass the 11-test M1/M2 run, which validates
the M2.3 math core and supports closing #15.

See the [M2 canonical kinematics guide](../docs/m2/CANONICAL_KINEMATICS.md) for the contract,
headless build and Automation Test commands, and the capabilities deliberately left for later
increments.
