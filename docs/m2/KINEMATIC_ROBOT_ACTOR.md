# M2.5 generic kinematic robot actor

Status: **M2.5 implementation, Linux/Win64 editor evidence, and a public
synthetic capture are complete.** The broader M2 milestone remains open for
IK, preview, and VR authoring.
This document records the bounded M2.5 implementation and its evidence.

`ADeferredTeleopKinematicRobotActor` is a presentation actor for a validated
`FDttRobotDescription`. It has one rigid `USceneComponent` frame per model
link and no `USkeletalMeshComponent`. Generated cube/sphere primitives, arrow
components, and text components provide a reviewable debug view without
committing robot assets.

The public Blueprint boundary is deliberately small:

```text
InitializeModel(description, canonical_world_root, error)
ApplyState(named_joint_positions, error)
GetLinkTransform(link_name, world_transform, error)
GetToolTransform(tool_name, world_transform, error)
SetDebugFramesVisible(visible)
```

`InitializeModel` validates the description and the canonical root with the
existing kinematics library. It stores the root pose and builds topology once
for a new description. The first successful `ApplyState` evaluates the named
state with `EvaluateForwardKinematics`, converts every returned link/tool
transform through `ConvertCanonicalToUnrealTransform`, then updates all
components. The link frames remain flat children of `SceneRoot`; their
attachment hierarchy never performs FK. A second state update reuses the same
component objects.

Canonical root pose and Unreal actor placement are separate. The root pose is
the supplied absolute canonical `^world T_root`; all link and tool component
transforms are written as absolute Unreal world transforms. Canonical metres
become Unreal centimetres and the canonical Y basis is reflected only by the
shared conversion function. Actor scale must remain `(1, 1, 1)` and is
rejected otherwise.

The actor prepares every candidate transform before changing the last valid
pose. A malformed or incomplete state (including non-finite, missing, unknown,
or duplicate joint input) leaves every rendered and queryable transform as it
was and stores an explicit `LastError`. An invalid replacement model leaves
the installed model, topology, and last valid pose unchanged. Joint-limit
diagnostics remain non-fatal, matching the core FK contract.

`SemanticLayer` is an explicit `Confirmed`, `Arrival`, or `Target` property
supplied by the caller. The actor does not infer provenance from a color or
material. Materials are optional presentation properties; labels include the
explicit layer when debug names are enabled. Three actor instances can use the
same description independently because model, state, and component storage
are instance-local.

Debug mode displays XYZ arrows at every link frame, yellow arrows along every
revolute joint axis, primitive segments between parent/child link origins,
optional names, and a distinct sphere/label for each tool frame. These are
presentation helpers only. The actor contains no protocol DTO, IK, collision,
physics, hardware, or mesh-origin semantics.

## Deterministic checks

The source test file is
`Source/DeferredTeleopRuntime/Private/Tests/DeferredTeleopKinematicRobotActorTests.cpp`.
Build the runtime module and use the platform-specific command quoting in
[Canonical kinematics](CANONICAL_KINEMATICS.md#verification), replacing its
Automation selector with `DeferredTeleop.M2.KinematicRobotActor`. The argument
passed to Unreal must contain quotes immediately after the equals sign:

```text
-ExecCmds="Automation RunTests DeferredTeleop.M2.KinematicRobotActor; SoftQuit"
```

The canonical guide includes the Windows `ProcessStartInfo` recipe that
preserves these quotes. The recorded platform evidence is summarized in
[`docs/m2/evidence/kinematic-actor-platform-validation.json`](evidence/kinematic-actor-platform-validation.json):

- Linux: 19 successful tests in the full report, including the five actor tests;
- Win64: 22 successful tests in the full report, including the same five actor tests.

The full-report totals include the other M1/M2 checks from each platform run.
Only the five `DeferredTeleop.M2.KinematicRobotActor.*` tests are the M2.5
acceptance subset.

The tests cover correspondence with the shared FK result by name, canonical
metre/Y-reflection/root handling, the generated SO-101 description, independent
semantic layers, preservation after invalid state/model inputs, and stable
link/tool-frame component identity across repeated state application, including
flat absolute attachment invariants. The five tests are deterministic and do
not rely on a rendered image to establish FK correspondence.

## Reproducible visible scene

`unreal/Scripts/generate_m2_kinematic_scene.py` is the public editor recipe.
It creates the M2.5 debug materials, a Blueprint child of
`ADeferredTeleopKinematicRobotActor`, and a level containing three independent
actors with explicit `Confirmed`, `Arrival`, and `Target` layers. Each actor
loads the committed `robots/so101/generated/so101.kinematics.json`, applies a
deterministic non-zero named joint vector, places the three actors side by
side, and enables axes, origin segments, tool markers, and labels. It follows
the idempotent asset/level pattern of the M1 generator and does not modify M1
assets or commit generated binaries.

The three layer values and the named joint vector are synthetic demonstration
inputs for visual review. They do not represent measured confirmed state,
arrival prediction, or an operational target UI. Run the generator and capture
the result in the same editor process after the runtime module has compiled:
the generated actor topology is transient, so a level reopened after an editor
reload is not standalone evidence until the recipe is run again.

For a reproducible public capture, keep the Unreal Editor open throughout this
sequence:

1. Open `unreal/DeferredTeleopDemo/DeferredTeleopDemo.uproject` after the
   runtime module has compiled.
2. In the Editor Python console, execute the public generator from the project
   directory:

   ```python
   import unreal
   exec(open(unreal.Paths.project_dir() + "../Scripts/generate_m2_kinematic_scene.py", encoding="utf-8").read())
   ```

3. Wait for the level, materials, three actors, and debug primitives to finish
   rendering. Select and pilot the generated `M2 Kinematic Camera`; the recipe
   sets its world location to `(-420, 0, 220)`, pitch to `-22` degrees, and
   field of view to `55` degrees. This preserves the intended scene view.
4. After the viewport has settled, open the Unreal console in that same editor
   process and run:

   ```text
   HighResShot 1920x1080 filename="m2_kinematic_scene"
   ```

Unreal writes new captures under the project's `Saved/Screenshots` directory.
The recorded example from this workflow is
[`docs/m2/evidence/m2-kinematic-scene.png`](evidence/m2-kinematic-scene.png).
The generated level and materials stay local unless a maintainer explicitly
chooses to commit them. The scene is a synthetic three-layer demonstration;
it contains no measured telemetry, operational UI, or VR authoring path.

The capture has deliberate visual limits: projected axes and origin segments
can overlap, and the tool marker is small at the review framing. The PNG alone
does not prove forward-kinematics correctness; the five Automation tests above
perform the named-link pose correspondence and state-preservation checks.
