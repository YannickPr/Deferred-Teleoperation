# Experimental protocol namespace `dtt/0`

The protocol is intentionally incomplete and unstable. M1 defines only the strict semantic spine
needed by the delayed dummy vertical slice.

## Current contract

`message-envelope.schema.json` is deterministically generated from strict Pydantic v2 wire DTOs.
It describes transport-independent metadata around typed payloads, but does not define delivery,
storage or physical-execution semantics by itself. Regenerate it with
`python -m deferred_teleop.schema`; CI verifies it with `--check`.

The constrained chain is:

```text
OperationIntent -> GroundedOperation -> OperationPlan (one TaskNode)
-> TaskAssignment -> ExecutionContract -> ExecutionEvent
```

M2 adds the independent root topic `robot.articulated_state`. Its payload is
`ArticulatedRobotState`:

```text
robot_id
model_reference: { model_id, model_revision, description_hash }
root_pose
joints: [{ joint_name, position_radians }]
evidence
```

`description_hash` is a `sha256:` hash over the exact generated robot-description bytes.
Joint names are semantic and array order is not meaningful; description-backed validation
reports unknown, fixed, missing, duplicate, non-finite, and out-of-limit joints without
clamping or substituting a default vector. The pinned SO-101 state has six structural
revolute joints, including `gripper` in radians. LeRobot's separate normalized `0..100`
device command domain requires an explicit conversion and is never placed in
`position_radians`; the number `100` is rejected by the SO-101 structural limit, while a
numeric value alone does not prove its unit.
This description-backed validation is a separate consumer boundary. The wire DTO and live
Mission path preserve references and provenance without resolving SO-101 geometry or applying
joint positions; a future FK consumer must invoke the validator and expose its diagnostics.

The M2 Mission frame is a separate `mission.articulated_view_state` WebSocket message. It
requires nullable `confirmed_robot_state`, `arrival_robot_state`, and `target_robot_state`
keys. M2 currently emits only a confirmed measured/fused state; arrival and target remain
explicitly null until predictor and IK authoring work exists. Arrival records predicted timing
metadata and target records operator assertion when those layers are later populated. The M1
`RobotState`, `RobotForecast`, `SiteSnapshot`, and `mission.view_state` structures remain
unchanged.

Operation states are `DRAFT -> SUBMITTED -> RECEIVED_BY_FIELD -> ADMITTED | HELD | REJECTED`.
The M1 contract transition validator accepts only:

```text
RECEIVED -> ACCEPTED | HELD
ACCEPTED -> DISPATCH_RECORDED | CANCELLED
DISPATCH_RECORDED -> RUNNING
RUNNING -> SUCCEEDED | FAILED | HELD | CANCELLED
```

Inter-site assumptions:

- messages may be delayed, duplicated, reordered or retransmitted;
- delivery is at-least-once when connectivity and retention allow;
- consumers must persist deduplication/execution state where duplicate physical effects matter;
- expiry and supersession are application-level decisions;
- no message is trusted solely because it is syntactically valid.

## Versioning

- Protocol identifier: `dtt/0`.
- Breaking changes are allowed during the experimental phase.
- Fixtures are compatibility evidence, not a promise of long-term stability.
- A broader version freeze will not occur before the delayed physical button task has run end to end.
