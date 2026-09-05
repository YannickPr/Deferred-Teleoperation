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

Status: **in progress; M1.8b proof and bounded M1.8c budget implemented**

The [long-delay domain tests](docs/m1/LONG_DELAY_DOMAIN.md) cover 0, 30, 900 and 1200 seconds
of one-way transit, blackout, expiry and persisted-service restart with the M1 dummy effect. The
[M1.8b combined proof](docs/m1/EXTERNAL_EFFECT_LONG_DELAY.md) now runs the independent device
through the delayed Mission/Field domain, including symmetric 1200-second and asymmetric
900-second outbound / 1200-second return transit.

The [external-effect recovery proof](docs/m1/EXTERNAL_EFFECT_RECOVERY.md) defines the
non-idempotent simulated device and its own persistent record. Robot binds the device at
dispatch, observes after uncertain dispatch instead of repeating the action, and holds when the
outcome is unknown or not applied. Missing or substituted adapters are rejected during recovery.
The M1.8b proof combines that adapter with the delayed domain and verifies independent pulse
history, duplicate contract delivery after Robot-store recovery, receiving-site expiry, and the
absence of a fabricated completion snapshot. Stable effect identity across plan revisions and a
full cross-revision effect identity remain open. The [M1.8c durable budget](docs/m1/DURABLE_EXTERNAL_ACTION_BUDGET.md)
adds one local attempt/action reservation for each revision-1 operation, a configurable finite
service-clock window (60 seconds by default), atomic reservation/dispatch/device binding, and
durable pre-dispatch holds with v3-to-v4 legacy classification.

