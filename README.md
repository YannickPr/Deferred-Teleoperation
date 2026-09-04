# Deferred Teleoperation

**A research prototype for delay-tolerant, VR-mediated shared autonomy with remote robots.**

> **Status:** M0, the M1 delay-tolerant dummy, and the bounded M2.2–M2.5 protocol, math, oracle, and actor
> slices are complete. The bounded M2.7 constrained-IK implementation is complete with Linux and
> Win64 Unreal Engine 5.8.2 evidence: each run records 35 contextual successes, including all 13
> IK tests, with build/editor exit code 0.
> The `v0.1.0` release gate is satisfied with portable Python CI;
> M2.2–M2.5 have separate Unreal Engine 5.8.2 evidence on Linux and Win64. No physical robot path
> is enabled.

Deferred Teleoperation explores how an operator can express a spatial and linguistic intent from a delayed representation of a remote environment, while an autonomous field site grounds, assigns, executes, adapts, or holds that intent without depending on a real-time round trip.

The initial vertical thread is deliberately small:

```text
VR-authored OperationIntent
-> persistent delayed link
-> lightweight Field Server
-> autonomous execution contract
-> delayed result
-> reconciliation in Unreal Engine
```

The first physical demonstration will use a SO-101 arm and an independently instrumented button. A dummy implementation will come first and exercise the same semantic path as the real robot.

## Current status

