# Delayed intent validation

Status: **M1.7a implemented; remaining gates planned**
Scope: **M1.7a, M1.7, M1.8, M3a and M3b**

Related documents: [roadmap](../../ROADMAP.md), [project status](../STATUS.md), [canonical
terminology](../concepts/TERMINOLOGY.md), [time and provenance](../concepts/TIME_FRAMES_AND_PROVENANCE.md),
and the [M1 release gate](../m1/RELEASE_GATE.md).

## 1. Purpose and boundary

M1 proves a delayed, persistent dummy path. This design specifies the next proof: an operation
must preserve the operator's intended target when the Field estimate is old, incomplete or
contradictory. The local decision may execute, re-anchor within an explicit allowance, acquire
one more observation, hold, recognize an effect already present, or report an unknown result.

M1.7a is a bounded correction to Mission's current selection behavior. M1.7 then tests full
causal coherence when two operations are in flight. M1.8 tests an external effect and long
virtual delays. M3a uses the bounded rules on a known two-button simulation fixture; M3b later
transposes the same oracles to a calibrated physical fixture. M1.7a is implemented in
[PR #30](https://github.com/YannickPr/Deferred-Teleoperation/pull/30); the remaining gates are
planned. No physical result is claimed until M3b passes.

The design keeps the existing authority boundary:

```text
Mission: author and reconcile delayed intent and views
Field:   ground, admit and coordinate against its local estimate
Robot:   decide and execute locally within an admitted contract; report evidence
```

The SO-101 mathematical twin can provide a local target and `KinematicPreview`, but that preview
is not an admission, an actuator command or proof of an external effect. M2 remains the
mathematical/visualization target `v0.2.0` and does not require hardware.

## M1.7a — Bounded Mission-view selection correction

M1.7a is implemented and documented in the [Mission selection contract](../m1/MISSION_OPERATION_SELECTION.md).
Its 72-test Python suite and CI passed with the historical golden session unchanged. It is
deliberately smaller than the full lineage work that follows:

- Mission chooses the latest intent globally with `MAX(created_at, str(message_id))`, then uses
  that intent's `correlation_id` for view filtering;
- view construction validates the mapping among Mission outbox intents: one `operation_id` must
  map to one `correlation_id` and one `correlation_id` to one `operation_id`; this is not a global
  protocol invariant enforced by Field or Robot;
- duplicate deliveries with the same operation and correlation are allowed when the mapping is
  unambiguous;
- distinct operation IDs sharing a correlation, or one operation using several correlations, raise
  `MissionViewSelectionError`; Mission never chooses one conflicting operation;
- the selected operation and correlation filter snapshot, arrival forecast and terminal event; a
  layer from another operation is never a fallback;
- a `frame_id`, calibration-reference or `robot_id` mismatch in snapshot, forecast or target
  projection makes the affected layer absent or unknown; terminal events have no frame and are not
  subject to a frame mismatch check;
- the existing `dtt/0` golden fixture and Unreal Mission-view behavior remain regression checks;
- no complete lineage schema or multi-operation interpretation is claimed by this correction.

M1.7a therefore establishes a stable bounded invariant; it does not close the full M1.7 gate.

## 2. Hypothesis and controlled comparison

At equal local autonomy, an intent that carries target identity, explicit adaptation limits and
causal evidence should produce more conforming outcomes under stale-world changes, with no
unauthorized substitutions or duplicate effects.

The first comparison is the M3a simulation gate. It uses the same fixture, virtual-time schedule,
Field decision table, Robot controller, retry budget and evidence recorder for every condition:

| Variant | Difference under test |
|---|---|
| Fixed-skill baseline | A delayed fixed target is sent to the same local skill; no intent re-anchoring |
| Delayed 2D authoring | The bounded intent is authored and reviewed in the desktop interface |
| Delayed VR authoring | The same bounded intent is authored and reviewed in VR |
| Optional prediction ablation | The same 2D or VR intent is shown without the arrival projection |

The fixed-skill row is an explicit ablation of intent re-anchoring; it keeps the low-level Robot
controller, timing and budget unchanged. The 2D and VR rows use the same bounded local decision
policy and differ only in authoring/presentation. This keeps an interface comparison from adding
autonomy by accident.

The optional prediction ablation is descriptive and does not change the M3a correctness oracle.
No variant receives extra autonomy because it uses VR, a preview or a prediction. Report at least
conforming outcome rate, unauthorized adaptation, duplicate effect count, necessary and
unnecessary holds, assistance requests, operator active time, end-to-end reconciliation delay and
bytes per useful result.

## 3. Minimal logical contract

The following is a test vocabulary and semantic contract, not a frozen wire schema. Existing
`OperationIntent`, `GroundedOperation`, `ExecutionContract` and `ExecutionEvent` types may carry
these fields through their normal versioning rules.

### 3.1 Delayed intent

```text
DelayedIntent
- operation_id
- intent_revision
- target
- reference_observation
- requested_effect
- allowed_adaptation
- validity
- cancellation_policy
```

The fields have these minimum meanings:

```text
target
- target_identity          # stable fixture identity, for example button-A
- target_role              # optional descriptive role; never a substitute identity

reference_observation
- observation_id
- world_revision
- observed_at
- frame_id

requested_effect
- effect_id                # stable across retries and plan revisions
- kind                     # PRESS_ONCE for this experiment

allowed_adaptation
- same_identity_only = true
- max_displacement_m
- substitution = FORBIDDEN

validity
- not_before
- expires_at
- max_local_duration

cancellation_policy
- cancellation_is_a_request = true
- safe_interruption = required
```

`effect_id` identifies the requested external effect; a delivery or execution attempt has a
separate attempt identity. Revising a plan or receiving a duplicate envelope must not create a
new effect identity. The fixture's distance tolerance is declared before the run and is applied
to the same target identity only.

### 3.2 Observation and decision

The receiving side records the observation it actually had, rather than copying the hidden
fixture truth into a decision:

```text
Observation
- observation_id
- world_revision
- observed_at
- entities[]                # identity, role, pose, visibility and source evidence
- source_ids
- model_reference
- calibration_reference    # explicit unavailable value is allowed in the simulated phase
```

Each decision is explainable and tied to one operation revision:

```text
LocalDecision
- decision_id
- operation_id
- intent_revision
- basis_observation_ids[]
- target_identity          # absent when identity is unresolved
- action
- reason_code
- budget_before
- budget_after
```

Allowed actions for this experiment are `EXECUTE`, `REANCHOR_EXECUTE`,
`ACQUIRE_OBSERVATION`, `HOLD`, `RECOGNIZE_EFFECT` and `CANCEL`. A hold can request assistance;
it never silently becomes an execution.

### 3.3 Independent effect evidence and outcome

The fake button device has state or a monotonic effect counter stored independently of the Robot
execution journal:

```text
EffectEvidence
- effect_id
- device_id
- evidence_observation_id
- device_counter_or_state
- status                    # PRESENT, ABSENT, AMBIGUOUS or UNAVAILABLE
- observed_at
- provenance
```

The minimum outcome vocabulary distinguishes a physical result from a decision to stop looking:

```text
SUCCEEDED
RECOGNIZED_ALREADY_EFFECTIVE
CANCELLED_BEFORE_EFFECT
INTERRUPTED_SAFE
OUTCOME_UNKNOWN
REJECTED_IDENTITY
REJECTED_UNAUTHORIZED_DISPLACEMENT
REJECTED_EXPIRED
HELD_AMBIGUOUS
```

Robot-estimated completion, independent `EffectEvidence` and the final `Outcome` remain separate
records. A missing terminal message or a released button does not prove that no press occurred.

## 4. Causal bundle and authority rules

Every Mission view branch and every decision evidence bundle is keyed by the following lineage:

```text
CausalBundle
- operation_id
- intent_revision
- source_observation_id
- source_world_revision
- grounding_reference
- forecast_reference       # optional; required only for an arrival projection
- model_reference
- calibration_reference
- decision_reference
- contract_revision
- effect_id
- effect_evidence_refs[]
- outcome_reference
```

The rules are:

1. A target, arrival forecast and terminal event may be assembled only when their operation,
   intent revision and parent references are compatible.
2. A late, duplicate or reordered event is idempotent by its semantic identity and cannot attach
   to a different operation because it arrived last.
3. A missing or incompatible parent remains missing or incompatible. Mission exposes `UNKNOWN`
   or `INCOMPATIBLE_CONTEXT` instead of borrowing a value from another operation.
4. A new world, model or calibration revision invalidates only descendants that cite it. It does
   not silently rewrite an unrelated operation.
5. Field's admission authorizes a bounded contract; it is not independent proof that the physical
   effect happened. Robot may refuse or hold when local evidence violates the contract.
6. A cancellation delivered after admission is a request for a future local decision. It cannot
   erase an effect already produced or reset a consumed budget.

The hidden fixture truth and device counter are recorded independently of Mission, Field and
Robot. The decision process receives only the observations scheduled for that condition, so an
oracle cannot pass by reading a test-only world state.

## 5. Deterministic local decision policy

The first implementation uses a small explicit table. It does not require an LLM, a learned
policy, dense reconstruction or a generic robot adapter.

| Local condition | Required action/outcome | Forbidden behavior |
|---|---|---|
| Same identity, within the recorded reference tolerance | `EXECUTE`; use the observed pose | Selecting another entity because it is closer |
| Same identity displaced within `max_displacement_m` | `REANCHOR_EXECUTE` and record the displacement | Treating the displacement as permission to change identity |
| Same identity displaced beyond the allowance | `HOLD` or `REJECTED_UNAUTHORIZED_DISPLACEMENT` | Executing on an unapproved pose |
| Two compatible-looking buttons or unresolved identity | `ACQUIRE_OBSERVATION`, then `HELD_AMBIGUOUS` if unresolved | Guessing from proximity, appearance or arrival order |
| Target absent, occluded or observation too old to support the rule | `ACQUIRE_OBSERVATION` or `HOLD` | Inventing a measured pose from a forecast |
| Independent evidence says `effect_id` is already present | `RECOGNIZE_EFFECT` / `RECOGNIZED_ALREADY_EFFECTIVE` | Pressing again |
| Contact or attempt occurred but independent evidence is absent/ambiguous | `OUTCOME_UNKNOWN` and no blind retry | Reporting success from the Robot journal alone |
| Cancellation received before effect | `CANCEL` / `CANCELLED_BEFORE_EFFECT` | Starting the cancelled effect |
| Cancellation received during execution | Stop at the next safe boundary; report `INTERRUPTED_SAFE`, effect already produced, or unknown | Claiming that the request stopped a past effect |
| Cancellation received after effect | Report effect already produced or unknown | Reversing or hiding the effect as compensation |
| Validity expired before admission | `REJECTED_EXPIRED` | Dispatching an expired intent |
| Obstacle, local safety violation or exhausted durable budget | `HOLD` with reason and evidence | Resetting a budget after restart or revision |

`EXECUTE` and `REANCHOR_EXECUTE` are permitted only for the target identity in the intent. For
the two-button fixture, swapping A and B is therefore a negative identity test even when the
buttons have identical geometry and role labels.

## 6. Scenario and oracle matrix

The matrix is deterministic: each row fixes the initial fixture, event schedule, virtual clocks,
message order and expected oracle. A row passes only when the action, target identity, outcome,
effect counter and causal references match the stated result.

| ID | Slice | Injected condition | Expected oracle | Required invariant |
|---|---|---|---|---|
| A1 | M1.7a | Two distinct operation IDs in Mission's outbox share one `correlation_id` | `MissionViewSelectionError`; assemble no Mission view | Reject the ambiguous mapping during view construction |
| A2 | M1.7a | One operation ID in Mission's outbox is presented with more than one `correlation_id` | `MissionViewSelectionError`; assemble no Mission view | Reject the ambiguous mapping during view construction |
| A3 | M1.7a | The selected operation/correlation has a frame, calibration or robot mismatch in snapshot, forecast or target projection; its terminal event is otherwise valid | Mark only the affected projection layer absent/unknown; filter the terminal event by operation/correlation | Terminal events have no frame; no incompatible projection is borrowed |
| A4 | M1.7a | Valid operations use distinct correlations; envelopes are delivered out of order | Select the intent with global `MAX(created_at, str(message_id))`, then filter only its correlation | Selection is independent of arrival order |
| A5 | M1.7a | Duplicate envelopes repeat one unambiguous operation/correlation mapping | Select that operation/correlation without an error | Same operation/correlation duplicates remain idempotent |
| C1 | M1.7 | Operations A and B are admitted; intents, forecasts and terminal events arrive in reverse order | Two independent bundles, each reconciled to its own operation | No target/forecast/result cross-association |
| C2 | M1.7 | Duplicate delivery and reconnect occur after a model or calibration revision | Same decision identity after replay; incompatible descendants remain explicit | No duplicate decision or borrowed latest state |
| S0 | M3a | Button A is unchanged from the reference observation | `EXECUTE` A; `SUCCEEDED` after independent effect evidence | Exactly one `effect_id` and one counter increment |
| S1 | M3a | Button A moves within the declared authorized displacement | `REANCHOR_EXECUTE` A; `SUCCEEDED` | Same identity is retained; displacement is recorded |
| S2 | M3a | Buttons A and B exchange positions while the intent names A; the bounded follow-up observation cannot distinguish them | `HELD_AMBIGUOUS` | Zero unauthorized substitution and zero B effect |
| S3 | M3a | A is hidden or identity evidence remains ambiguous | `HELD_AMBIGUOUS` after the bounded observation attempt | No guessed target and no effect |
| S4 | M3a/M1.8 | Independent device counter shows A's `effect_id` already present before arrival | `RECOGNIZED_ALREADY_EFFECTIVE` | Counter is not incremented a second time |
| S5 | M1.8 | Robot causes the device effect, then stops before journal/result persistence; evidence later says `PRESENT` | `RECOGNIZED_ALREADY_EFFECTIVE` | Recovery does not replay the effect |
| S6 | M1.8 | Same crash window, but evidence is `AMBIGUOUS` or `UNAVAILABLE` | `OUTCOME_UNKNOWN`; hold for controlled evidence | No blind retry and no false success |
| S7 | M3a | Cancellation arrives before contact/effect | `CANCELLED_BEFORE_EFFECT` | No effect for the cancelled `effect_id` |
| S8 | M3a | Cancellation arrives during execution before the effect and a safe boundary is available | `INTERRUPTED_SAFE` | No effect is reported for the cancelled `effect_id` |
| S9 | M3a | Cancellation arrives after independent effect evidence says `PRESENT` | `RECOGNIZED_ALREADY_EFFECTIVE` | Never claim cancellation erased a past effect |
| S10 | M3a | Obstacle or local budget exhaustion appears during execution; Robot restarts | Safe `HOLD` and durable remaining budget | Restart cannot reset attempts, time or action allowance |
| L1 | M1.8 | Virtual one-way transit is at least 900 seconds, with queue age and asymmetry recorded; validity includes admission | `EXECUTE` A and `SUCCEEDED` after independent effect evidence | A blackout label alone cannot count as a long-delay proof |
| L2 | M1.8 | The same recorded transit reaches Field after `expires_at` and before admission | `REJECTED_EXPIRED` | No dispatch and no effect for the expired `effect_id` |

For A1–A5, the oracle checks the bounded selection correction only; it does not establish complete
lineage for multiple operations. For S2, the oracle is fixed before the run: identity A remains
authorized, and proximity or visual similarity cannot authorize B. For S5 and S6, the device
counter is the independent reference; the Robot journal is deliberately insufficient. For S9, the
positive device evidence is delivered before the cancellation is processed, so the expected result
is fixed. For L1, a shorter or unrecorded transit run may
still test transport, but cannot be reported as the fifteen-minute propagation evidence.

## 7. Replay procedure and evidence

The planned harness will:

1. seed operation, intent, message and device identities;
2. set a virtual monotonic timeline and record source/produced/arrival times separately;
3. generate the hidden fixture truth and independent device log before releasing only the
   scheduled observations to the three authorities;
4. inject delay, queueing, duplication, reorder, reconnect, crash and cancellation events;
5. compare each decision and outcome with the matrix oracle;
6. emit machine-readable causal bundles, decision reasons, budget transitions, effect counters and
   a Mission-view artifact suitable for human inspection.

The long-delay configuration must state one-way and return transit, blackout intervals, local
queue age, clock uncertainty and intent validity. A virtual 900-second transit is an evidence
parameter, not a calendar deadline. For M3a, physical hardware, engine startup and actuator motion
are outside the validation run.

### M3b physical transposition

M3b is a separate planned physical-fixture procedure. Before delayed trials, it must record a
calibrated SO-101 and its measured articulated mirror in Unreal, a real two-button fixture with
independent button instrumentation and an independent effect register, and a validated local
`PressButton` skill with an independent local stop and documented conservative test conditions.
The S0–S10 M3a oracles are then transposed without changing target-identity or effect semantics.
Each physical result must include Robot evidence and independent fixture/effect-register evidence;
an event from either source alone is insufficient. This gate creates a narrowly scoped physical
result for the documented fixture and conditions, not a general hardware-safety claim.

## 8. Acceptance gates

There is no date-based gate. The following artifacts and invariants close the increments:

### M1.7a gate

- deterministic replay passes A1–A5;
- the latest intent is selected globally with `MAX(created_at, str(message_id))`, then its
  correlation is used for filtering;
- the mapping among Mission outbox intents is validated during view construction; conflicting
  operation/correlation mappings raise `MissionViewSelectionError` and assemble no Mission view;
- duplicate delivery of one unambiguous operation/correlation mapping remains idempotent;
- snapshot, forecast and terminal layers are filtered to the selected operation/correlation;
- frame, calibration or robot mismatches in snapshot/forecast/target projection produce
  absent/unknown layers without fallback; terminal events have no frame;
- the existing `dtt/0` golden session and Unreal Mission-view checks remain unchanged;
- the result is reported as a bounded correction only, with no complete lineage claim.

### M1.7 full gate

- deterministic two-operation replay passes C1 and C2;
- every view and event has operation and intent-revision lineage;
- no cross-operation association occurs under reversal, duplication or reconnect;
- missing, stale or incompatible context is visible as such in machine-readable and Mission-view
  evidence.

### M1.8 gate

- S4–S6 prove independent effect recognition and the unknown-result path across the journal crash
  window;
- L1 and L2 contain recorded virtual one-way transit of at least 900 seconds and separate
  propagation from blackout and local queue time;
- expiry and admission decisions use the receiving-side evidence and do not silently reset the
  effect identity or autonomy budget;
- no duplicate external effect is produced in replay.

### M3a simulation gate

- S0–S10 pass with the exact target identity, action/outcome and effect-counter invariants;
- the two-button swap and ambiguity cases produce no unauthorized action;
- late cancellation is reported according to observed effect state;
- fixed-skill, delayed 2D and delayed VR variants run with the same low-level controller, budget,
  fixture and delay schedule; the 2D and VR variants also use identical bounded local autonomy,
  with metrics and failures retained for comparison;
- the gate is simulation-only and makes no physical-hardware claim.

### M3b physical-fixture gate

- a calibrated SO-101 and measured articulated mirror are recorded in Unreal;
- the real two-button fixture, independent button instrumentation and independent effect register
  are validated under the declared test conditions;
- the local `PressButton` skill and independent local stop are validated before delayed trials;
- the S0–S10 M3a oracles are transposed with physical Robot evidence and independent fixture
  evidence recorded separately for every result;
- no physical result is accepted from a Robot event or fixture register alone.

### M3 completion rule

M3 is complete only when both M3a and M3b pass. This documentation does not close M1.7 or M3;
M3a and M3b remain planned gates until their respective artifacts and evidence exist.

## 9. Scope limits

The first proof has two instrumented buttons, stable fixture identities, bounded displacement and
deterministic observations. It establishes whether explicit authorization, abstention and causal
reconciliation work in this narrow setting. It does not establish general visual identity,
robot-agnostic task transfer, multi-robot coordination, learned policies or safety certification.

M3a is simulation-only and cannot be reported as physical evidence. M3b may support a physical
claim only for the calibrated SO-101, documented two-button fixture and validated test conditions
that pass its gate; M3 is complete only when both gates pass.

Broader capability registries, context acquisition and plan revision remain M4/M5 work. An
optional LLM adapter may later propose a grounding or explanation, but it is excluded from the
M1.7a/M1.7/M1.8/M3a/M3b decision oracles and cannot grant authorization. The historical `v0.1.0`
M1 release and the hardware-free `v0.2.0` M2 target keep their existing evidence boundaries.
