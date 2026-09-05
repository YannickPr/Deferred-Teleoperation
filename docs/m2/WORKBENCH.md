# Local authoring workbench

Status: **source prepared; native build, Automation execution and rendered/VR validation pending**.

`ADeferredTeleopAuthoringWorkbench` is an opt-in Editor/Development example for the local
kinematic authoring component. It initializes its fixture explicitly at BeginPlay instead of
relying on transient components created by an editor recipe. It has no network, camera-device,
robot-hardware or operation-submission path. It does not simulate contacts or dynamics.

## What the example supplies

- the existing checked-in SO-101 description and synthetic articulated-state fixture;
- an independent reference actor and an independent local candidate actor;
- a rigid `TargetHandle` SceneComponent;
- source initialization, editing, final-pose handling, local freeze, reset and cancel;
- debug trajectory lines, a requested target and achieved tool marker, a tool +Z approach arrow;
- sample-relative time labels, source revision, residuals and measured solve time;
- eight new Automation test groups (not executed by the preparation environment).

These visual actors are NOT Mission's actors. The first source is the fixture's declared
confirmed state. There is no live Arrival prediction in this example. Fixture fields retain
their parser-test provenance; the whole example is visibly labelled **SYNTHETIC FIXTURE REPLAY**.

## Place and initialize

After compiling the plugin, create a Blueprint child of `DeferredTeleopAuthoringWorkbench`.
Place exactly one instance in an isolated test level. Keep its actor transform at location zero,
identity rotation and unit scale. Enable `Initialize Synthetic Fixture On Begin Play` explicitly
on that instance; it is off by default. Keep the development overlay enabled initially.

The full repository checkout is required, including `robots/` and `fixtures/`. Missing or invalid
files leave the workbench not ready; they never cause a connection to a server or fallback to
invented robot data. A packaged standalone application is outside this example's scope.

The example reads the pinned structural model and the committed articulated view using the
existing production parsers and model binding. It sets presentation speeds of 0.5 rad/s for all
six revolute joints; these are NOT motor settings. It keeps partial IK solutions disabled.

Move the operator/Pawn to the robot, not the workbench or its spawned robot actors. The existing
robot actor uses absolute canonical world transforms. A movable table or miniature world needs
a later complete presentation-anchor adapter, not an actor-scale workaround.

## Input contract for a Blueprint desktop or VR front end

| Workbench function/property | Responsibility |
|---|---|
| `InitializeSyntheticFixture` | Explicitly reload/rebase this synthetic fixture |
| `BeginTargetEdit` | Start one local editing gesture |
| `TargetHandle` | Set its world transform while editing; move no other actor |
| `EndTargetEdit(true)` | Queue the exact final handle pose and request a local freeze |
| `EndTargetEdit(false)` | Finish editing without freezing a copy |
| `CancelLocalEdit` | Clear work, pending freeze and previous frozen copy |
| `ResetTargetToSource` | Cancel editing and return handle to selected source tool pose |
| `SetGoalMode` | Select PositionOnly or PositionPlusApproachAxis with invalidation |
| `bFreezePending` | Final solve has not yet produced the selection |
| `bHasFrozenPreview` / `FrozenPreview` | Independent local copy; never an execution command |
| `StatusText` | Current initialization, local solve or presentation diagnostic |

On grab, preserve the initial hand-to-handle offset to avoid snapping the handle to a controller
origin. The simplest adapter copies a computed world transform to `TargetHandle` only while its
own `Held` state is true. It does not attach the workbench or the robot to a controller.

On release, perform the final handle write, stop all hand-to-handle writes, then call
`EndTargetEdit`. The workbench always queues this final pose, even when the normal Tick has not
seen it. It freezes only after the corresponding current solution has also been displayed.
A failed final solve never freezes the preceding valid solution. A late, unqueued handle change
invalidates the pending selection rather than freezing a different pose from the one shown.

Tracking loss, input cancellation, focus loss and a local cancel action must clear the front
end's `Held` flag and call `CancelLocalEdit`. Reset/mode changes should happen outside an active
grab. Do not silently restart grabbing when tracking returns.

Start with PositionOnly. In approach-axis mode the handle's local +Z points along the requested
tool approach direction; rotation around this axis is free. The requested handle does not snap
to the achieved IK pose: the difference remains observable.

## Presentation

The reference and candidate can use `ReferenceMaterial` and `CandidateMaterial`; the example
tries the existing plugin materials when those properties are unset. Blueprint may replace the
debug drawing and labels with meshes/UMG without changing source data or solver behavior.

A tiny child mesh can make the handle easy to grab. Keep `TargetHandle` itself unit-scale; scale
only a child visual, or use geometry already sized appropriately. It must not simulate physics.
Use custom input/grab behavior rather than a grab system that moves the owning workbench actor.

The overlay draws the sampled tool path, not a collision-free plan or a recorded hand path.
The time labels are relative **preview motion times**, not network transit or wall-clock dates.
The candidate shows the solved end configuration. This example does not automatically animate
the robot along the timed path. An old retained path is grey and labelled as old; it cannot be
frozen as the result of a new failed input.

## Native gate

Run the existing suite and both exact manifests:

- `release/m2/authoring-required-tests.json` (eight component groups);
- `release/m2/workbench-required-tests.json` (eight workbench groups).

The report inspector rejects absent, duplicated, failed, not-run, conflicting or warning-bearing
required tests. For a full contextual report, only the two pre-existing reviewed warning cases
listed in `release/m2/expected-context-warnings.json` may be allowed explicitly with
`--allow-context-warnings`. Even an allowed warning test may not contain error evidence.

Record build/editor exit codes and exact compiled-source/report hashes separately. A report
checker success is neither source authentication nor proof of a rendered scene. Python CI does
not run Unreal. A total of 68 tests would match the current 52-test baseline plus these 16 new
groups, but the exact names and outcomes, not a guessed count, determine acceptance.

Workbench cases cover default inactivity, reload, final-result freeze, failed goal, cancellation,
stage/mode validation, late handle motion and display failure. The tests use production objects
without a viewport; PIE lifecycle and real controller behavior still need manual validation.

## Human acceptance

Verify startup after a fresh editor launch and after repeated PIE sessions; a small reachable
move; a rapid move followed by release; an unreachable target; cancel during a pending solve;
reset; approach-axis rotation; and a lost/recovered controller. Check that no stale candidate can
be frozen, the source remains fixed during editing and the world scale is not altered.

Then assess readability of the reference/candidate/path and whether the handle is comfortable to
grab without occluding the tool. Record the headset/runtime and source revision with observations.
None of these checks establishes physical safety or completion of issues #20/#21.

The lower-level API is documented in [Local goal authoring](LOCAL_GOAL_AUTHORING.md).
