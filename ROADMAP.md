# Roadmap

The roadmap is organized around runnable vertical slices. Dates are intentionally secondary to demonstrated exit criteria.

## M0 — Public foundation

Status: **complete**

- public documentation and status matrix;
- minimal Python package and CI;
- one Unreal runtime plugin module;
- canonical vocabulary, units, frames, time and provenance;
- initial threat model and safe defaults;
- experimental `protocol/v0` namespace with conformance fixtures.

M0 is not a release. It completed after local Unreal Engine 5.8 verification and review of the
bootstrap pull request. The first runnable release target remains M1.

## M1 — Delay-tolerant dummy

First runnable release target.

```text
OperationIntent(PressButton)
-> one-node OperationPlan
-> TaskAssignment
-> ExecutionContract
-> dummy SkillInvocation
-> ExecutionEvent
```

The test harness will inject delay, blackout windows, duplication, reordering, retransmission and crash points. Delivery is at-least-once; application effects must be idempotent.

## M2 — Mathematical SO-101 twin in Unreal

- textual robot description;
- forward kinematics, Jacobian and constrained damped-least-squares IK in C++;
- separate rigid link meshes, without a skeletal mesh;
- Blueprint-accessible target authoring and debugging;
- confirmed, arrival and target representations;
- trajectory lines and temporal markers.

## M3 — Autonomous delayed button press

First five-month success boundary.

- known and calibrated button fixture;
- independently instrumented button state;
- measured SO-101 mirror in Unreal;
- autonomous local `PressButton` skill;
- explicit interruption/cancellation semantics;
- delayed operation admission, execution and reconciliation;
- both robot-estimated and independent hardware success evidence.

## M4 — Robot-agnostic intent and Field assignment

Stretch goal for the first cycle.

- VR-designated target rebound against the Field operational estimate;
- typed procedure templates and capability registry;
- robot-independent `OperationIntent`;
- Field grounding, assignment and deterministic validation;
- optional non-authoritative LLM proposal adapter.

## M5 — Adapt, acquire context, or hold

- bounded autonomy envelope and plan revisions;
- evidence ladder for context acquisition;
- targeted world deltas and incident bundles;
- tiered robot/Field/Mission telemetry;
- assistance request containing selected evidence and attempted recovery.

## Later research tracks

- reusable Unreal Simulation Worker and sim-to-real identification;
- LeRobotDataset export and learned residual policies;
- prediction calibration and uncertainty UX;
- multi-robot coordination groups and resource leases;
- richer world reconstruction;
- Microduck locomotion as a second morphology.
