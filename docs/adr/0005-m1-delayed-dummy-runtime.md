# ADR 0005: M1 delayed-dummy runtime authority and recovery

- Status: Proposed for M1
- Date: 2026-09-04

## Context

The M1 vertical slice must exercise the canonical semantic path without Unreal, robot
hardware, or a real-time Mission connection. It must also make three different kinds of
state visible without conflating them: Field-confirmed state, predicted arrival state, and
the outcome requested by the operator.

At-least-once delivery means transport deduplication alone is insufficient. A repeated
intent must not create a second accepted operation, and a repeated contract must not cause
a second effect. Conversely, Field and Robot must keep making safe local progress while
Mission is disconnected.

## Decision

M1 runs four independent Python processes:

```text
operator / demo CLI
-> Mission
-> deterministic delayed link
-> Field
-> dummy Robot
```

Mission, Field, and Robot each own a separate SQLite database. The link remains the
volatile, non-authoritative test instrument defined by ADR 0004. Every receiver persists an
envelope before returning its ACK, and every sender retains an outbox record until that ACK
returns.

Mission creates and durably submits one constrained `PressButton` `OperationIntent`. Its
read model keeps the following representations separate:

- **Confirmed State:** the last measured `SiteSnapshot` received from Field;
- **Arrival Belief:** the `RobotForecast` and deterministic `PredictionManifest`, both
  explicitly `PREDICTED`;
- **Target Branch:** the requested button outcome, explicitly `OPERATOR_ASSERTED`.

Field binds only the known `dummy-button-1` fixture. Before admission it validates expiry,
the whitelist, selected executor, approval policy, capability, and contract revision. A
valid intent yields one `GroundedOperation`, one-node `OperationPlan`, `TaskAssignment`, and
revision-1 `ExecutionContract`. An expired or unsupported intent yields an explicit
`RECEIVED -> HELD` event and is never sent to Robot. Once admitted, Field has no dependency
on Mission availability.

The dummy Robot exposes only the typed `PRESS_BUTTON` capability. It durably relates an
assignment and contract, records dispatch before execution, and advances through:

```text
VALIDATING
-> APPROACHING
-> CONTACTING
-> VERIFYING_EFFECT
-> RETRACTING
-> SUCCEEDED
```

Each phase records `safe_to_interrupt` metadata. The injectable clock makes phase tests
deterministic. For the dummy backend, the effect counter, terminal result, and terminal
event share one SQLite transaction keyed by `press:{operation_id}:{revision}`. A semantic
duplicate contract therefore replays the durable terminal result without incrementing the
effect counter. This is an effect-once guarantee for the database-backed dummy only; it is
not a claim that a future physical action can be exactly-once.

`dtt-demo delayed-dummy` supervises all four processes with isolated stores. The
`short-visible-delay` profile demonstrates the nominal path. `short-visible-fault` adds
seeded delay, jitter, duplication, reordering, and a short blackout. The optional Mission
restart demonstration proves that Field and Robot finish locally and Mission reconciles
from retained endpoint outboxes after reconnecting. `dtt-inspect` aggregates the causal
history across the three authoritative stores.

## Consequences

- M1 has a runnable, observable vertical slice without Unreal or physical hardware.
- Mission disconnection does not revoke admitted Field work.
- Message identity and semantic operation/contract identity are deduplicated separately.
- Dependency reordering is retryable without an unbounded poison-message loop.
- Structured logs and durable histories expose IDs, ACKs, phases, the single dummy effect,
  and final reconciliation.

Target ambiguity, reassignment, multi-robot coordination, real geometry, learned planning,
scanning, physical cancellation, and hardware safety remain outside this decision.
