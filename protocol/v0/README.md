# Experimental protocol namespace `dtt/0`

The protocol is intentionally incomplete and unstable. M0 defines only enough shared structure to build conformance fixtures for the first vertical slice.

## Current contract

`message-envelope.schema.json` describes transport-independent metadata around a typed payload. It does not define delivery, storage or physical-execution semantics by itself.

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
