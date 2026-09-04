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
| M1.8 | **In progress; M1.8b combined proof implemented** | External device runs through delayed Mission/Field domain; durable budget and cross-revision identity remain open |
| M2 | **In progress; target `v0.2.0`** | M2.1 structural model, M2.2 articulated-state protocol, M2.3 FK math core and M2.4 oracle are complete with Linux/Win64 UE evidence; Jacobian, IK and authoring remain |
| M3 | **Planned; M3a + M3b required** | Simulation oracle gate plus calibrated physical-fixture gate |
| M4/M5 | **Planned** | Broader robot-agnostic assignment, context acquisition and replanning |

M1.7a, M1.7, M1.8 and the enriched M3a/M3b slices are specified in the [delayed-intent
validation design](design/DELAYED_INTENT_VALIDATION.md). M1.7a is implemented in
[PR #30](https://github.com/YannickPr/Deferred-Teleoperation/pull/30), and the bounded M1.8b
combined proof is implemented in [the external long-delay evidence](m1/EXTERNAL_EFFECT_LONG_DELAY.md).
The remaining M1.7 and full M1.8 gates are not established by the design document.

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
  Linux and Win64.

The [M2.4 fixture contract](m2/KINEMATICS_FIXTURES.md) documents the numerical oracle and the
mandatory Python `--check` gate. The [M2.4 platform summary](m2/evidence/fk-oracle-platform-validation.json)
records the machine-readable eight-test native results and retains the earlier full 14-test run as
context. The generated SO-101 check covers model identity and structure, while the M2.4 fixture
contract supplies the numerical FK oracle. Wire parsing and the live Mission view preserve the
articulated model reference without validating SO-101 geometry; an FK consumer must call the
explicit description-backed validator. The post-rebase integrated Python validation passes 141
tests; the M2.4 oracle record retains its 121-test snapshot and the M2.2 record retains its
historical 135/20 context. Remaining M2 work is described in the [M2 design](design/M2_SO101_MATHEMATICAL_TWIN.md). Its target is `v0.2.0`; it remains a mathematical
and visualization milestone and does not require a physical robot.

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

## Planned, not implemented

### M1.7 full lineage gate

- operation/intent-revision lineage on every Mission-view branch, with compatibility checks across
  two concurrent operations and reordered delivery;
- explicit handling for missing or incompatible parent references;
- deterministic machine-readable and visible evidence without cross-operation association.

### M1.8 combined external-effect and long-delay gate

The [long-delay domain tests](m1/LONG_DELAY_DOMAIN.md) exercise the real M1 services and
deterministic link with up to 1200 seconds of one-way transit. The
[external-effect recovery proof](m1/EXTERNAL_EFFECT_RECOVERY.md) uses a persistent simulated
device outside the Robot journal. The combined [M1.8b proof](m1/EXTERNAL_EFFECT_LONG_DELAY.md)
now runs that device through the delayed Mission/Field domain. Dispatch binds its identity
durably; recovery observes without blind replay and rejects a missing or substituted adapter. An
unknown outcome remains held, and a terminal event alone does not manufacture measured completion
telemetry in the normal Field.

The integrated Python suite passes 115 tests, including six focused M1.8b scenarios. The historical
golden session and its six committed files remain unchanged, and the release gate remains
unchanged. Its explicit dummy fixture compatibility is documented; it is not an external
observation guarantee.

The remaining full M1.8 gate requires:

- stable effect identity across plan revisions and durable autonomy-budget accounting;
- machine-readable evidence connecting those decisions and the independently recorded effect.

### Remaining M2 work

- Jacobian in C++;
- constrained damped-least-squares IK with explicit status and residuals;
- articulated Unreal link visualization and cross-language golden evidence;
- desktop/VR target authoring and time-sampled `KinematicPreview`;
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

M3 remains incomplete until both M3a and M3b pass. Neither gate is currently implemented.

### Later M4/M5 work

- VR target rebinding beyond the bounded two-button rule;
- capability registry, typed procedure templates and robot-independent intent;
- broader context acquisition, plan revision, incident bundles and assistance requests;
- optional non-authoritative LLM proposals, excluded from the first decision oracle and safety gate;
- Simulation Worker, LeRobot integration and learned policies.

## Evidence boundary

This documentation update introduces no additional runtime implementation or hardware path. M1.7a has the
separate implementation and validation cited above; the bounded M1.8b proof is implemented while
the full M1.8 gate remains open. M2.2 and M2.4 have their implementation and machine-readable
Linux/Win64 platform records; Jacobian, IK and the remaining M2 visualization work are open. A
milestone is complete only after its deterministic replay, machine-readable artifacts and visible
result satisfy the gate in the [roadmap](../ROADMAP.md).

This documentation change does not close M1.7 or M3. M3a remains a future simulation gate, and
M3b's physical-fixture evidence is not present.

`v0.1.0` remains the historical M1 tag; later planned increments do not add capabilities to it.
`v0.2.0` remains the M2 target and makes no physical SO-101 or hardware-control claim.

## Safety status

No public hardware-control path exists. Nothing in the current repository should command a
physical robot. The M1 release gate operates entirely on the public dummy path. Unreal Engine
5.8.2 is verified on the reference Windows platform for `v0.1.0` and on Linux/Win64 for the M2.2
protocol, M2.3 math-core and M2.4 oracle evidence; `v0.1.0` does not claim Unreal support on
Linux.
