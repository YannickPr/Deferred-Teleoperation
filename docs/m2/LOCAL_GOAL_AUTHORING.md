# M2.8b — local goal authoring component

Status: **source prepared; native compilation and headset validation pending**.
Related: #20, #21 and #55. This does not close the full M2 milestone.

For a minimal self-initializing synthetic example, start with the [workbench](WORKBENCH.md).
This document describes the reusable lower-level component, independent of input bindings.

```text
explicit confirmed snapshot + exact local model binding
                         |
                 GoalAuthoringComponent
                         ^
          desktop/VR canonical goal (latest only)
                         |
                  existing DLS IK
                         |
                  existing BuildPreview
                         |
                 local candidate + samples
```

## Ownership and API

`UDeferredTeleopGoalAuthoringComponent` owns local source selection and candidate computation.
It does not mutate scene actors, open a transport or issue an operation. Its output is a local
kinematic preview, not a hardware MotionPlan.

| API | Meaning |
|---|---|
| `ConfigureFromConfirmedView` | Freeze a validated source and settings explicitly |
| `ConfigureFromConfirmedJson` | Same boundary through the existing strict parser |
| `GetSourceModelAndState` | Get the validated model, canonical root and named initial joints |
| `QueueCanonicalGoal` | Replace pending target in canonical site coordinates |
| `QueueUnrealGoal` | Validate rigid target/anchor and convert once to canonical coordinates |
| `OnPreviewUpdated` | Accepted current local result with original source evidence |
| `OnAuthoringDiagnostic` | Rebase/input/solve diagnostics |
| `HasCurrentPreview` | Latest input and selected source match this candidate |
| `CopyCurrentPreview` | Copy a current candidate locally, submitting nothing |
| `ClearCandidate` | Drop pending/candidate work and reset warm seed to the source |

A local candidate must use a different actor from the existing articulated scene's Mission Target.
Rendering can hide a remote Target while editing, but cannot overwrite its provenance or data.

## Confirmed-only source in this slice

The first source is a selected, frozen confirmed articulated snapshot with declared MEASURED or
FUSED evidence. Exact local description binding and the description-backed state validator are
reused. Structural-limit outliers are refused. A failed rebase disables the prior source; an old
preview may remain for drawing but is not current and cannot be copied as a new result.

This is not Arrival-based authoring. The current preview source enum has no PREDICTED member.
That follow-up needs explicit predicted provenance, an arrival time and a compatible manifest;
never relabel a prediction as a measurement to make an enum accept it.

The SourceMessageId value `<mission-source>/view/<sequence>` identifies the selected view, not
an invented raw sensor UUID. It does not close the full M1.7 lineage gate. A committed fixture
remains a SYNTHETIC FIXTURE REPLAY even if its test fields declare measured evidence.
`HasCurrentPreview` does not assert physical safety, current terrain knowledge or execution approval.

## Temporal behavior

All mutating methods and delegates run on the Game Thread. Tick uses a monotonic presentation
clock and at most one solve per interval (20 Hz by default, configurable from 1 to 90). Inputs
replace one pending slot; long frames cause no catch-up burst. The existing evaluation/sample
caps remain in force, but they are not wall-clock guarantees: measure LastSolveMilliseconds
and VR frame timing on the native PC.

Every new input immediately invalidates current acceptance. Failed inputs/solves retain the
last drawing but not permission to freeze it. Partial solutions require explicit opt-in;
by default they are refused. Previous accepted IK joints warm-start the solver, while **every
preview still begins at the original selected source**, not the previous target.

Successful source rebase clears the candidate. Do not rebase silently while a target is grabbed.
Delegate notifications use a copy; listeners that queue/rebase during callbacks cannot rewrite
that notification or cause a stale success diagnostic to overwrite their newer state.

## Settings and frames

Start with group `arm`, tool `gripper_frame_link`, local approach axis `(0,0,1)`, existing IK
defaults, partial results disabled and an explicit preview speed for all six revolute joints.
The example's 0.5 rad/s is a presentation parameter, not a motor speed. Gripper state remains
unchanged but needs a preview speed entry under the current builder contract.

The goal is in canonical site coordinates, not root-relative coordinates. The goal orientation
rotates the configured tool axis into the desired canonical approach direction. PositionOnly
ignores orientation. Approach-axis mode leaves roll free.

Use identity site/display anchoring and unit scale for the first scene. QueueUnrealGoal can remove
a rigid input anchor, but the existing visual actor has absolute world link transforms; a complete
nonidentity presentation stage must map every visual output consistently as well. Moving/scaling
the robot actor is not a substitute. Visual child geometry can be resized without scaling the
mathematical handle, actor or robot state.

## Validation boundary

Eight exact component test names are in `release/m2/authoring-required-tests.json`. They cover
source setup, latest-wins/rate control, failure retention, rebase/identity, frame/scale conversion,
warm-start/source invariance, copy/clear/settings and clock faults. These tests have been written
but are not run by the source-preparation environment.

The optional workbench adds eight tests in `release/m2/workbench-required-tests.json`. The portable
report checker requires exact Success states, validates per-row counters/event entries and checks
supplied aggregate counters. Contextual warnings require an explicit reviewed name list; required
tests cannot receive warning exemptions. Build exits, source bindings, rendered evidence and VR
observations remain separate requirements.

No existing protocol, hardware, FK/IK algorithm or historical fixture is changed by this slice.
