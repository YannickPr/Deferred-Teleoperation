# M2.8b — local goal authoring component

Status: **source prepared; native compilation and headset validation pending**.
Related: issues #20 and #21. This is not closure of either issue or of M2.

This slice connects the existing model binding, IK and time-sampled preview. It adds no
kinematics algorithm, protocol revision, network write, physical device, or execution authority.

```text
explicit confirmed snapshot + local model binding
                    |
                    v
       GoalAuthoringComponent (C++)
                    ^
 desktop/VR target --| one latest-wins goal slot
                    |
              existing DLS IK
                    |
              existing BuildPreview
                    |
       separate local candidate + trajectory (Blueprint)
```

## API and ownership

Add `UDeferredTeleopGoalAuthoringComponent` to an operator/test Blueprint. It exposes:

| Function or event | Meaning |
|---|---|
| `ConfigureFromConfirmedView` | Explicitly freeze a validated confirmed source and settings |
| `ConfigureFromConfirmedJson` | Same operation through the existing strict JSON parser |
| `GetSourceModelAndState` | Obtain the validated model, canonical root and named initial joints |
| `QueueCanonicalGoal` | Replace the pending target in canonical site coordinates |
| `QueueUnrealGoal` | Validate unit-scale target/anchor, convert once, and replace the pending target |
| `OnPreviewUpdated` | A fresh locally accepted preview, with original source evidence preserved |
| `OnAuthoringDiagnostic` | Configuration/rejection/solve diagnostic |
| `HasCurrentPreview` | Candidate matches the latest local input and selected source |
| `CopyCurrentPreview` | Freeze a copy locally; **does not submit an operation** |
| `ClearCandidate` | Drop pending work/candidate and restore warm start to the selected source |

The component never mutates a scene actor. The existing articulated scene owns its Confirmed,
Arrival and Mission Target actors. The operator Blueprint owns a **different local candidate
actor**. Never bind both writers to the same Target actor. The UI may hide the remote Target while
editing the local candidate, but must not overwrite its data or provenance.

## First source boundary: confirmed only

This increment starts from an explicitly selected, frozen confirmed articulated snapshot. It
supports declared `MEASURED`/`FUSED` evidence, uses the existing exact local-description binding
and description-backed validator, and refuses structural-limit outliers. A failed rebase disables
authoring from the old source; the old preview may remain as a visibly stale drawing.

**This is not yet Arrival-based operation authoring.** The current preview source enum has no
`PREDICTED` member. Do not relabel an Arrival prediction as a measurement to bypass it. A later
bounded extension must explicitly carry predicted-source provenance and the arrival manifest,
then validate an Arrival-started preview before the full #21 integration gate closes.

The view key stored in `SourceReference.SourceMessageId` has the form
`<mission-source>/view/<sequence>`. It is a reference to the selected view, not a fabricated raw
sensor-message UUID and not a substitute for the open full M1.7 lineage gate.

A checked-in fixture is a **synthetic replay**, even when its fields declare MEASURED evidence to
test the parser. Preserve the fields and display `SYNTHETIC FIXTURE REPLAY`; never claim live
robot evidence. `HasCurrentPreview` means input/source consistency, not freshness, collision
freedom, physical executability, or permission to act.

## Time, threading and failure behavior

All methods and events run on the Game Thread. Tick checks a monotonic presentation clock and
starts at most one solve per configured interval (default 20 Hz, configurable in [1,90]). New
controller samples replace pending data rather than creating a FIFO. A long frame does not cause
a burst of catch-up solves. No worker thread, timer per joint, or physics tick is introduced.

The existing IK evaluation cap and preview sample cap remain in force. This is **not** a
wall-clock guarantee: measure `LastSolveMilliseconds` and total VR frame time on the real PC.
Move expensive work off the Game Thread only after profiling and with an explicit immutable
request/result generation contract.

On new input, `HasCurrentPreview` becomes false immediately. A rejected input or failed solve
retains `LastValidPreview` for drawing but cannot be frozen. `LastIKResult` and `LastDiagnostic`
explain the latest attempt. Partial solutions require explicit opt-in through the existing
preview settings; the default is refusal. Accepted previous joint solutions warm-start IK, but
**every preview still starts at the original source snapshot**, not the previous target.

A successful rebase clears the candidate. Rebase is explicit, never triggered silently while the
operator is holding the goal. Blueprint listeners may queue/rebase during callbacks; preview
notifications carry a copy and stale diagnostic broadcasts are suppressed.

## Settings for the first no-hardware test

Use `arm`, `gripper_frame_link`, local tool approach axis `(0,0,1)`, the existing IK defaults,
`MaximumSolveRateHz=20`, and `bAcceptPartial=false`.

Provide a preview speed for **all six revolute joints**. For an illustrative desktop/VR test,
`0.5 rad/s` for each joint is adequate as a presentation parameter, not a hardware setting. The
gripper speed entry is required by the existing builder even though arm IK leaves the gripper
unchanged. Keep the existing 30-second and 128-sample upper bounds.

