# M1.8b external effect under long delay

This proof composes the real Python M1 services through durable stores:

```text
Mission -> DeterministicLink (outbound) -> Field
       -> DummyRobotService(external_effect_adapter=PersistentDummyExternalEffect)
       -> Field -> DeterministicLink (return) -> Mission
```

The two links are independent deterministic schedulers driven by one virtual
clock. The nominal matrix covers a symmetric 1200 second one way delay and an
asymmetric 900 second outbound / 1200 second return path. A delay is scheduled
from the actual send instant. Each envelope is checked independently: the
sender can send it only when `created_at`, `not_before`, and `expires_at` allow
the send, while the link delivers a scheduled copy only when its delivery
instant remains before `expires_at`. The harness advances time only to the next
link delivery or genuinely eligible outbox attempt. The local Field queue case
persists an envelope before expiry and evaluates its expiry later, at the
handling boundary. Send, delivery, and queue decisions all use the same virtual
clock.

The external device is a separate append-only JSON-lines file. It is deliberately
non-idempotent: every `press(effect_key)` appends one pulse, including a repeated
key. Robot's `effect_count` remains zero on this path. The crash case persists a
pulse, interrupts Robot before its terminal commit, reopens Robot's SQLite store
and the adapter, then submits the same contract again. Recovery observes the
existing device record and produces exactly one `SUCCEEDED` result. When the
reopened adapter reports `UNKNOWN`, recovery produces `HELD` and never reports a
false success. Contract revision 1, the semantic effect key, the device identity,
and observation identity are checked after recovery.

Two expiry boundaries are covered. A 60 second TTL with a 900 second outbound
delay expires in the transport and is never admitted by Field, so no pulse is
possible. A separate local Field queue persists the inbox first, waits until the
same 60 second TTL boundary, and then processes the intent; Field emits `HELD`
without creating a Robot assignment, pulse, or completion snapshot. A fifteen
minute blackout remains a transport condition and is covered by the preceding
domain proof; these tests use the local queue to exercise the distinct
post-inbox boundary.

Each nominal invocation writes a small machine-readable result table named
`external-effect-long-delay-results.json` below pytest's temporary directory.
It is an execution report, not a committed or golden artifact.

Run the proof from the repository root:

```text
PYTHONPATH=python/src python -m pytest -q tests/test_external_effect_long_delay.py
```

The virtual clock, dummy Field/Robot domain, deterministic schedulers, SQLite
stores, and JSON-lines device are test fixtures. This does not validate a
15-minute weather forecast, a changed physical world, a real network, an OS
restart, or physical hardware. Reopen means closing and reopening the involved
service's SQLite store and the external adapter; it does not restart the whole
operating system. The proof does not provide cross-revision identity or budget
enforcement. Expiry or cancellation after an external dispatch but before Robot
recovery is outside this tranche; no outcome policy for that in-flight window is
claimed. The proof does not extend the wire schema and does not close M1.8. A
real adapter still needs a durable attributable observation and its own safety
and actuation guarantees.

The broader checks remain:

```text
PYTHONPATH=python/src python -m pytest -q
ruff check .
python -m deferred_teleop.release_gate verify --scope ci --skip-pytest
```
