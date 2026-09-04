# M1 delayed-dummy demonstration

This development demonstration runs the complete M1 semantic path with four separate local
processes and no hardware.

## One-command proof

Install the package from the repository, then run the nominal profile:

```bash
python -m pip install -e ".[dev]"
dtt-demo delayed-dummy --profile short-visible-delay
```

The final `demo.completed` record reports the operation and contract IDs, estimated arrival,
all dummy phases, effect counter, final reconciliation, three provenance labels, and a ready
to run `dtt-inspect` command. Success requires `effect_counter: 1` and
`terminal_state: SUCCEEDED`.

Run the fault and recovery proof with:

```bash
dtt-demo delayed-dummy --profile short-visible-fault --restart-mission-after-admission
```

This profile deterministically injects delay, jitter, duplication, reordering, and a short
blackout. The supervisor stops Mission after Field admission, waits for the Robot effect,
then restarts Mission on the same database. Field and Robot do not restart or wait for
Mission to finish the admitted contract.

By default the supervisor creates a retained temporary data directory and prints its path.
Use `--data-dir PATH` to select an empty directory. The three files `mission.db`, `field.db`,
and `robot.db` are deliberately separate; the link has no authoritative store.

## Manual four-terminal launch

From an activated environment, start these commands in order, one per terminal:

```bash
dtt-link --mission-listen 127.0.0.1:8765 --field-listen 127.0.0.1:8766 --profile profiles/short-visible-delay.toml
dtt-robot-dummy --db .dtt-demo/robot.db --listen 127.0.0.1:8771
dtt-field --db .dtt-demo/field.db --link ws://127.0.0.1:8766 --robot ws://127.0.0.1:8771
dtt-mission --db .dtt-demo/mission.db --link ws://127.0.0.1:8765 --api 127.0.0.1:8770 --one-way-delay 0.15
```

In a fifth terminal, submit and inspect the operation:

```bash
dtt-operator submit-press-button
dtt-operator view
dtt-operator causal-history --correlation-id CORRELATION_ID
dtt-inspect causal-history --data-dir .dtt-demo --correlation-id CORRELATION_ID
```

The online Mission history contains what Mission knows. The offline inspector aggregates
Mission, Field, and Robot records, so it also shows assignment and contract delivery.

## Scope and safety

Only the database-backed dummy effect is enabled. No Unreal project, physical robot,
actuator, learned policy, or hardware interface is loaded by these commands. Stop the four
processes with the normal terminal interrupt when finished.
