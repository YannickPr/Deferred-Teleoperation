# M3a two-button spatial slice (simulation-only)

This simulation-only slice provides the independent simulated-device fixture
and the pure Robot policy
used by the M3a observed-spatial-intent integration. It covers one immutable
revision-1 `EnsureButtonLatched(desired_latched=true)` operation. The existing
M1/M2 payloads and golden bytes remain unchanged.

`deferred_teleop.m3a_types` contains frozen local value types for the persisted
observation, exact intent reference, execution context, command, level proof,
and local decision. The wire `TwoButtonEffectEvidence` carries the independent
post-dispatch contact, counters, latches, outcome, and command digest.
`canonical_bytes()` uses sorted compact UTF-8 JSON and
`canonical_digest()` returns `sha256:<64 lowercase hex digits>`. Observation and
command values compute their digest over the payload without the digest field;
when a caller supplies a digest, construction verifies it.

The policy entry point is:

```python
decision = decide_two_button(
    intent,
    reference_observation,
    current_observation,
    level_evidence,
    expected_source_id=observer_id,
    expected_device_id=device_id,
)
```

It validates the reference observation ID, recomputed digest, target detection,
pose, frame, calibration, world revision, and observed timestamp before making
a decision. It accepts a unique same-identity detection at zero displacement
(`EXECUTE`) or within the inclusive tolerance (`REANCHOR_EXECUTE`). Missing,
non-unique, candidate-set, and over-tolerance detections produce
`HOLD_AMBIGUOUS`. Frame/calibration differences produce
`HOLD_CONTEXT_MISMATCH`; changed reference payloads produce
`HOLD_REFERENCE_MISMATCH`. A trusted level proof that the named target is
already latched produces `RECOGNIZE_EFFECT`, which is the preacceptance path
and does not derive a command.

`derive_spatial_press_command()` turns an execute decision into an immutable
`SpatialPressCommand`. It copies the selected observation pose and source IDs;
it never reads a fixture or chooses a nearest target.

`TwoButtonFixture` owns hidden simulated button positions, collision radius,
scenario setup, level state, and the append-only device journal. The only
simulated contact entry point is `press_at(SpatialPressCommand)`. Each press row records the exact
canonical command bytes and digest, effect key, commanded position/frame/
calibration, simulated contact (`A`, `B`, or `NONE`), both counters, and both
latches. Every append flushes and `fsync`s the file before returning; the parent
 directory is synced when the platform exposes directory descriptors. Closing
 and reopening the fixture demonstrates visibility after reopening the objects
 in the test process; it does not prove an OS process restart, power-loss, or
 filesystem durability.

`SpatialExternalEffectAdapter.bind(effect_key, command)` persists an immutable
effect-key-to-command binding and returns a receipt containing device ID,
effect key, and command digest. Equal rebinds return the same receipt. A
different command for an existing key raises `SpatialBindingConflictError`.
`press(effect_key)` can dispatch only the command loaded from that binding and
calls the fixture's `press_at`; it accepts no target name or mutable position.

In the service harness, Mission persists the authoring reference and authors
the intent. It does not send a current observation that was known before the
delay. After the virtual 1200-second transit, `M3aFieldService` records the
current observation directly from its local observer, then builds the Field
bundle. The bundle preserves the reference observation's source, provenance,
`observed_at`, `produced_at`, and world revision instead of re-dating it.
The older Mission helper `publish_m3a_current_observation` remains reserved for
compatibility/test replays and is not this local proof.
Robot's post-dispatch `TwoButtonEffectEvidence` is durably committed with the
external outcome, relayed Field-to-Mission, and used by `m3a.view`; counters,
latches, contact, and terminal result remain `null` until that post-action
proof exists. An already-latched preacceptance therefore cannot attribute its
unrelated seed impulse to the new operation.

Field and Mission persist the first command-digest-verified effect proof for a
contract and compare later copies by canonical payload plus source,
destination, and correlation semantics. Identical transport retries retain the
first proof; divergent copies are recorded as conflicts and cannot rewrite the
read model. A digest-unverified `UNKNOWN` is retained as one durable diagnostic
until an attributable proof exists, and never supplies physical facts. These
checks preserve consistency and replay behavior between the configured
services. They are not cryptographic authentication and do not establish that
a first message from a trusted service was genuine.

The focused proofs run in the public checkout with:

```text
PYTHONPATH=python/src python -m pytest -q tests/test_m3a_two_button.py
```

The service integration tests cover the five oracle groups: S0 nominal A
contact, S1 inclusive re-anchor and epsilon hold, intent/reference integrity
with exact duplicate bytes and durable conflict, S2 ambiguous A/B swap, and S4
already-latched preacceptance plus Robot/device reopen. They exercise the
Mission-to-Field 1200-second virtual transit followed by Field-local current
observation, Field's persisted reference and plan/context/assignment/contract
bundle, Robot's retryable dependency ordering, the single M1.8c reservation,
the relayed post-effect proof, and the independent device journal.

The fixture and contact proof are simulation-only. The slice does not claim
M3a closure, cancellation, new-impulse semantics, revisions, physical
validity, or hardware behavior.
