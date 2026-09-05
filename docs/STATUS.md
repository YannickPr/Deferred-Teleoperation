# Project status

This file separates demonstrated behavior from design work that is planned or still in
progress. The [roadmap](../ROADMAP.md) defines the corresponding evidence gates.

## Status at a glance

| Slice | State | Evidence boundary |
|---|---|---|
| M0 | **Implemented** | Public foundation, protocol vocabulary, CI and Unreal module skeleton |
| M1 | **Implemented; `v0.1.0` historical** | Delay-tolerant dummy, persistence, replay and Mission reconciliation |
| M1.7a | **Implemented; PR #30** | Bounded correlation selection and compatible Mission-view layer filtering |
| M1.7 | **Planned after M1.7a** | Full causal coherence for concurrent operations and reordered Mission-view data |
| M1.8 | **In progress; M1.8b proof and bounded M1.8c budget implemented** | One revision-1 local reservation/window is durable; local ownership is enforced; cross-revision identity remains open |
| M2 | **In progress; target `v0.2.0`** | M2.1 structural model, M2.2 articulated-state protocol, M2.3 FK math core, M2.4 oracle, M2.5 actor, and M2.8a local preview math core are complete with Linux/Win64 UE evidence; M2.7 constrained IK is complete with Linux/Win64 UE evidence; bounded M2.9a articulated-scene tranche is complete with Linux/Win64 native evidence and a synthetic desktop capture; full M2.9, desktop/VR authoring and #20/#21 remain |
| M3 | **In progress; bounded M3a.1 implemented** | [Two-button service proof](m3/evidence/two-button-service-proof.json); full M3a S0–S10 and physical M3b remain open |
| M4/M5 | **Planned** | Broader robot-agnostic assignment, context acquisition and replanning |

