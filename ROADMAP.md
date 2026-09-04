# Roadmap

The roadmap is organized around runnable vertical slices. Calendar dates are secondary; a
milestone closes only when its stated evidence gate passes. The authority boundary remains:
Mission authors and reconciles, Field admits and coordinates, and Robot executes locally and
reports physical evidence.

## M0 — Public foundation

Status: **complete**

- public documentation and status matrix;
- minimal Python package and CI;
- one Unreal runtime plugin module;
- canonical vocabulary, units, frames, time and provenance;
- initial threat model and safe defaults;
- experimental `protocol/v0` namespace with conformance fixtures.

M0 is not a release. It completed after local Unreal Engine 5.8 verification and review of the
bootstrap pull request. The first runnable release target was M1.

## M1 — Delay-tolerant dummy

Status: **complete for `v0.1.0` (historical)**

```text
OperationIntent(PressButton)
-> one-node OperationPlan
-> TaskAssignment
-> ExecutionContract
-> dummy SkillInvocation
-> ExecutionEvent
```

The test harness injects delay, blackout windows, duplication, reordering, retransmission and
crash points. Delivery is at-least-once; application effects are idempotent on the dummy path.

The deterministic golden session and fourteen-profile adversarial matrix are implemented.
Portable Python checks run on Linux in CI; Unreal Engine 5.8.2 was built and exercised on
Windows, the reference Unreal platform for `v0.1.0`. This release proves transport, persistence,
replay and reconciliation for the constrained dummy scenario. It does not claim that a stale
world, an external physical effect or multiple concurrent operations are resolved correctly.

## M1.7a — Bounded Mission-view selection correction