| Area | State |
|---|---|
| Architecture and authority model | Defined, still experimental |
| Public Python package | M0 foundation complete |
| Unreal Engine plugin | M1 Mission view, strict client and reconciliation scene; verified with UE 5.8.2 on Windows |
| Delay-tolerant dummy | Runnable M1 Mission / Field / dummy-Robot development slice |
| M1 release gate | Passed: golden replay, adversarial matrix, portable CI, and Windows UE 5.8.2 evidence |
| SO-101 twin | M2.2 articulated-state protocol (#14), M2.3 canonical transforms/generic FK code (#15), M2.4 cross-language numerical oracle (#16), and the bounded M2.5 rigid-link actor are complete with Python 3.11/3.12 and Linux/Win64 UE 5.8.2 evidence; the M2.7 constrained-IK implementation is complete with 13 targeted tests and Linux/Win64 UE 5.8.2 evidence; preview and VR authoring remain pending |
| Autonomous delayed button press | Planned for M3 |
| Hardware control | Disabled by default; not implemented publicly |

See [project status](docs/STATUS.md), [canonical terminology](docs/concepts/TERMINOLOGY.md), [time, frames and provenance](docs/concepts/TIME_FRAMES_AND_PROVENANCE.md), the initial [threat model](docs/security/THREAT_MODEL.md), and the [roadmap](ROADMAP.md).

## Core authority model

- **Robot Runtime:** physical safety, local perception, motion planning, skills, and recovery.
- **Field Server:** authoritative operational site estimate, task admission, assignment, active contracts, and short-horizon coordination.
- **Mission Server:** delayed confirmed state, arrival-time prediction, operator intent, and speculative target branches.
- **Unreal VR client:** spatial authoring and visualization; never the source of remote physical truth.
- **Simulation Worker:** optional and non-authoritative, reusable for prediction, validation, replay, and training.

## Canonical semantic path

```text
OperationIntent
-> GroundedOperation
-> OperationPlan / TaskGraph
-> TaskAssignment
-> ExecutionContract
-> SkillInvocation
-> MotionPlan
-> ActuatorCommand
```

M0 does not freeze all of these messages. It establishes the vocabulary, trust boundaries, test fixtures and minimum runtime skeleton needed to exercise the path vertically.

## Development quickstart

The Python package exposes the experimental `dtt/0` models, durable endpoint storage and a
deterministic development-link emulator.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
ruff check .
```

Run the local two-sided WebSocket link with a reproducible fault profile:

```bash
dtt-link --mission-listen 127.0.0.1:8765 --field-listen 127.0.0.1:8766 \
  --profile profiles/15min-blackout.toml
```

The emulator queue is intentionally volatile and never acknowledges on behalf of a receiver.
Durable endpoint outboxes retain messages for retry until a receiver persists the envelope and
its ACK returns.
See [ADR 0004](docs/adr/0004-deterministic-development-link.md).

Run the complete nominal M1 path with four local processes:

```bash
dtt-demo delayed-dummy --profile short-visible-delay
```

Run the deterministic fault/reconnect proof with:

```bash
dtt-demo delayed-dummy --profile short-visible-fault --restart-mission-after-admission
```

See the [M1 delayed-dummy guide](docs/m1/DELAYED_DUMMY.md) for the expected evidence,
manual four-terminal launch, online Mission queries, and offline causal-history inspection.
The [M1 Unreal visualization proof](docs/m1/UNREAL_VISUALIZATION.md) documents the local
Mission view, generated example scene, automated tests, and reproducible screenshot.
The [M1 release gate](docs/m1/RELEASE_GATE.md) binds a deterministic golden session to the
adversarial test matrix and records the evidence required for `v0.1.0`.
The runtime authority and recovery boundaries are recorded in
[ADR 0005](docs/adr/0005-m1-delayed-dummy-runtime.md).

M2 starts from a pinned SO-101 structural source and a deterministic canonical description. M2.2
is complete for the strict articulated robot-state/model-reference wire contract, Field relay,
opt-in Mission view, and the explicit description-backed validator boundary. Its LinuxEditor and
WindowsEditor record each lists the three targeted ArticulatedView Automation tests with `Success`
inside a 22-test contextual report, with build/editor exit code `0`; the compact [platform evidence](docs/m2/evidence/articulated-state-platform-validation.json)
also records the independent source hashes and unchanged M1 goldens. M2.3 provides the generic
C++ fixed/revolute tree and canonical/Unreal boundary (#15). The [canonical Unreal kinematics
guide](docs/m2/CANONICAL_KINEMATICS.md) documents the schema-scoped parser and its Blueprint
boundary. M2.4 is complete for the numerical cross-language oracle (#16): the [fixture contract](docs/m2/KINEMATICS_FIXTURES.md)
defines nine SO-101 cases, six independent Python reference tests, and three Unreal Automation
tests. Version 2 fixes the reference operation order so Python 3.11 and 3.12 generate identical
bytes; the final native validation passes the eight-test `DeferredTeleop.M2.Kinematics` selector
on Linux and Win64, as recorded in the [M2.4 platform summary](docs/m2/evidence/fk-oracle-platform-validation.json)
alongside the earlier full 14-test context. The post-rebase integrated Python validation passes
141 tests; the M2.4 oracle record retains its 121-test snapshot and the M2.2 record retains its
historical 135/20 context. The raw articulated feed does not validate SO-101
geometry: an FK consumer must call the description-backed validator and retain its diagnostics.
M2.4 supplies the numerical FK oracle. The bounded M2.7 constrained-IK implementation (#19)
adds a generic named-group solver with explicit tool frames, position-only and
position-plus-approach-axis tasks, deterministic damped least squares, limits and inspectable
results; no hardware path is claimed.

The bounded M2.5 rigid-link actor now provides three independent semantic layers,
explicit world-transform conversion, deterministic invalid-input preservation, and
debug primitives without robot mesh assets. See the [M2.5 actor guide](docs/m2/KINEMATIC_ROBOT_ACTOR.md)
and its [Linux/Win64 platform evidence](docs/m2/evidence/kinematic-actor-platform-validation.json).
The accompanying scene and PNG are a synthetic visual demonstration; they do not
represent measured telemetry, an operational UI, VR authoring, or hardware control.

The M2.7 acceptance slice is complete with a 13-test `DeferredTeleop.M2.IK` selector. Linux and
Win64 each record 35 contextual successes (13 IK plus 22 contextual tests), no warnings/failures/
not-run tests in process, and build/editor exit code 0. See the [constrained IK guide](docs/m2/CONSTRAINED_IK.md)
and the [M2.7 platform record](docs/m2/evidence/constrained-ik-platform-validation.json).

Verify the portable gate in CI-compatible mode:

```bash
dtt-release-gate verify --scope ci --skip-pytest
```

Run the full matrix and release decision with `dtt-release-gate verify --scope release`. Every
required automated and reference-platform item must pass.

The Unreal host project and plugin live under `unreal/DeferredTeleopDemo`. See [the Unreal notes](unreal/README.md). GitHub CI does not compile Unreal; M0 and the M1 visualization were therefore verified locally with Unreal Engine 5.8.2 on Windows, the reference Unreal platform for this release. Unreal on Linux is not claimed as supported by `v0.1.0`.

## Design constraints

- Inter-site latency may exceed 15 minutes and connectivity may be intermittent.
- Accepted field work must remain safe and useful without an operator connection.
- Messages may be delayed, duplicated, reordered, or retransmitted; physical effects must remain idempotent at the application level.
- Hardware execution is disabled by default.
- Protocol files are explicitly experimental (`v0`) until an end-to-end delayed physical task has been demonstrated.

## Safety and scope

This repository is a research and learning project. It is not certified for safety-critical, industrial, medical, or spaceflight use. Any future hardware path will require explicit configuration, conservative limits, local physical safety mechanisms, and documented test conditions.

## Contributing

Contributions and design critique are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before opening a pull request.
