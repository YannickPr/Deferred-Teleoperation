# M1 long-delay domain proof

This proof exercises the existing M1 `MissionService`, `FieldService`, and
`DummyRobotService` with separate SQLite `NodeStore` files. The Mission/Field boundary is
scheduled by `DeterministicLink` and `FaultProfile`; the Field/Robot leg is delivered by the
same in-process adapter used by the domain tests because M1 has no second link endpoint.
The scheduler advances the virtual clock to the next link delivery or the next outbox instant
that is eligible by `created_at`, `not_before`, and `next_attempt_at`; an envelope is never
sent before those timestamps. The suite performs no real-time sleep, network I/O, engine
launch, or actuator operation.

## Reproduce

From the repository root, after installing the development dependencies described in
[CONTRIBUTING](../../CONTRIBUTING.md):

```bash
python -m pytest -q tests/test_long_delay_domain.py

python -m ruff check tests/test_long_delay_domain.py
```

The nominal parameter set is `0 s`, `30 s`, `900 s`, and `1200 s` one-way delay. Each
operation has an explicit TTL of `2 × one-way-delay + 120 s`, which covers the two delayed
Mission/Field directions and a margin. The test records that the dummy effect is committed
before the terminal event can return to Mission, then checks terminal reconciliation after
that return. Every successful run has exactly one durable journal effect.

The duplicate/restart cases set deterministic transport duplication and reopen either the
Mission or Field service and its SQLite file after the intent is durably admitted. This is a
service/database restart inside the harness, not an operating-system restart. Inbox
deduplication and outbox delivery converge while Robot's durable effect count remains one. The
`900 s` delay with a `60 s` TTL is a separate transport-expiry proof: the link drops the
frame before Field receives it, so there is no admission, effect, or false Mission success.
An additional boundary case schedules the frame before expiry, persists it, and only then
lets Field process it; Field emits an explicit `HELD` result and never dispatches Robot.

The fifteen-minute blackout case uses `one_way_delay_seconds = 0` and the distinct virtual
interval `[60, 960)`. Submitting at virtual second 60 defers delivery to second 960. This
keeps blackout duration separate from link latency.

## Evidence and limits

The assertions cover the dummy domain, SQLite persistence, transport scheduling, duplicate
delivery, restart recovery, TTL admission, and the effect counter. They do not establish
forecast validity across fifteen minutes, consistency after a changed physical world,
multi-operator arbitration, real network behavior, Unreal rendering, or physical robot
safety. Those remain later validation gates. The TTL boundary proof also does not claim a
general user-interface expiry policy: it verifies the current Field admission rule at the
processing boundary.