The target orientation rotates `LocalToolApproachAxis` into the desired canonical site direction.
In the default configuration, the target's local +Z is the tool approach direction. PositionOnly
ignores orientation; PositionPlusApproachAxis leaves roll around the approach axis free.

## Blueprint wiring recipe

Start in an isolated example level. Keep a text label `LOCAL KINEMATIC CANDIDATE — NO COMMAND`.
Do not modify the historical M1 example or golden fixtures.

1. Add the authoring component, a unit-scale target `SceneComponent`, a separate
   `ADeferredTeleopKinematicRobotActor`, a polyline renderer and a status widget.
2. Select a valid articulated view from the existing Mission client, or explicitly configure a
   local fixture via `ConfigureFromConfirmedJson`. Use the binding from the articulated scene:
   robot `so101-follower-1`, frame `field-world`, calibration `field-cal-1`, and the checked-in
   `robots/so101/generated/so101.kinematics.json`. Resolve the path locally, not from a wire value.
3. On successful configuration, call `GetSourceModelAndState`; then `InitializeModel` and
   `ApplyState` on the **local candidate actor**. Set its semantic layer to Target and its material
   to the chosen blue preview material. These are presentation choices, not evidence promotion.
4. Convert `StartToolTransform` with the existing kinematics conversion node and place the target
   handle there. Initially use an **identity site-to-Unreal anchor** and unit scale throughout.
5. On a changed target while grabbed, call `QueueUnrealGoal(TargetWorld, Identity, Mode)`. Queue
   the final pose once when releasing the handle; stop feeding unchanged targets continuously.
6. In `OnPreviewUpdated`, first check `HasCurrentPreview`, then apply the accepted
   `LastIKResult.JointPositions` to the local candidate actor. Draw each preview sample's
   `ToolTransform` through the existing canonical-to-Unreal conversion. Reuse line components;
   do not rebuild robot topology for every hand sample.
7. Draw source samples as a polyline and add a few labels from `TimeSeconds`. Any smoothed spline
   is a visual interpolation only; retain the original sample points for inspection. Show target
   versus achieved tool position, residuals, status, source revision and solve duration.
8. After an invalid goal, retain a grey/stale drawing or hide it, and disable Freeze. Never move
   the handle automatically to the achieved pose and thereby hide an IK error.
9. Freeze calls `CopyCurrentPreview` into a Blueprint-owned value. If a final hand sample is
   pending, wait for its matching preview update rather than copying the previous candidate.
   There is no Submit-to-Field button in this increment.
10. Tracking loss clears pending/freeze intent; call `ClearCandidate`. New source selection is
    an explicit Rebase action, preferably after releasing the handle.

The existing kinematic actor uses absolute canonical-root world transforms. Moving or scaling
its Actor root is **not** a supported way to relocate the robot. The input API can remove a rigid
presentation anchor, but a nonidentity display stage also needs the same mapping on every visual
output; that scene feature is not supplied here. Use identity for the first test and move the
operator viewpoint instead.

## Fixture initialization in PIE is explicit

`unreal/Scripts/generate_articulated_scene.py` creates an editor-only same-process fixture view.
It does not create this authoring Blueprint. Its transient link topology and loaded view are not
an assurance that a saved map will reinitialize during PIE or after reopening the editor.

For the first Blueprint harness, add an editable `FixtureViewJson` string and populate it from
`fixtures/m2/articulated-state/valid-articulated-view.json` using the editor Python console or
manual paste. At BeginPlay, explicitly configure/replay that fixture and model binding, then
initialize the separate local candidate. Keep auto-connect off in fixture mode. A live mode must
wait for the existing client's valid view event instead; do not silently fall back to a fixture.

A public fixture launcher should resolve repository-relative paths at runtime, not save a user's
absolute filesystem path in a committed binary asset. The first capture must state whether it
used fixture replay or the live Mission endpoint.

## Native test scope and evidence

Eight grouped Automation tests live under `DeferredTeleop.M2.GoalAuthoring`. The exact required
list is committed in `release/m2/authoring-required-tests.json`:
configuration/zero preview; latest-wins/rate cap; rejected goals; rebase/model guards;
Unreal/canonical conversion and scale refusal; warm-start/source preservation; copy/clear/settings;
clock rollback and missing source.

These tests are **written but not executed by the source-preparation environment**. Run them
and the existing suite in UE 5.8.2 on the local build machine. The portable helper checks the
resulting report without claiming to compile Unreal:

```bash
python -m deferred_teleop.unreal_report \
  --report /path/to/fresh/AutomationReport/index.json \
  --required release/m2/authoring-required-tests.json
```

Required tests must appear exactly once with `Success`. Any contextual failed/not-run/in-process
test fails the check. Contextual warning tests are listed for human review, not hidden. The tool
rejects duplicate JSON keys and nonstandard constants. Its report hash is not authentication or
proof of the compiled source: keep build/editor exits, source hashes and platform details beside it.

See [the PC/Codex and manual VR handoff](../handoff/PCVR_2026_09_05.md).
