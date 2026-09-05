# M2.9a — opt-in articulated scene

M2.9a adds an Unreal presentation consumer for `mission.articulated_view_state`.
It is deliberately separate from the M1 view and the M2.5 kinematic preview.  A
scene contains exactly three persistent `ADeferredTeleopKinematicRobotActor`
references, labelled `Confirmed`, `Arrival`, and `Target`.  The scene never
spawns, replaces, or recreates those actors at runtime.

The scene actor owns a `UDeferredTeleopMissionClientComponent` configured for
`EDeferredTeleopMissionWireMode::ArticulatedView`.  A standalone mission client
keeps `LegacyView` as its default, so existing M1 levels continue to consume
`mission.view_state` with their existing delegate and state fields.  The wire
mode is snapshotted when a socket connects.  Changing the editable mode while
connected causes the client to close that socket, reset source ordering, and
reconnect with the new mode; a callback from the replaced socket is ignored by
its connection generation.

## Local catalogue and exact bytes

The binding is one explicit local catalogue entry:

```text
RobotId
DescriptionFilePath
ExpectedFrameId
ExpectedCalibrationVersion
```

Only `ConfigureBinding` and `ReloadLocalDescription` read
`DescriptionFilePath`.  A wire model reference is compared with the cached
catalogue and can never cause a file read or promote a remote description.
The loader keeps the bytes returned by `LoadFileToArray`, requires a strict
UTF-8 round trip, computes `sha256:<64 lowercase hexadecimal digits>` with the
private OpenSSL backend, and parses those same bytes with
`ParseRobotDescriptionJson`.  `ConfigureBinding` performs the initial load.
Once configured, an explicit `ReloadLocalDescription` is the operation that
may replace the cache; its candidate includes the binding fields and parsed
bytes as one transaction.  An unsuccessful load restores the previous binding
and pose untouched and reports `InvalidModel`.  A successful reload whose robot,
frame, calibration, or model key changes clears the old layer caches and waits
for a matching view before showing the actors again.

The OpenSSL dependency is private to `DeferredTeleopRuntime` and is enabled for
Linux and Win64.  The generic `FPlatformMisc::GetSHA256Signature` path is not
used because its generic implementation is a deliberate `checkf` stub in UE
5.8.2.  Other targets report that the articulated SHA-256 backend is
unavailable instead of comparing a substitute digest.

## Validation and transaction

Before an actor is touched, the scene validates robot identity, model id,
revision, exact raw description hash, canonical root, frame id, and calibration
version.  Wire joint strings are compared with the description's `FName`
strings using exact case before any `FName` conversion.  The set must contain
all revolute joints once, no fixed or unknown joint, and no non-finite value;
the resulting `FDttNamedJointPosition` array is ordered by the description.
Forward kinematics and every canonical-to-Unreal conversion are also prepared
before `InitializeModel` or `ApplyState`.
The direct `ApplyArticulatedViewState` entry point repeats the parser's layer-role
guards: Confirmed is `MEASURED`/`FUSED`, Arrival is `PREDICTED` with a strictly later
`PredictedFor` and finite delay in `[0, 86400]`, and Target is `OPERATOR_ASSERTED`.
The identity correction also makes protocol and robot-description literals exact in the two
existing C++ parsers, `Private/Articulated/DeferredTeleopArticulatedViewParser.cpp` and
`Private/RobotModel/DeferredTeleopRobotModelJson.cpp`.  This preserves the standalone client's
`LegacyView` default and existing M1 behavior.  JSON object field-name
lookup still follows Unreal's case-insensitive alias behavior in this slice; exact JSON key
validation and parser conformance remain tracked by [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).

For a valid candidate the scene performs `InitializeModel` and `ApplyState` on
the existing layer actor.  Each layer retains a last-good tuple containing its
description, canonical root, ordered joints, and original evidence.  If the
candidate initialization or application fails, that tuple is reinstalled and
reapplied.  A failed restoration is a visible critical status and hides the
actor so a candidate frame cannot remain presented.

Changing a valid root is allowed because the wire binding has no expected root
transform.  The root is checked only for canonical finiteness and quaternion
normalization.  A different root therefore exercises the same rollback path
without comparing against an invented root value.

Confirmed `MEASURED` or `FUSED` values outside a declared joint limit are
reported as `MeasuredOutlier`; the last pose is preserved and the diagnostic
contains the value and its limit.  Arrival and Target out-of-limit values are
`InvalidLayer`.  No value is clamped.

## Presence, stale state, and evidence

An explicit `null` Arrival or Target is unavailable and hidden.  It leaves its
internal last-good tuple available for a later valid update.  A malformed
message, an invalid layer, a rejected hash, or a disconnection keeps a
last-good actor visible with `STALE/DEGRADED`; a layer without a last-good pose
remains hidden.  A valid layer updates only that layer's cache.

