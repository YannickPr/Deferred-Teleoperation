# Canonical terminology

The project uses one semantic spine from operator intent to physical control. Names at different levels are not interchangeable.

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

## OperationIntent

Desired operator-level outcome plus evidence and constraints: language, gesture, spatial anchors, target selectors, priority, validity and approval preference. It need not name a robot or an exact current entity.

## GroundedOperation

Field-side binding of selectors and anchors to entities, regions and explicit assumptions in one operational world revision.

## OperationPlan and TaskGraph

A versioned decomposition of an operation into typed, schedulable `TaskNode` objects and task-to-task dependencies. Dependencies never refer to a robot pose. If a pose or region matters, reaching it is itself a task with a success predicate.

## TaskAssignment

A replaceable Field decision that maps one task to a robot or coordination group according to capabilities, availability and resource leases. Reassignment does not rewrite mission semantics.

## ExecutionContract

A bounded contract issued to exactly one Robot Runtime. It contains local goal predicates, invariants, permitted skills, required observations, interruption behavior, autonomy/retry/risk budgets and validity information.

A multi-robot operation produces one contract per robot plus a Field coordination group; it never creates one shared motor-level contract.

## SkillInvocation

Robot-local invocation of a typed capability such as `PressButton`, `AcquireContext` or `MoveToRegion`.

## MotionPlan

Robot-local geometric or dynamic path that realizes a skill while satisfying current physical constraints.

## ActuatorCommand

Robot-local position, velocity, torque or lower-level command. It never crosses the delayed Mission/Field boundary as the primary operator instruction.

## Frequently confused terms

- `Field operational estimate` is the authoritative revision used for operation, not omniscient truth.
- `Mission world replica` is delayed and may contain projections.
- `Arrival Belief` is projected for the estimated arrival time of a newly sent intent.
- `Target Branch` is a conditional outcome, not a confirmed future.
- `Kinematic Preview`, `Mission Prediction`, `Physics Simulation`, `Replay` and `Counterfactual Simulation` are different products.
- Qualify every use of `policy`: `MLPolicy`, `ApprovalPolicy`, `ExecutionPolicy`, `ContextAcquisitionPolicy`, or `DivergenceResponsePolicy`.