M1.8c assumes one active Robot instance per SQLite database and external adapter. SQLite serializes
the durable reservation, but it does not fence external I/O after that commit; two active workers
can still produce an observe-versus-press race. Fencing or an exclusive process lock and a
multiprocess oracle remain future work ([issue #45](https://github.com/YannickPr/Deferred-Teleoperation/issues/45)).

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

The M1.8b bounded combined slice is implemented and covered by six focused tests. M1.8c adds
eleven persistent budget cases; the current Python suite passes 175 tests. These slices do not
validate physical hardware, a real network, or a whole-OS restart.
Positive and ambiguous observation recovery are covered under long delay; absent external
evidence remains covered by the separate M1.8a recovery proof. It covers contract revision 1;
cross-revision semantic identity remains open. Expiry or cancellation after external dispatch and
before Robot recovery is outside this proof.

M1.8 closes when deterministic evidence shows that:

- an independently observed effect is recognized without replaying it;
- absent or ambiguous external evidence yields `OUTCOME_UNKNOWN` (or the documented equivalent)
  and no blind duplicate effect;
- a long-delay run records virtual transit, local queue age, validity and the resulting decision;
- restart and retransmission do not reset the effect identity or autonomy budget.

The bounded budget portion satisfies the one-reservation invariant for revision 1. Full M1.8
remains open for stable effect identity across plan revisions, multiprocess fencing, and the
machine-readable evidence that joins those decisions to the independent effect record.

No hardware-control path is introduced by this increment.

## M2 — Mathematical SO-101 twin in Unreal

Status: **M2.2 protocol, M2.3 math core, M2.4 oracle and M2.5 kinematic actor complete; M2.7 constrained IK and M2.8a preview math core complete with Linux/Win64 evidence; bounded M2.9a articulated-scene tranche complete with Linux/Win64 native evidence and a synthetic desktop capture; full M2.9, desktop/VR authoring and #20/#21 integration remain open**
Target release: **`v0.2.0`**

- M2.2 articulated robot-state and model-reference protocol (#14), complete with strict Python and
  Unreal DTO/parser coverage, Field relay, the opt-in Mission view, and the explicit
  description-backed validator boundary;
- M2.3 canonical transforms and generic fixed/revolute forward kinematics in C++ (#15),
  validated on Linux and Win64 with Unreal Engine 5.8.2;
- M2.4 cross-language numerical oracle (#16), complete with nine SO-101 cases, six Python
  reference tests and three Unreal Automation tests; its final version-2 validation passes the
  eight-test `DeferredTeleop.M2.Kinematics` selector on Linux and Win64;
- M2.5 generic rigid-link kinematic actor and debug primitives without a skeletal mesh (#17),
  with an explicit Blueprint boundary and independent Confirmed/Arrival/Target layers;
- M2.7 bounded constrained damped-least-squares IK (#19), complete with named generic joint
  groups and tool frames, PositionOnly and PositionPlusApproachAxis tasks, central finite-
  difference Jacobians, structural-limit projection and inspectable result diagnostics, with
  Linux/Win64 Unreal Engine 5.8.2 evidence;
- M2.8a bounded local time-sampled `KinematicPreview` math core (related to #20), with pure
  Blueprint/C++ `BuildPreview`, explicit provenance values, partial-result opt-in, exact inactive
  joint handling, per-joint preview timing limits, FK recomputation for every tool sample, exact
  endpoints, and bounds of 128 samples and 30 seconds, with Linux/Win64 evidence;
- M2.9a bounded opt-in articulated-scene tranche with persistent Confirmed, Arrival and Target
  kinematic actors, explicit local description binding and SHA-256 authentication, per-connection
  wire mode and source ordering, transactional last-good rollback, and seven grouped production
  Automation tests; Linux and Win64 each record build/editor exit code 0 with 50 tests (48
  `Success` and 2 expected `SuccessWithWarnings` for missing-model and duplicate-sequence negative
  cases), plus a synthetic desktop capture;
- planned desktop/VR target authoring and debugging;
- planned confirmed, arrival and target representations with causal provenance;
- planned trajectory lines and temporal markers;
- a `KinematicPreview` consumer remains a local candidate, not an execution command;

The M2.2 platform snapshot records the three targeted ArticulatedView tests as `Success` on
LinuxEditor and WindowsEditor within a 22-test contextual report; build and headless-editor exit code 0.
The compact [M2.2 platform record](docs/m2/evidence/articulated-state-platform-validation.json)
names each test and state and binds the 19 source/fixture hashes to both platform overlays. M2.3's
math core remains covered by its 11-test Linux/Win64 baseline. M2.4 is complete for the numerical
cross-language oracle: its version-2 reference evaluator uses explicit left-to-right reductions so
Python 3.11 and 3.12 produce identical bytes, and its final native validation passes the eight-test
`DeferredTeleop.M2.Kinematics` selector on both targets. The [M2.4 fixture contract](docs/m2/KINEMATICS_FIXTURES.md)
defines nine SO-101 cases, six independent Python reference tests, and three Unreal Automation
tests; the integrated oracle snapshot reports 121 Python tests. The post-rebase integrated Python
validation passes 175 tests; the M2.2 record retains its historical 135/20 context. Its [platform summary](docs/m2/evidence/fk-oracle-platform-validation.json)
retains the earlier full 14-test run as context. The raw articulated feed preserves a model
reference but does not validate geometry; an FK consumer must call the explicit description-backed
validator. The recorded M2.5 Linux and Win64 runs pass their full Automation reports with build and
headless-editor exit code 0: 19 successful tests on Linux and 22 on Win64. Each report contains
the five `DeferredTeleop.M2.KinematicRobotActor.*` tests; the remaining tests are contextual M1/M2
coverage from the same platform run. The [actor guide](docs/m2/KINEMATIC_ROBOT_ACTOR.md) and
[platform evidence](docs/m2/evidence/kinematic-actor-platform-validation.json) record the exact
subset and hashes. The public PNG is a synthetic visual demonstration, not FK proof, measured
telemetry, an operational UI, or VR evidence. M2 remains a mathematical and visualization
milestone. `v0.2.0` requires no physical robot, hardware calibration or hardware-control path. M2
must preserve the distinction between an
operator goal, a local kinematic preview, a Field admission and a Robot result. See the [M2 design](docs/design/M2_SO101_MATHEMATICAL_TWIN.md)
and the [delayed-intent validation design](docs/design/DELAYED_INTENT_VALIDATION.md).

The bounded M2.7 implementation is complete with a 13-test `DeferredTeleop.M2.IK` selector. Linux
and Win64 each record 35 contextual successes (13 IK plus 22 contextual tests), no warnings,
failures or not-run tests in process, and build/editor exit code 0. See the [constrained IK guide](docs/m2/CONSTRAINED_IK.md)
and the [M2.7 platform record](docs/m2/evidence/constrained-ik-platform-validation.json) for
the platform details and source bindings.

The bounded M2.8a preview core is covered by eight production tests under
`DeferredTeleop.M2.KinematicPreview`. Linux and Win64 Unreal Engine 5.8.2 validation each record
43/43 contextual successes (35 existing M2 tests plus the eight preview tests), with build and
headless-editor exit code 0 and no warnings, failures, or not-run tests in process. The [preview guide](docs/m2/KINEMATIC_PREVIEW.md) documents the
timed joint-space math, FK-per-sample tool poses, 128-sample/30-second bounds, preview velocity
limits rather than dynamics, exact inactive-joint rejection, partial opt-in, and provenance
boundary. The [platform record](docs/m2/evidence/kinematic-preview-platform-validation.json)
binds the platform evidence; this math slice does not claim desktop/VR authoring, trajectory
visualization, or closure of #20/#21.

The bounded M2.9a tranche adds the opt-in Unreal presentation consumer described in the
[articulated-scene guide](docs/m2/ARTICULATED_SCENE.md). Its committed editor recipe and seven
grouped production tests cover exact description bytes, strict model and evidence validation,
three persistent semantic layers, stale/last-good transaction behavior, and connection ordering.
The identity correction makes protocol and robot-description literals exact in the existing
articulated-view and robot-description JSON C++ parsers while preserving the standalone client's
`LegacyView` default and M1 behavior. JSON field-name exactness remains a separate parser
conformance concern tracked by [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).
The [platform record](docs/m2/evidence/articulated-scene-platform-validation.json) binds 63
selected files and records build, editor, and automation exit code 0 on both Linux and Win64.
Each platform reports 50 tests: 48 `Success`, 2 expected `SuccessWithWarnings` for the
missing-model and duplicate-sequence negative cases, and zero failures. The final 1920x1080
`RenderOffscreenVulkan` image is a `SYNTHETIC FIXTURE REPLAY` illustrating the three layers from
runtime status labels; it is not a pose/root oracle or a pixel-identical output of the public
generator alone. It is documented in the guide and committed at [the capture](docs/m2/evidence/m2-9a-articulated-scene.png). JSON field-name
exactness remains tracked by [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).
The tranche does not close full M2.9, #20 or #21.

## M3 — Autonomous delayed button press with bounded re-anchoring

Status: **in progress; bounded M3a.1 implemented; complete only after full M3a and M3b**

M3 is the next behavioral boundary. It brings a deliberately bounded slice of the later M4/M5
ideas into the first button experiment so that delay changes the knowledge available to the local
decision. It does not attempt robot-agnostic generalization or open-ended autonomy.

### M3a.1 — Delayed two-button service slice

Status: **implemented for the bounded simulation scope**

The [guide](docs/m3/M3A_TWO_BUTTON.md) and [service proof](docs/m3/evidence/two-button-service-proof.json)
cover one immutable revision-1 `EnsureButtonLatched` intent through Mission, 1200 seconds of
virtual transit, Field-local observation after the delay, Robot admission/reservation, an
independent device journal, and the final Mission snapshot. The service classes run in one
process with separate stores; failures are injected and stores/devices are reopened.

S0 establishes actual A/B contacts. S1 permits same-identity re-anchoring at the declared bound
and holds beyond it; S2 holds on ambiguous identity while the fixed-reference ablation contacts
the other button. S4 recognizes an already-latched target without a new impulse or budget
admission. Recovery preserves one reservation and one impulse, and replay preserves the accepted
proof. Missing or inconsistent command evidence resolves to explicit UNKNOWN rather than an
attributed contact. This slice does not close the full S0–S10 matrix or physical M3b.

Next, extend the same independent oracles to the remaining matrix rows before broadening the
policy. Cross-revision effect identity and causal lineage, multiprocess fencing (#45), and the
separate M2 parser/authoring/integration issues (#47, #20 and #21) remain open. Future 2D/VR
comparisons retain the same controller and local autonomy.

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
retroactively add capabilities or evidence to that tag. `v0.2.0` remains the M2 target; the M2.2,
M2.3, M2.4 and bounded M2.5 slices are evidenced, M2.7 and M2.8a are complete with Linux/Win64
evidence, and the bounded M2.9a articulated-scene tranche is complete with its Linux/Win64 native
record and synthetic desktop capture. Full M2.9 and desktop/VR authoring with the #20/#21
integration gates remain open. The target does not require a physical SO-101 or claim hardware
control.

Every status claim in this roadmap distinguishes an implemented increment from planned or
in-progress work. A design document, fixture or proposed oracle is not reported as an
implementation result by itself.

## Later research tracks

- reusable Unreal Simulation Worker and sim-to-real identification;
- LeRobotDataset export and learned residual policies;
- prediction calibration and uncertainty UX;
- multi-robot coordination groups and resource leases;
- richer world reconstruction;
- Microduck locomotion as a second morphology.