`FDttArticulatedLayerStatus` exposes availability, cache presence, visibility,
degradation and critical state, a reason, declared provenance, source ids,
`ObservedAt`, `ProducedAt`, `WorldRevision`, `ModelKey`, diagnostics, and a
monotonic `ReceiptAgeSeconds`.  It also preserves `FreshUntil` and the
evidence `ModelVersion` (with presence flags), plus Arrival's `PredictedFor`,
optional estimated intent arrival, and one-way delay.  Receipt age is computed
from the local receive clock and is never substituted into the evidence
timestamps.  A `PREDICTED` arrival and an `OPERATOR_ASSERTED` target keep those
declared provenances; no status converts either into a measurement.

The checked-in three-layer fixture is labelled `FIXTURE REPLAY / SYNTHETIC
DEMONSTRATION` by the editor recipe.  Its Confirmed evidence still carries the
fixture's declared `MEASURED` provenance, but that label does not claim live
telemetry.  The checked-in `live-articulated-view.json` is an idle/disconnected
envelope with all three layer values explicitly `null`.  Test group 5 reads those
bytes without modifying the fixture, combines its envelope with the valid fixture's
Confirmed state, and applies explicit `null` Arrival and Target layers through the
production C++ method to exercise the documented connected shape.

The editor recipe is
`unreal/Scripts/generate_articulated_scene.py`.  It reads the fixture as raw
UTF-8 bytes and passes its text through `ApplyArticulatedViewJson`, which uses
the same strict C++ parser and transaction as the Mission callback.  It creates
only primitive desktop presentation assets, three persistent kinematic actors,
and a scene controller.  It does not add VR, hardware, IK, skeletal meshes, or
a Target authoring path.  The generated level and assets are local editor
products.

## Native evidence and desktop capture

The compact [platform record](evidence/articulated-scene-platform-validation.json) is complete
for this bounded M2.9a slice and binds exactly 63 selected source, fixture, robot-model, and
project files. LinuxEditor and Win64Editor each record build, editor, and automation exit code
`0` with the same 50-test contextual result: 48 `Success`, 2 `SuccessWithWarnings`, and zero
failed, in-process, or not-run tests. The two warnings are expected and come from the missing
local model and duplicate source-sequence negative cases. The seven scene tests are included in
that 50-test total; the record keeps their individual warning attribution and source hashes.

The separate final desktop capture is an actual Unreal render at 1920x1080 using
`RenderOffscreenVulkan`. It is labelled `SYNTHETIC FIXTURE REPLAY`: the scene replays committed
fixture bytes and does not represent measured telemetry, a live robot, or operational readiness.
The desktop presentation uses the real runtime statuses for its labels and lays out visual
components from the three layers separately; billboards and the camera frame are hidden for
readability. Because the fixture roots are zero, this image illustrates layer separation and is
not a pose/root oracle or a pixel-identical output of the public generator alone. The evidence
record binds the presentation wrapper hash without exposing a machine-local path.
The generator is bound by SHA-256
`295000d390212e028e6ef4071b83b5640834327a340862a1e347dc9816bf43f1`, and the committed image
[`m2-9a-articulated-scene.png`](evidence/m2-9a-articulated-scene.png) is bound by SHA-256
`690ca1f5b0fb2233e5e4dc8a56d8b8bce5976284a4b91cf7c15a69cce9a79b3a`. The capture is desktop
presentation evidence only; it adds no VR or hardware path.

JSON field-name exactness remains a separate parser-conformance concern tracked by
[issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47); the native
`FName` topology lookup remains case-insensitive. The full M2.9 milestone and #20/#21 remain
open.

## Deterministic checks

The source test file is
`Source/DeferredTeleopRuntime/Private/Tests/DeferredTeleopArticulatedSceneTests.cpp`.
It contains exactly seven grouped automation tests:

1. exact SHA-256 for empty bytes, `abc`, and the committed raw SO-101 fixture;
2. reference validation, description order, and case-sensitive joint names;
3. robot/model/hash/root/frame/calibration and missing-evidence rejects preserving
   last-good state, plus failed catalogue reloads (missing and invalid UTF-8);
4. valid root change plus forced candidate failure restoring root, joints, and pose;
5. the three fixture layers, explicit null hiding, and measured outlier handling;
6. per-source sequence ordering, reconnect reset, and old-generation rejection;
7. invalid/disconnected stale presentation with original evidence and separate receipt age.

After the runtime module has been built for the target, run:

```text
Automation RunTests DeferredTeleop.M2.ArticulatedScene; SoftQuit
```

This bounded slice is complete for its native validation and desktop replay scope.  It does not
close the full M2.9 milestone, #20, or #21: there is no live Target authoring, command path, hardware loop,
teleoperation control, VR interaction, IK, collision model, or operational
readiness claim.
