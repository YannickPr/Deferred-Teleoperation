# M2 articulated state (`dtt/0`)

The M2 protocol adds the root topic `robot.articulated_state`. Its payload is an
`ArticulatedRobotState` with a stable `robot_id`, a `RobotModelReference`, a canonical
`root_pose`, named `joints`, and the usual `EvidenceMetadata`.

Every joint position is a finite number of radians. JSON array order has no meaning: a
description-backed consumer maps names to the deterministic order in the generated robot
description. Duplicate and blank names are rejected. The SO-101 fixture contains its six
revolute joints, including `gripper` as a structural radian joint. A LeRobot device value in
the normalized `0..100` domain requires an explicit conversion and is not represented by this
field. The fixture value `100` is outside the structural SO-101 radian limit and is reported as a
limit violation without clamping; a numeric value alone does not establish its unit.

`description_hash` is `sha256:<64 lowercase hexadecimal digits>` over the exact bytes of
`robots/so101/generated/so101.kinematics.json`, including its final newline. The validator in
`deferred_teleop.robot_model.articulated` compares model ID, model revision, and this hash to an
explicit description and returns `valid`, deterministic `diagnostics`, and
`ordered_positions`. An invalid result has no replacement vector, model, or zero fallback.
This description-backed function is an explicit consumer boundary. Wire parsing, Field relay,
and the live Mission view preserve the model reference but do not resolve a model, validate
SO-101 geometry, or apply joint positions; a future FK consumer must invoke this validator and
keep its diagnostics visible before applying the ordered vector.

Mission exposes this state through an opt-in `mission.articulated_view_state` WebSocket. The
frame has three required nullable keys: `confirmed_robot_state`, `arrival_robot_state`, and
`target_robot_state`. When a candidate exists, the live M2 path currently fills only the
confirmed key, selecting the latest measured or fused state for the active operation
correlation and its preferred executor. Selection is ordered by
`(world_revision, observed_at, produced_at, message_id)` and retains the M1
operation/correlation ambiguity checks. State from another operation or executor is never
borrowed. An explicit grounded frame is used when one is available; an M1 `OperationIntent`
does not contain a frame or calibration reference and is not extended for M2.

The arrival wrapper requires predicted evidence and records `predicted_for`, the optional
estimated intent arrival, and the finite non-negative one-way delay. The target state requires
`OPERATOR_ASSERTED` evidence. Predictor, IK, arrival projection, and target authoring are not
implemented in this slice, so live arrival and target values are `null`. The model reference
and canonical frame remain visible to a later FK consumer, which must reject an incompatible
description rather than silently falling back.

The checked-in `live-articulated-view.json` is an idle/disconnected envelope and deliberately
has all three layer values set to explicit `null`; it is an absence fixture rather than a
populated confirmed telemetry sample. The articulated-scene demonstration reads that envelope
as-is and constructs its connected presentation state separately with the valid fixture's
`Confirmed` layer plus explicit `null` Arrival and Target layers.

The M2 endpoint is separate from the M1 endpoint and is enabled with
`--articulated-view-ws HOST:PORT`; the existing `--view-ws` port and M1 client are unchanged.
Field persists and relays articulated envelopes without converting them to the minimal M1
`RobotState` or introducing Unreal units, handedness, asset paths, ticks, or calibration data.

## Platform evidence

The compact [platform record](evidence/articulated-state-platform-validation.json) records the
three M2.2 ArticulatedView Automation tests and their exact `Success` state on both LinuxEditor
and WindowsEditor. The contextual report records 22/22 aggregate successes with build and
headless-editor exit code `0`; the three listed tests are the M2.2 slice. The record also binds the 4 C++ files and 15 articulated-state fixtures to independent
Linux-copy and Windows-overlay SHA-256 checks, and records six byte-identical M1 golden files.
The 135-test Python and 20-test targeted totals are a historical suite22 context snapshot from
the root aggregate, including other feature branches rather than an M2.2-only count. Root's
post-rebase integrated validation passes 152 Python tests; the platform record remains the dated
135/20 snapshot.

This platform evidence is a parser/DTO and contract result. It does not claim that a raw feed
state is SO-101-model-valid: an FK consumer must call the explicit description-backed validator,
compare the model triple, and keep any diagnostics visible. A populated live view exposes only
the provenance-selected confirmed state; arrival and target remain `null` in this slice. The
checked-in idle fixture has all three layers `null`. The record does not claim the full M1.7
lineage gate, an Unreal network consumer, or hardware validation. The numerical
M2.4 oracle is documented separately and complete; Jacobian and IK remain future work.

Regenerate and check the protocol artefact and model independently:

```text
PYTHONPATH=python/src python -m deferred_teleop.schema --check
PYTHONPATH=python/src python -m deferred_teleop.robot_model.so101 --check
```

The fixtures in `fixtures/m2/articulated-state/` are deterministic Python/Unreal parser
evidence. They establish strict parsing, order independence, provenance separation, model
identity diagnostics, finite values, and explicit limit failures. They make no hardware,
network execution, collision, or full M1.7 lineage claim.
