# Project status

This file separates what exists from what is planned.

## Implemented in the M0 branch

- public repository and Apache-2.0 code licence;
- minimal Python package metadata;
- CI for Python style, package import and JSON-schema fixtures;
- experimental `dtt/0` message-envelope schema;
- initial terminology, time/frame/provenance and threat-model documents;
- Unreal Engine host project and one runtime-plugin module skeleton.

## Requires local verification before M0 closes

- regenerate project files with Unreal Engine 5.8;
- compile `DeferredTeleopRuntime` on the primary development platform;
- open `DeferredTeleopDemo` and verify plugin discovery;
- call `Get Deferred Teleop Protocol Version` from Blueprint or observe the module startup log;
- record the exact engine version and result on the bootstrap pull request.

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