Status: **complete** — [PR #30](https://github.com/YannickPr/Deferred-Teleoperation/pull/30)

M1.7a is a small correction to the released Mission selection path. It rejects ambiguous
operation/correlation mappings among the intents in Mission's outbox when constructing the view,
without introducing the full lineage model or changing the `dtt/0` golden and Unreal evidence.
Mission chooses the latest intent globally by
`MAX(created_at, str(message_id))`, then filters the selected intent's correlation.

The correlation mapping is valid only when one `operation_id` maps to one `correlation_id` and one
`correlation_id` maps to one `operation_id`. Duplicate deliveries with the same operation and
correlation are allowed when this mapping remains unambiguous. Distinct operation IDs sharing a
correlation, or one operation using multiple correlations, raise the explicit
`MissionViewSelectionError`; Mission never chooses one of the conflicting operations.

The selected operation and correlation are then used as filters for its snapshot, arrival forecast
and terminal event. A frame, calibration or robot mismatch in a snapshot, forecast or target
projection makes that layer absent/unknown; terminal events have no frame and are not subject to a
frame mismatch check. Mission does not fall through to another operation or fill a layer with a
different item. This rule is intentionally bounded and deterministic.

The regression suite verifies mapping validation in the selected Mission view, global latest-intent
selection, explicit mapping errors, duplicate handling and snapshot/forecast/terminal filters.
All 72 Python tests and CI passed with the historical `dtt/0` golden session unchanged; no Unreal
source or wire schema changed. See the [selection contract](docs/m1/MISSION_OPERATION_SELECTION.md).
This does not close M1.7 or establish complete multi-operation lineage.

## M1.7 — Causal coherence across operations

Status: **planned after M1.7a; not closed by this documentation**

M1.7 extends the bounded correction into a full lineage proof before adding broader autonomy.
Every confirmed, arrival-belief and target branch must carry enough lineage to identify the
operation, intent revision, source observation and model/forecast assumptions that produced it.
Mission must keep branches separate when messages arrive late, duplicated or out of order.

The first fixture contains two concurrent operations, two target branches and an intervening
model or calibration revision. The replay deliberately reorders their intents, forecasts and
terminal events. A view either assembles causally compatible elements or exposes an explicit
incompatibility/unknown state; it never borrows a result from another operation to complete a
missing field.

M1.7 closes only when a deterministic replay demonstrates all of the following:

- no cross-operation target, forecast or terminal-result association;
- operation and intent-revision lineage is preserved through reconnect and duplicate delivery;
- a missing or incompatible branch remains missing or incompatible;
- the same assertion is visible in the machine-readable evidence and Mission view.

This increment is a protocol/runtime proof only. It does not require a physical robot or a
general perception system. M1.7a is a prerequisite correction, not a substitute for this gate.

## M1.8 — External effect and long-delay evidence

Status: **planned**

M1.8 adds the smallest deterministic proof that a recorded execution event is not itself proof
of an external effect. A fake button device keeps an effect counter or state in storage separate
from the Robot execution journal. The harness can stop the Robot after the device effect and
before the journal/result write, then deliver independent evidence that is present, delayed,
absent or ambiguous.

The same fixture includes virtual-time propagation and queueing delays. A run claiming a
fifteen-minute delay must configure and record at least 900 seconds of one-way transit; a
900-second blackout alone is not such evidence. The run also varies direction/asymmetry and
validity so that admission, execution and expiry are evaluated at the receiving site rather than
inferred from a link profile name.

M1.8 closes when deterministic evidence shows that:

- an independently observed effect is recognized without replaying it;
- absent or ambiguous external evidence yields `OUTCOME_UNKNOWN` (or the documented equivalent)
  and no blind duplicate effect;
- a long-delay run records virtual transit, local queue age, validity and the resulting decision;
- restart and retransmission do not reset the effect identity or autonomy budget.

No hardware-control path is introduced by this increment.

## M2 — Mathematical SO-101 twin in Unreal

Status: **implementation in progress; M2.1 structural description complete**
Target release: **`v0.2.0`**

- textual robot description;
- forward kinematics, Jacobian and constrained damped-least-squares IK in C++;
- separate rigid link meshes, without a skeletal mesh;
- Blueprint-accessible target authoring and debugging;
- confirmed, arrival and target representations with causal provenance;
- trajectory lines and temporal markers;
- a `KinematicPreview` that remains a local candidate, not an execution command.

M2 is a mathematical and visualization milestone. `v0.2.0` requires no physical robot, hardware
calibration or hardware-control path. M2 must preserve the distinction between an operator goal,
a local kinematic preview, a Field admission and a Robot result. See the [M2 design](docs/design/M2_SO101_MATHEMATICAL_TWIN.md)
and the [delayed-intent validation design](docs/design/DELAYED_INTENT_VALIDATION.md).

## M3 — Autonomous delayed button press with bounded re-anchoring

Status: **planned; complete only after M3a and M3b**

M3 is the next behavioral boundary. It brings a deliberately bounded slice of the later M4/M5
ideas into the first button experiment so that delay changes the knowledge available to the local
decision. It does not attempt robot-agnostic generalization or open-ended autonomy.

### M3a — Deterministic simulation gate

M3a uses a known, instrumented two-button simulation fixture and an independently recorded fake
external effect. Its bounded rules are:

- target identity is explicit; a same-identity displacement within a declared tolerance may be
  re-anchored and executed;
- substitution of a visually similar button is forbidden; unresolved identity or ambiguity must
  trigger observation, hold or an assistance request;
- an already acquired effect is recognized according to the task's effect semantics;
- late cancellation is represented as a request for a future decision, with explicit outcomes for
  pre-effect cancellation, safe interruption, effect already produced and unknown result;
- local execution consumes a durable attempt/time/action budget and can continue safely without
  Mission connectivity;
- Robot-estimated and independent effect evidence are reported separately;
- the same low-level controller, budget and delay schedule are used for a fixed-skill baseline,
  delayed 2D authoring and delayed VR authoring; the 2D and VR variants share the same bounded
  local decision policy.

The deterministic scenario/oracle matrix in the [validation design](docs/design/DELAYED_INTENT_VALIDATION.md)
is the M3a gate. S0–S10 must pass with zero unauthorized target substitutions and zero duplicate
effects in the simulation fixture. M3a provides simulation evidence only and makes no physical
hardware claim.

### M3b — Calibrated physical-fixture gate

M3b is a separate planned gate for the first bounded physical claim. It requires:

- a calibrated SO-101 and a measured mirror of its articulated state in Unreal;
- a real two-button fixture with each button instrumented and an independent effect register;
- the local `PressButton` skill, an independent local stop mechanism and documented conservative
  test conditions validated before delayed trials;
- the M3a oracles transposed to the physical fixture, with Robot evidence and independent fixture
  evidence recorded separately for every result.

M3 is complete only when M3a and M3b both pass. M3b remains planned and does not promote the
simulation evidence or the `v0.1.0` dummy release into a hardware claim.

## M4 — Robot-agnostic intent and Field assignment

Status: **planned after complete M3a + M3b evidence**

- VR-designated target rebound against the Field operational estimate beyond the two-button rule;
- typed procedure templates and capability registry;
- robot-independent `OperationIntent`;
- Field grounding, assignment and deterministic validation;
- optional non-authoritative LLM proposal adapter, excluded from safety and acceptance oracles.

The broad identity, capability and substitution problem belongs here after the bounded M3 rule has
shown which decisions are useful. M4 does not retroactively change the `v0.1.0` contract.

## M5 — Adapt, acquire context, or hold

Status: **planned after M3/M4 evidence**

- bounded autonomy envelopes and plan revisions;
- evidence ladder for context acquisition;
- targeted world deltas and incident bundles;
- tiered Robot/Field/Mission telemetry;
- assistance requests containing selected evidence and attempted recovery.

M5 generalizes the M3 hold/observe decision to broader tasks. Its future context-acquisition and
replanning policies must retain explicit authorization, effect identity and causal lineage.

## Version and evidence boundaries

`v0.1.0` remains the historical M1 release. The M1.7a, M1.7, M1.8, M3a and M3b design increments do not
retroactively add capabilities or evidence to that tag. `v0.2.0` remains the M2 target and is
closed by mathematical, cross-language and visualization evidence; it does not require a physical
SO-101 or claim hardware control.

Every status claim in this roadmap is either marked complete with the evidence already recorded by
the project or marked planned/in progress. A design document, fixture or proposed oracle is not
reported as an implementation result.

## Later research tracks

- reusable Unreal Simulation Worker and sim-to-real identification;
- LeRobotDataset export and learned residual policies;
- prediction calibration and uncertainty UX;
- multi-robot coordination groups and resource leases;
- richer world reconstruction;
- Microduck locomotion as a second morphology.
