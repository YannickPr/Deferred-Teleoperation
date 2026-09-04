# Project status

This file separates what exists from what is planned.

## Implemented in the M0 branch

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

## Planned, not implemented

- Mission, Field or Robot processes;
- persistent inbox/outbox;
- delay and failure emulator;
- `OperationIntent`, `OperationPlan`, `TaskAssignment` or `ExecutionContract` runtime models;
- Unreal VR visualization;
- SO-101 geometry, kinematics or hardware integration;
- Simulation Worker;
- LeRobot integration;
- learned policies or LLM planning.

## Safety status

No public hardware-control path exists. Nothing in the current repository should command a physical robot.
