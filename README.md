# Deferred Teleoperation

**A research prototype for delay-tolerant, VR-mediated shared autonomy with remote robots.**

> **Status:** M0 bootstrap in progress. No runnable release is available yet.

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
| Public Python package | M0 bootstrap |
| Unreal Engine plugin | M0 skeleton; local UE 5.8 build not yet verified |
| Delay-tolerant dummy | Planned for M1 |
| SO-101 twin | Planned for M2 |
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

The Python package currently exposes only project metadata and protocol-fixture tests.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pytest
ruff check .
```

The Unreal host project and plugin skeleton live under `unreal/DeferredTeleopDemo`. See [the Unreal bootstrap notes](unreal/README.md). The plugin has not been compiled by CI and must be verified locally against Unreal Engine 5.8 before M0 closes.

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