M1.7a, M1.7, M1.8 and the enriched M3a/M3b slices are specified in the [delayed-intent
validation design](design/DELAYED_INTENT_VALIDATION.md). M1.7a is implemented in
[PR #30](https://github.com/YannickPr/Deferred-Teleoperation/pull/30), and the bounded M1.8b
combined proof is implemented in [the external long-delay evidence](m1/EXTERNAL_EFFECT_LONG_DELAY.md).
The bounded M1.8c local budget is documented in [the durable budget guide](m1/DURABLE_EXTERNAL_ACTION_BUDGET.md).
The remaining M1.7 and full M1.8 gates are not established by these bounded slices.

## Implemented in M0

- public repository and Apache-2.0 code licence;
- minimal Python package metadata;
- CI for Python style, package import and JSON-schema fixtures;
- experimental `dtt/0` message-envelope schema;
- initial terminology, time/frame/provenance and threat-model documents;
- Unreal Engine host project and one runtime-plugin module skeleton.

## M0 local Unreal verification

Completed on 4 September 2026 on the primary Windows development platform with Unreal Engine
5.8.2 (build 56702186):

- associated the host project with UE 5.8 and accepted the editor's automatic regeneration and
  rebuild;
- compiled and linked `DeferredTeleopRuntime` for Development Editor / Win64;
- opened `DeferredTeleopDemo` and verified plugin discovery and enablement;
- observed `DeferredTeleopRuntime dtt/0 loaded` in the startup log;
- called `Get Deferred Teleop Protocol Version` from Blueprint and observed `dtt/0`;
- confirmed that no robot or hardware-control path was introduced.

The exact commands, environment and visible evidence are recorded on bootstrap pull request #2.

## Implemented in M1

- strict Python wire models and JSON schemas for the constrained `dtt/0` dummy path;
- durable SQLite inbox, outbox, execution journal and crash recovery primitives;
- deterministic in-memory and two-sided WebSocket link emulator;
- seeded delay, jitter, duplication, reordering, blackout, bandwidth and capacity profiles;
- transport-level ACK frames, observability metrics and virtual-time fault tests.
- separate Mission, Field and dummy-Robot processes with independent durable stores;
- constrained Field grounding, one-node planning, assignment and local execution contract;
- deterministic six-phase dummy skill with an effect-once journal boundary;
- confirmed, predicted-arrival and operator-target Mission projections with explicit provenance;
- one-command nominal and fault/reconnect demonstrations plus causal-history inspection.
- strict `dtt/0` Mission-view snapshots streamed on a local WebSocket endpoint;
- reconnecting Blueprint-spawnable Unreal client that preserves the last valid view;
- explicit metres/right-handed to centimetres/left-handed Unreal conversion with tests;
- generated Blueprint example scene separating confirmed, arrival-belief and target states;
- timed trajectory marker, provenance/freshness labels and deterministic PNG evidence capture.
- deterministic golden session replayed through the real M1 domain services;
- fourteen-profile adversarial matrix mapped to executable tests and durable evidence;
- machine-readable `v0.1.0` release checklist and CI-compatible gate;
- changelog and citation metadata prepared for the first tagged release.
- Unreal Engine 5.8.2 validation on Windows, the reference Unreal platform for `v0.1.0`;
- portable Python validation on Linux through GitHub CI.

The [M1 release gate](m1/RELEASE_GATE.md) records the evidence for this historical release.
It covers the constrained dummy path. It does not cover stale-world target decisions, an
independent external effect, or concurrent-operation causal coherence.

## Implemented in M2

- pinned, unmodified SO-101 structural URDF with immutable source identity and licence metadata;
- deterministic canonical right-handed metre/radian robot description;
- explicit arm and gripper joint groups plus the `gripper_frame_link` tool frame;
- offline source-hash, structure and generated-description drift checks on Linux and Windows.
- M2.3 canonical transforms, generic fixed/revolute tree FK and named tool-frame output (#15);
- M2.3 Unreal boundary conversion and schema-scoped parser for the generated description;
- M2.2 strict articulated robot-state/model-reference DTOs and parser, Field relay, opt-in Mission
  view, and an explicit description-backed validator that returns visible diagnostics;
- the M2.2 platform snapshot lists the three targeted ArticulatedView tests as `Success` on
  LinuxEditor and WindowsEditor within a 22-test contextual report; build and headless-editor
  exit code 0. The [compact platform record](m2/evidence/articulated-state-platform-validation.json)
  contains the exact names/states, raw report hashes, 19 source/fixture hashes, and six unchanged
  M1 golden files;
- Linux and Win64 Unreal Engine 5.8.2 M2.3 baseline evidence: 11 successful tests on each target
  (4 M1 and 7 M2), with build and headless-editor exit code 0;
- M2.4 cross-language SO-101 FK oracle (#16): nine fixture cases, six independent Python
  reference tests and three Unreal Automation tests added to the 11-test baseline;
- generator version 2's explicit left-to-right reductions, with byte-identical Python 3.11/3.12
  output and final native validation of the eight-test `DeferredTeleop.M2.Kinematics` selector on
  Linux and Win64;
- M2.5 generic rigid-link kinematic actor with explicit Confirmed/Arrival/Target layers,
  all-or-nothing invalid-input preservation, reusable flat link topology, and debug primitives
  without robot mesh assets (#17);
- M2.5 Linux and Win64 Unreal Engine 5.8.2 validation: full reports with 19 successful tests on
  Linux and 22 on Win64, including the same five actor tests on each target, with final build and
  headless-editor exit code 0.
- M2.7 constrained IK implementation (#19): named generic joint groups and tool
  frames, PositionOnly and PositionPlusApproachAxis tasks, deterministic damped least squares,
  central finite-difference Jacobian, structural-limit projection and full result diagnostics;
  the acceptance selector contains 13 automation tests. Linux and Win64 each record 35 contextual
  successes (13 IK plus 22 contextual tests), no warnings, failures or not-run tests in process,
  and build/editor exit code 0. See the [M2.7 platform record](m2/evidence/constrained-ik-platform-validation.json).
- M2.8a bounded local time-sampled `KinematicPreview` math core: pure C++/Blueprint
  `BuildPreview`, explicit provenance values, partial-result opt-in, exact inactive-joint
  rejection, preview velocity limits in radians per second, FK recomputation for every tool
  sample, exact endpoints, and bounds of 128 samples and 30 seconds. Its eight-test selector
  passes within the 43-test contextual Linux and Win64 Unreal Engine 5.8.2 reports, with build
  and editor exit code 0 and no warnings, failures, or not-run tests. See the [preview guide](m2/KINEMATIC_PREVIEW.md)
  and the [platform record](m2/evidence/kinematic-preview-platform-validation.json).

The [M2.4 fixture contract](m2/KINEMATICS_FIXTURES.md) documents the numerical oracle and the
mandatory Python `--check` gate. The [M2.4 platform summary](m2/evidence/fk-oracle-platform-validation.json)
records the machine-readable eight-test native results and retains the earlier full 14-test run as
context. The generated SO-101 structural check and the M2.4 fixture contract have distinct roles:
the latter supplies the numerical FK oracle. Wire parsing and the live Mission view preserve the
articulated model reference without validating SO-101 geometry; an FK consumer must call the
explicit description-backed validator. The post-rebase integrated Python validation passes 185
tests; the M2.4 oracle record retains its 121-test snapshot and the M2.2 record retains its
historical 135/20 context. Remaining M2 work is described in the [M2 design](design/M2_SO101_MATHEMATICAL_TWIN.md).

The public [M2.5 actor guide](m2/KINEMATIC_ROBOT_ACTOR.md) describes the manual same-process scene
recipe, and the [platform evidence](m2/evidence/kinematic-actor-platform-validation.json) records
the five-test subset, report hashes, source hashes, and synthetic PNG provenance. The PNG is a
visual demonstration only: it does not establish FK correctness, measured telemetry, an operational
UI, or VR authoring. M2 remains a mathematical and visualization milestone and does not require a
physical robot.

### M2.7 constrained IK

The bounded #19 runtime and test slice is complete, including the private test-only Jacobian
oracle, generic-group cases, SO-101 position and approach-axis cases, honest local-failure
handling and warm-start/free-roll regressions. Its 13-test selector records 35 contextual
successes on both Linux and Win64 (13 IK plus 22 contextual tests), with no warnings, failures or
not-run tests in process and build/editor exit code 0. The slice is a local kinematic preview aid
and does not add hardware, collision or motion-control behavior. The [platform record](m2/evidence/constrained-ik-platform-validation.json)
binds the report hashes and source details.

### M2.8a local kinematic preview

The bounded #20 support slice converts one explicit articulated start state and a validated
converged or opt-in partial IK result into deterministic joint-space samples. It computes
`T = max(abs(goal-start) / preview_velocity)` in radians, rejects non-finite or over-30-second
durations, returns one sample for zero duration, and caps non-zero previews at 128 samples.
Endpoints use the copied start and goal values exactly. Every sample recomputes canonical FK for
the requested IK tool frame; tool poses are never interpolated. Preview velocity limits only bound
presentation timing and do not model dynamics or authorize motion.

The goal uses active IK joints. Every inactive revolute IK value must equal its start value exactly;
an inactive gripper mismatch is rejected before goal FK and sampling. `Partial` and `IterationLimit`
results require explicit opt-in and `bSuccess=false`. Source and evidence identifiers, provenance,
frame, calibration and declared model hash shape are carried as values; the hash is not authenticated.
The builder resets failed output and leaves retention of the last valid preview to its caller.

The selector contains eight production `DeferredTeleop.M2.KinematicPreview` tests. Linux and Win64
UE 5.8.2 each report 43/43 contextual successes (35 existing M2 tests plus the eight preview tests),
zero warnings/failures/not-run tests in process, and build/editor exit code 0. The
[machine-readable record](m2/evidence/kinematic-preview-platform-validation.json) binds the
platform reports and selected source hashes. This is the M2.8a math core only; desktop/VR authoring,
trajectory visualization, and the full M2 integration gates in #20/#21 remain open.

### M2.9a opt-in articulated scene

The bounded M2.9a tranche adds an opt-in Unreal presentation consumer for the separate
`mission.articulated_view_state` wire mode. It keeps three persistent Confirmed, Arrival and Target
kinematic actors, binds one explicit local description using the exact raw bytes and private
OpenSSL SHA-256, validates layer provenance and canonical FK inputs before mutation, and restores
last-good roots/joints/evidence when candidate application fails. The effective wire mode and
per-source sequence ordering are fixed per connection; stale callbacks from an old generation are
ignored. The editor recipe labels its output `FIXTURE REPLAY / SYNTHETIC DEMONSTRATION` and does not
claim live telemetry.

The source contains exactly seven grouped production Automation tests covering byte hashing,
identity/order validation, catalogue reload rollback, root/apply rollback, layer presence and
outliers, connection ordering, and temporal evidence preservation. The
[platform record](m2/evidence/articulated-scene-platform-validation.json) binds 63 selected files
and records build, editor, and automation exit code `0` on Linux and Win64. Each platform reports
50 tests: 48 `Success`, 2 expected `SuccessWithWarnings` for the missing-model and
duplicate-sequence negative cases, and zero failures. The identity correction makes protocol and
robot-description literals exact in the two existing C++ parsers while preserving the standalone
client's `LegacyView` default and M1 behavior. JSON field-name exactness remains a separate
parser-conformance concern tracked by [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).

The final desktop capture is a 1920x1080 `RenderOffscreenVulkan` render labelled
`SYNTHETIC FIXTURE REPLAY`; it is an illustration of the three layers using labels from the
runtime statuses, not a pose/root oracle or a pixel-identical output of the public generator
alone. It adds no VR or hardware path. Its generator, presentation-wrapper and image hashes,
plus the capture metadata, are recorded in the platform record and the [articulated-scene guide](m2/ARTICULATED_SCENE.md)
without exposing machine-local paths. This bounded tranche does not close full M2.9,
desktop/VR authoring, or the full #20/#21 integration gates.

## Implemented in M1.7a

The [selection contract](m1/MISSION_OPERATION_SELECTION.md) documents this bounded correction.
All 72 Python tests, Ruff and the unchanged M1 CI release gate passed in
[PR #30](https://github.com/YannickPr/Deferred-Teleoperation/pull/30).

- validation of the operation/correlation mapping among Mission outbox intents, with latest-intent
  selection by `MAX(created_at, str(message_id))`;
- distinct operation IDs sharing a correlation, or one operation using several correlations, raise
  `MissionViewSelectionError`; duplicate delivery of one operation/correlation remains allowed;
- snapshot, forecast and terminal-event layers filtered to the selected operation/correlation;
- frame, calibration or robot mismatches in snapshot/forecast/target projection represented as
  absent/unknown layers; terminal events have no frame;
- existing `dtt/0` golden and Unreal Mission-view evidence preserved;
- no claim of complete lineage or multi-operation coherence.

## Implemented in M3a.1

The [two-button guide](m3/M3A_TWO_BUTTON.md) and [machine-readable proof](m3/evidence/two-button-service-proof.json)
record the bounded in-process Mission/Field/Robot simulation with separate persistent stores.
Field acquires the current observation locally after 1200 seconds of virtual Mission-to-Field
transit. Robot acts only within the declared same-identity displacement and one-action budget;
the independent device records the actual contact and counters, which return to Mission.

The focused suite covers S0, S1, S2 and S4 behaviors, target B, reference/context/proof mutations,
reservation failures and close/reopen recovery. A separate recorded run exercises six scenario
setups, including a failure after the real simulated impulse and an already-latched replay.
The latter produces no new impulse, execution journal or budget. An unverified command digest
produces durable HELD/UNKNOWN with no attributed contact or counters. These are simulation
receipts; no separate-process crash, power-loss, network-deployment, physical or VR claim follows.

## Remaining work

### M1.7 full lineage gate

- operation/intent-revision lineage on every Mission-view branch, with compatibility checks across
  two concurrent operations and reordered delivery;
- explicit handling for missing or incompatible parent references;
- deterministic machine-readable and visible evidence without cross-operation association.

### M1.8 remaining combined external-effect and long-delay gate

The [long-delay domain tests](m1/LONG_DELAY_DOMAIN.md) exercise the real M1 services and
deterministic link with up to 1200 seconds of one-way transit. The
[external-effect recovery proof](m1/EXTERNAL_EFFECT_RECOVERY.md) uses a persistent simulated
device outside the Robot journal. The combined [M1.8b proof](m1/EXTERNAL_EFFECT_LONG_DELAY.md)
now runs that device through the delayed Mission/Field domain. Dispatch binds its identity
durably; recovery observes without blind replay and rejects a missing or substituted adapter. An
unknown outcome remains held, and a terminal event alone does not manufacture measured completion
telemetry in the normal Field.

The integrated Python suite passes 185 tests, including six M1.8b scenarios, eleven persistent
M1.8c budget cases, and ten local ownership cases. The historical golden session and its six committed files remain unchanged,
and the release gate remains unchanged. Its explicit dummy fixture compatibility is documented;
it is not an external observation guarantee. The [exclusive local Robot owner lock](m1/EXCLUSIVE_ROBOT_OWNERSHIP.md)
now excludes competing services on the same canonical database path through external I/O and
durable resolution. Contention is retryable before inbox/recovery mutation; owner process death
permits observe-only recovery. Separate databases and multi-host/device fencing remain outside
this guarantee.

The remaining full M1.8 gate requires:

- stable effect identity and causal lineage across plan revisions;
- machine-readable evidence connecting those decisions and the independently recorded effect.

### Remaining M2 work

- desktop/VR target authoring and consumption of the time-sampled `KinematicPreview`;
- available provenance connecting confirmed, arrival and target branches, with missing references
  shown explicitly and without treating a preview as an execution command; complete multi-operation
  lineage remains the later M1.7 gate.

### M3a simulation gate

- known two-button simulation fixture with explicit identity and independently recorded effect
  state;
- S0–S10 scenario/oracle rows with exact target, action/outcome and effect-counter invariants;
- authorized same-identity displacement, with no substitution when buttons are swapped or
  indistinguishable;
- observation/hold decisions for ambiguity, missing targets, obstacles and exhausted budgets;
- already-acquired-effect and late-cancellation outcomes;
- controlled comparison of a fixed-skill baseline, delayed 2D authoring and delayed VR authoring
  under the same local autonomy and controller;
- simulation evidence only; no physical-hardware claim.

### M3b physical-fixture gate

- calibrated SO-101 with a measured articulated mirror in Unreal;
- real two-button fixture, independent button instrumentation and an independent effect register;
- `PressButton` skill, independent local stop and conservative test conditions validated before
  delayed trials;
- M3a oracles transposed to the physical fixture, with Robot and independent fixture evidence
  recorded separately for every result.

M3 remains incomplete until both full M3a and M3b pass. M3a.1 implements the bounded subset above; the remaining simulation rows and physical gate are still open.

### Later M4/M5 work

- VR target rebinding beyond the bounded two-button rule;
- capability registry, typed procedure templates and robot-independent intent;
- broader context acquisition, plan revision, incident bundles and assistance requests;
- optional non-authoritative LLM proposals, excluded from the first decision oracle and safety gate;
- Simulation Worker, LeRobot integration and learned policies.

## Evidence boundary

The M2.5 runtime actor, public scene recipe, platform reports, and synthetic PNG are implemented
in this bounded tranche; they introduce no hardware path. M1.7a has the separate implementation
and validation cited above; the bounded M1.8b proof and M1.8c budget are implemented while the full M1.8 gate remains
open. M2.2, M2.3, M2.4, and M2.5 have their implementation and machine-readable Linux/Win64
platform records. M2.7, the M2.8a preview math core, and bounded M2.9a are complete with
machine-readable Linux/Win64 platform records; M2.9, desktop/VR authoring and the full #20/#21
integration gates remain open. A milestone is complete
only after its deterministic replay, machine-readable artifacts and visible result satisfy the gate
in the [roadmap](../ROADMAP.md).

The bounded M3a.1 implementation does not close M1.7 or M3. Full M3a still requires its remaining scenario rows, and M3b's physical-fixture evidence is not present.

`v0.1.0` remains the historical M1 tag; later planned increments do not add capabilities to it.
`v0.2.0` remains the M2 target and makes no physical SO-101 or hardware-control claim.

## Safety status

No public hardware-control path exists. Nothing in the current repository should command a
physical robot. The M1 release gate operates entirely on the public dummy path. Unreal Engine
5.8.2 is verified on the reference Windows platform for `v0.1.0` and on Linux/Win64 for the M2.2
protocol, M2.3 math-core, M2.4 oracle, M2.5 actor, M2.7 IK, M2.8a preview, and bounded M2.9a
articulated-scene evidence; the M2.9a image is a synthetic desktop fixture replay and does not
claim measured telemetry, VR, or hardware. `v0.1.0` does not claim Unreal support on Linux. The
M2.7, M2.8a, and M2.9a slices introduce no hardware-control path.
