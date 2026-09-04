# ADR 0004: Deterministic development link

- Status: Proposed for M1
- Date: 2026-09-04

## Context

M1 must demonstrate a delayed Mission-to-Field path, including acknowledgements,
duplicates, reordering, blackouts, bandwidth pressure, and link-process restarts. The
transport is a test instrument: endpoint SQLite inboxes and outboxes from ADR 0003 remain
the authoritative source of delivery and processing state.

## Decision

`deferred_teleop.link` provides a pure, virtual-time `DeterministicLink` scheduling core.
A `FaultProfile` fixes the seed and all delay/fault parameters; optional per-message scripts
make exact scenarios reproducible. Both envelopes and ACK frames traverse this scheduler.

Two adapters are provided:

- `InMemoryTransport` implements the narrow `send`, `receive`, `acknowledge`, and `health`
  port for domain tests.
- `WebSocketRelay` exposes separate Mission and Field listeners so independently launched
  Python processes can communicate through the same fault scheduler. The `dtt-link` command
  loads a TOML profile and emits structured health snapshots.

The emulator uses volatile queues by design. It never acknowledges a message on behalf of
its destination. A receiver must first persist an envelope in its durable inbox, and only
then send an ACK. The sender removes no pending outbox record until that ACK returns. Thus a
link crash may lose an in-flight copy, but endpoint retry makes that loss temporary; inbox
deduplication makes delivery duplicates harmless at the application boundary.

ACK/control traffic has a separate bandwidth lane from data traffic. This prevents a large
payload from indefinitely starving delivery control while preserving deterministic timing.
Expired messages and capacity or disconnected-destination drops are reported, not hidden.
Metrics also expose queue depth, next scheduled delivery, duplicate injections, blackout
deferrals, transmitted bytes, and reconnects.

## Consequences

- A fixed profile and seed produces an identical delivery schedule.
- Blackout and 15-minute-delay scenarios are tested with virtual time rather than real waits.
- Transport delivery is at-least-once and non-authoritative; application idempotence belongs
  to the durable endpoint store.
- WebSocket is a local development adapter, not the production transport choice.

DTN Bundle Protocol, QUIC, production authentication/key management, media/blob transfer,
Unreal integration, and real robot integration remain outside this decision.
