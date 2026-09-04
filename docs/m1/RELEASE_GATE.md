# M1 golden session and v0.1 release gate

M1.6 turns the runnable dummy slice into reviewable release evidence. The gate is deliberately
split into an automated CI scope and a stricter release scope: CI proves the portable parts, while
the release scope also requires every reference-platform/manual item to be recorded as `PASSED`.

No command in this document loads SO-101 support, an actuator, a Simulation Worker, or a private
repository. The public dummy remains the only execution backend.

## Golden session

The committed fixture lives in `fixtures/m1/golden-session`. A fixed UTC clock and deterministic
UUID streams drive the real Mission, Field, and dummy-Robot services. The session records:

- the initial measured Field snapshot and dummy capability/state/forecast;
- a delayed operation, a transport retransmission, and a semantic duplicate contract;
- grounding, one-node plan, assignment, contract, skill invocation, all dummy phases, and effect;
- terminal Field forwarding and Mission reconciliation;
- the final strict Unreal-facing Mission view;
- one effect counter and an explicit causal chain.

Arrival carries a typed `PredictionManifest`. The target manifest remains an
`OPERATOR_TARGET_ASSERTION` with its condition and assertion evidence; the fixture does not
mislabel an operator request as predicted data.

Regenerate the fixture, then verify that strict protocol parsing and a fresh runtime replay produce
the exact committed result:

```powershell
.\.venv\Scripts\python.exe -m deferred_teleop.release_gate generate
.\.venv\Scripts\python.exe -m deferred_teleop.release_gate verify --scope ci --skip-pytest
```

The generator is idempotent. A hand-edited artifact fails its SHA-256 check; a coherent but changed
fixture fails the fresh runtime replay comparison until it is regenerated and reviewed.

## Adversarial matrix

`release/m1/scenario-matrix.json` maps every required profile to executable pytest evidence:

| Profile | Main invariant |
|---|---|
| reliable short delay | complete four-process reconciliation |
| duplicated intent | one accepted operation |
| duplicated execution contract | no more than one dummy effect |
| acknowledgement duplication/reordering | durable ACK idempotence |
| blackout before Field receipt | deferred, not silently lost |
| blackout after Field acceptance | Field/Robot continue without Mission |
| Mission restart with pending outbox | terminal state eventually reconstructs |
| Field restart after admission | durable assignment/contract still dispatch |
| dummy restart after dispatch | journal resumes without a second effect |
| effect before terminal delivery | terminal evidence remains pending durably |
| stale/expired operation | explicit hold; no Robot dispatch |
| malformed/unsupported input | explicit isolation; next valid input survives |
| low-bandwidth control plane | ACK lane is not starved by payload data |
| Unreal/Mission disconnect and reconnect | strict view resumes after server restart |

Run the complete matrix and write a machine-readable report:

```powershell
.\.venv\Scripts\python.exe -m deferred_teleop.release_gate verify `
  --scope release --report artifacts\m1-release-gate.json
```

The command runs only the uniquely referenced matrix tests; the normal `pytest` command still runs
the full suite. Retransmitted envelopes remain visible in the golden delivery log but do not become
new domain decisions.

## Causal inspection

`expected-domain-state.json` records the compact reference chain:

```text
OperationIntent
-> GroundedOperation
-> OperationPlan
-> TaskAssignment
-> ExecutionContract
-> SkillInvocation
-> DummyEffect
-> terminal ExecutionEvent
-> reconciled SiteSnapshot
```

For an ordinary demo database, inspect the same causal evidence with the command printed by
`dtt-demo`, for example:

```powershell
dtt-inspect causal-history --data-dir artifacts\m1-demo --correlation-id <UUID>
```

## Current release decision

The committed `release/m1/release-checklist.json` is the authority for tag readiness. Windows
Unreal Engine 5.8.2 build, parser tests, reconnect proof, and the visible three-state capture are
recorded in `UNREAL_VISUALIZATION.md`.

The release-gate change was recompiled on 4 September 2026 with Unreal Engine 5.8.2 (build
56702186), Development Editor / Win64, Visual Studio toolchain 14.44.35228, and Windows SDK
10.0.26100.0. All four `DeferredTeleop.M1` automation tests passed, including
`MissionView.GoldenFixtureParses` against the committed Python-generated fixture.

Windows is the reference Unreal platform for `v0.1.0`, matching the primary development and VR
environment. Linux remains mandatory for the portable Python, protocol, schema, golden-session and
scenario-matrix checks in CI, but this release does not claim Unreal Editor or runtime support on
Linux. A second Unreal installation is therefore outside the M1 release scope rather than an
unrecorded substitute for the Windows evidence.

All checklist items are now recorded as `PASSED`, so the full release command exits with status 0
and reports `release_ready: true` when the automated matrix also passes.

The portable CI scope remains expected to pass:

```powershell
dtt-release-gate verify --scope ci --skip-pytest
```

## Evidence summary

- Golden replay: strict `dtt/0`, terminal `SUCCEEDED`, exactly one dummy effect.
- Unreal presentation: measured confirmed state, predicted arrival belief, and operator-asserted
  target remain distinct; see `evidence/m1-unreal-reconciliation.png`.
- Hardware: not used and no public hardware-control path exists.
- Platform scope: Windows is the reference Unreal platform; Unreal on Linux is not claimed.
- Release: the `v0.1.0` gate is satisfied.
