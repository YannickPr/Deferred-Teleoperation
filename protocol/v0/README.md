# Experimental protocol namespace `dtt/0`

The protocol is intentionally incomplete and unstable. M1 defines only the strict semantic spine
needed by the delayed dummy vertical slice.

## Current contract

`message-envelope.schema.json` is deterministically generated from strict Pydantic v2 wire DTOs.
It describes transport-independent metadata around typed payloads, but does not define delivery,
storage or physical-execution semantics by itself. Regenerate it with
`python -m deferred_teleop.schema`; CI verifies it with `--check`.

The constrained chain is:

```text
OperationIntent -> GroundedOperation -> OperationPlan (one TaskNode)
-> TaskAssignment -> ExecutionContract -> ExecutionEvent
```

Operation states are `DRAFT -> SUBMITTED -> RECEIVED_BY_FIELD -> ADMITTED | HELD | REJECTED`.
The M1 contract transition validator accepts only:

```text
RECEIVED -> ACCEPTED | HELD
ACCEPTED -> DISPATCH_RECORDED | CANCELLED
DISPATCH_RECORDED -> RUNNING
RUNNING -> SUCCEEDED | FAILED | HELD | CANCELLED
```

Inter-site assumptions:

- messages may be delayed, duplicated, reordered or retransmitted;
- delivery is at-least-once when connectivity and retention allow;
- consumers must persist deduplication/execution state where duplicate physical effects matter;
- expiry and supersession are application-level decisions;
- no message is trusted solely because it is syntactically valid.

## Versioning

- Protocol identifier: `dtt/0`.
- Breaking changes are allowed during the experimental phase.
- Fixtures are compatibility evidence, not a promise of long-term stability.
- A broader version freeze will not occur before the delayed physical button task has run end to end.
