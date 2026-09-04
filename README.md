# Deferred Teleoperation

**A research prototype for delay-tolerant, VR-mediated shared autonomy with remote robots.**

> **Status:** M0 and the M1 delay-tolerant dummy are complete. The `v0.1.0` release gate is
> satisfied with portable Python CI and Unreal Engine 5.8.2 verification on the reference Windows
> platform. No physical robot path is enabled.

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
| SO-101 twin | M2.2 canonical transforms and generic FK; SO-101 golden fixtures, Unreal articulation and IK remain planned |
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

M2 starts from a pinned SO-101 structural source and a deterministic canonical description. The
[canonical Unreal kinematics guide](docs/m2/CANONICAL_KINEMATICS.md) documents the generic C++
fixed/revolute tree, its Blueprint boundary, and the remaining limits of this increment.

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
