# Project status

This file separates what exists from what is planned.

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

## Implemented toward M1

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

## Planned, not implemented

- VR interaction and headset-specific presentation (the desktop Unreal visualization exists);
- SO-101 geometry, kinematics or hardware integration;
- Simulation Worker;
- LeRobot integration;
- learned policies or LLM planning.
- Linux Unreal Engine 5.8 verification required before the `v0.1.0` tag.

## Safety status

No public hardware-control path exists. Nothing in the current repository should command a physical robot.
The M1 release gate operates entirely on the public dummy path and explicitly reports the missing
Linux Unreal proof instead of weakening or silently skipping that requirement.
