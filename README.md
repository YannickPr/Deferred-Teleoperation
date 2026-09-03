# Deferred Teleoperation

**A research prototype for delay-tolerant, VR-mediated shared autonomy with remote robots.**

> **Project status:** architecture validated; implementation is starting. No runnable release is available yet.

Deferred Teleoperation explores how an operator can express a spatial and linguistic intent from a delayed representation of a remote environment, while an autonomous field site grounds, assigns, executes, adapts, or holds that intent without depending on a real-time round trip.

The initial public vertical slice is deliberately small:

```text
VR-authored OperationIntent
-> persistent delayed link
-> lightweight Field Server
-> autonomous robot execution contract
-> delayed execution result
-> reconciliation in Unreal Engine
```

The first physical demonstration will use a SO-101 arm and an independently instrumented button. A dummy implementation will come first and will exercise the same semantic path as the real robot.

## Core authority model

- **Robot Runtime:** physical safety, local perception, motion planning, skills, and recovery.
- **Field Server:** authoritative operational site estimate, task admission, assignment, active contracts, and short-horizon coordination.
- **Mission Server:** delayed confirmed state, arrival-time prediction, operator intent, and speculative target branches.
- **Unreal VR client:** spatial authoring and visualization; never the source of remote physical truth.
- **Simulation Worker:** optional and non-authoritative, reusable for prediction, validation, replay, and training.

## Design constraints

- Inter-site latency may exceed 15 minutes and connectivity may be intermittent.
- Accepted field work must remain safe and useful without an operator connection.
- Messages may be delayed, duplicated, reordered, or retransmitted; physical effects must remain idempotent at the application level.
- Hardware execution is disabled by default.
- The first protocol is explicitly experimental (`v0`) and will not be frozen before an end-to-end delayed physical task has been demonstrated.

## Planned first milestones

1. Runnable delay-tolerant dummy using `OperationIntent -> ExecutionContract`.
2. Mathematical SO-101 twin in Unreal Engine, implemented without a skeletal mesh.
3. VR target authoring and confirmed / arrival / target visualization.
4. Autonomous delayed button press with independent hardware verification.

## Safety and scope

This repository is a research and learning project. It is not certified for safety-critical, industrial, medical, or spaceflight use. Any future hardware path will require explicit configuration, conservative limits, local physical safety mechanisms, and documented test conditions.

Documentation, contribution guidance, protocol fixtures, Python services, and the Unreal plugin will be added through reviewed M0 pull requests before the first runnable release.
