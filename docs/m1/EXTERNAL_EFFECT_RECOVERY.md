# M1.8a external-effect recovery proof

M1's database-backed dummy effect is effect-once because the effect counter and
the Robot journal share one SQLite transaction. A physical actuator cannot use
that transaction. M1.8a makes this boundary executable with a deliberately
non-idempotent, file-backed button fixture.

The fixture journal is separate from `robot.sqlite3`. Each call to its
`press(effect_key)` appends one impulse, including repeated calls with the same
key. The Robot runtime receives the fixture through an injected
`ExternalEffectAdapter`; there is no command-line flag that enables it and no
hardware backend is loaded by default.

The recovery rule is intentionally conservative:

1. The Robot persists `ACCEPTED -> DISPATCH_RECORDED` before the first adapter
   call. That transition authorizes one `press` call and durably binds the
   non-empty adapter `device_id` to this contract and `effect_key`.
2. Once dispatch is durable, a restart calls `observe(effect_key)` and never
   calls `press` again. This closes the crash window after an external action
   and before the Robot terminal record. Recovery and resolution reject an
   adapter or proof whose device identity differs from that binding.
3. An attributable `APPLIED` observation resolves the contract to `SUCCEEDED`.
   `UNKNOWN` resolves to `HELD` with local outcome `OUTCOME_UNKNOWN`; a
   `NOT_APPLIED` observation after a dispatch uncertainty resolves to `HELD`
   with local outcome `NOT_APPLIED_AFTER_UNCERTAIN_DISPATCH`.
4. The terminal result stores the semantic `effect_key`, concrete `device_id`,
   observation identity, timestamp, outcome and adapter details. A bare boolean,
   count, or unaddressed status is rejected. Repeating a resolved observation
   cannot overwrite its terminal record.

The external path also requires the runtime clock to be at or after the durable
`dispatch_recorded_at` boundary before it creates `RUNNING`, calls `press` or
`observe`, or writes a terminal result. A clock-regressed restart raises a
`RecordConflictError` before adapter I/O and leaves the journal and outbox
unchanged (the inbox claim/reset is ordinary retry bookkeeping); after the
clock catches up, recovery observes the existing fixture record and continues
without replaying the impulse. Storage applies the same ordering check to a
terminal resolution.

The external resolution transaction writes the terminal event and immutable
contract result together, while leaving the Robot journal's `effect_count` at
zero. The counter belongs only to the historical database dummy path. The
fixture's persistent press record is the independent evidence used by
`observe`; the runtime never reads a test counter.

The Field only emits a reconciled site snapshot for `SUCCEEDED` when it has a
compatible measured or fused `RobotState` with the same correlation and robot
identifier. A `HELD` result or telemetry from a different operation does not
create a measured pose. The external fixture emits no Robot pose, so its
successful terminal event does not manufacture a site snapshot when
`dummy_fixture_compatibility=False`. The historical dummy compatibility option
is disabled by default. The golden replay passes
`dummy_fixture_compatibility=True` explicitly because its delivery order
presents the terminal before the dummy's pre-effect telemetry. That option is
not a physical observation guarantee. The external path has no message-shape
discriminator and never relies on one.

This is an experimental Python injection seam, not a physical exactly-once
protocol. It proves no atomicity between a real actuator and SQLite, supplies
no actuator safety mechanism, and does not advance the public wire schema or
Unreal contract. `HELD` uses the existing wire state; this tranche adds no typed
wire reason for it. It does not complete M1.8, and only execution-contract
revision 1 is covered. A real adapter must supply a durable, attributable
observation keyed to the intended effect and device before a future integration
can claim a stronger result.

Run the focused proof from the repository root:

```text
PYTHONPATH=python/src python -m pytest -q tests/test_external_effect.py tests/test_external_outcome_storage.py
```

The normal dummy golden and CI checks remain:

The release gate validates the committed golden history; this tranche does not
regenerate or claim a newly generated golden artifact.

```text
PYTHONPATH=python/src python -m pytest -q
ruff check .
python -m deferred_teleop.release_gate verify --scope ci --skip-pytest
```
