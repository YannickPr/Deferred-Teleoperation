# SO-101 structural source

This directory prepares the canonical structural description used by the M2 mathematical twin.
It does **not** contain per-device calibration or a hardware command mapping.

## Source

The vendored URDF is pinned in [`source-lock.toml`](source-lock.toml):

```text
repository  TheRobotStudio/SO-ARM100
commit      385e8d7c68e24945df6c60d9bd68837a4b7411ae
path        Simulation/SO101/so101_new_calib.urdf
blob SHA-1  9552a231d8b23bed68ec15779eba620c5d875ec4
licence     Apache-2.0
```

The copy under `upstream/` is unmodified. See the root
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) before redistributing derived files.

## Separation of concerns

```text
Structural model
- links, joints, fixed transforms, axes, limits, tool frames

Device calibration (later M3 work)
- raw encoder values, homing offsets, directions and measured ranges

Protocol state
- model reference and named canonical joint values

Unreal visuals
- logical visual IDs mapped to Static Mesh assets
```

The upstream SO-101 simulation README states that LeRobot represents the gripper on a normalized
`0` (closed) to `100` (open) scale and that this mapping is not reflected by the current URDF and
MuJoCo descriptions. The generator therefore keeps the structural `gripper` joint separate from
future hardware normalization.

## Generate the canonical description

From the repository root:

```bash
python -m deferred_teleop.robot_model.so101
```

This writes:

```text
robots/so101/generated/so101.kinematics.json
```

To verify the committed generated file without modifying it:

```bash
python -m deferred_teleop.robot_model.so101 --check
```

The output is deterministic and uses:

```text
right-handed coordinates
Z up
metres
radians
quaternion XYZW rotations
```

The Unreal runtime consumes this generated description rather than parsing arbitrary URDF/XML.
CI and unit tests reject source-hash drift or a generated file that no longer matches the pinned
source and generator.

On 4 September 2026, a fresh `--check` produced the same committed artifact with Python 3.11 and
3.12 on Linux and Python 3.12.10 on Windows 11. Its SHA-256 is
`36ce321332248351f5304630a9ccc4887d6665666e17b6933e3302874735e5f2`.

## Current status

M2.1 contains the pinned source, source lock, deterministic generator, canonical generated
description, third-party notice and drift checks. M2.2 covers the articulated robot-state and
model-reference protocol (#14). M2.3 implements schema-scoped Unreal-side parsing and generic
fixed/revolute tree FK (#15): source metadata is checked structurally and visual entries are only
checked as JSON objects, with neither used by FK. The generated-description test checks identity
and structure only. M2.3 is validated on Linux and Win64 with Unreal Engine 5.8.2; the numerical
cross-language oracle (M2.4, #16), shared SO-101 FK golden fixtures, articulated rendering, IK and
hardware calibration remain separate later increments.
