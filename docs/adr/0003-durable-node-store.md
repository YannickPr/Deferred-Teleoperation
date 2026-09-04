# ADR 0003: SQLite durable node store and effect boundary

- Status: Proposed for M1
- Date: 2026-09-04
- Scope: M1 Mission, Field and dummy Robot processes
- Issue: #6

## Decision

Each logical node owns a separate SQLite database. The Python runtime configures WAL mode,
full synchronous commits, foreign keys and a 5000 ms busy timeout. Explicit numbered migrations
create the inbox, outbox, execution journal and append-only execution audit.

Incoming envelopes are committed before a handler sees them. A unique `message_id` makes
redelivery idempotent; reuse of the same identifier with different bytes is a loud conflict.
Handler completion and all outgoing consequences can be committed in one transaction. Pending
outbox records survive restart and remain at-least-once until acknowledged.

One execution-journal row owns each `(contract_id, contract_revision)`. A globally unique
`effect_key` prevents two contract rows from claiming the same semantic effect. Terminal fields
are immutable; later forensic annotations go into the append-only audit table.

## Exact guarantee boundary

For the M1 dummy, the effect is a database fact (`effect_count = 1`). That fact, the immutable
terminal result and the terminal-event outbox record are committed atomically. Redelivery and
restart can therefore produce **at most one dummy effect** and eventually retransmit one durable
terminal result.

SQLite cannot make a future physical action and a database commit atomic. A physical adapter
must use hardware acknowledgement, an idempotency key, or record an explicit uncertain outcome;
it must never infer exactly-once execution merely from a committed dispatch. This ADR makes no
exactly-once network or physical-effect claim.

## Recovery

- `PROCESSING` inbox rows are returned to `RECEIVED` after a detected process restart.
- committed outbox rows remain pending with their attempt count and next-attempt time;
- accepted and dispatch-recorded contracts resume from their durable journal state;
- terminal results and their outgoing events are committed together;
- corrupt typed envelopes raise without rewriting the stored bytes.

The crash-window suite closes and reopens real file-backed databases at every boundary specified
by #6. Distributed SQL, consensus, production HA, key storage and retention policy remain out of
scope.
