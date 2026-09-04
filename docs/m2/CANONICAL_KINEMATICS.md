# Canonical kinematics in Unreal

M2.2 adds a small, robot-independent C++ kinematics core to
`DeferredTeleopRuntime`. It consumes the generated `dtt.robot-description/0`
JSON representation; it does not parse arbitrary URDF at runtime and it does
not command hardware.

## Contract

The core uses one explicit convention until the Unreal presentation boundary:

```text
right-handed, Z-up
metres and radians
quaternion XYZW
^A T_C = ^A T_B * ^B T_C
double precision
```

Robot descriptions contain named links, fixed or revolute joints, semantic
joint groups, and tool frames. Validation rejects malformed transforms,
non-unit axes, duplicate names, invalid references, multiple parents, cycles,
disconnected links, and invalid group membership.

Forward kinematics accepts one finite named position for every revolute joint.
It traverses the validated tree in a deterministic order and returns named
world transforms for every link and tool. Position-limit violations are
reported without silently clamping the requested state.

## Unreal boundary

Canonical transforms cross into Unreal through one conversion function. The
basis change is `S = diag(1, -1, 1)`, translation is converted from metres to
centimetres exactly once, and scale remains `(1, 1, 1)`. The reverse function
exists for authoring and round-trip tests. Negative Actor scale is not used to
repair handedness.

Blueprint callers can parse a canonical JSON string, validate a description,
evaluate FK, and convert transforms through
`UDeferredTeleopKinematicsLibrary`. File selection and asset packaging stay
outside the mathematical core.

## Verification

Unreal Automation Tests under `DeferredTeleop.M2` cover:

- rigid-transform composition;
- generic branched fixed/revolute FK and tool propagation;
- invalid tree, axis, transform, group, and joint-state inputs;
- explicit limit diagnostics without clamping;
- canonical JSON parsing;
- signed quarter-turn basis cases and canonical/Unreal round trips.

The reference verification platform is Windows with Unreal Engine 5.8. The
plugin can be compiled without launching the editor:

```powershell
RunUAT.bat BuildPlugin `
  -Plugin=C:\path\to\DeferredTeleop.uplugin `
  -Package=C:\path\to\package `
  -TargetPlatforms=Win64 `
  -StrictIncludes
```

Run the deterministic tests headlessly with a host project:

```powershell
UnrealEditor-Cmd.exe C:\path\to\DeferredTeleopDemo.uproject `
  -unattended -nop4 -nosplash -NullRHI `
  -ExecCmds="Automation RunTests DeferredTeleop.M2; Quit" `
  -TestExit="Automation Test Queue Empty"
```

These tests establish rigid-transform and FK behaviour only. Collision,
dynamics, device calibration, motion planning, safety validation, articulated
rendering, Jacobians, and IK remain outside this increment.
