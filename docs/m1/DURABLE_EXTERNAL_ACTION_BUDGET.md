# M1.8c durable external-action budget

The Robot runtime gives an external adapter one local autonomous reservation per
`operation_id`. This is a Robot policy snapshot; it is not a Mission permission
and it does not add a protocol field or execution revision.

For an external adapter, admission creates an `autonomy_budget` row with the
immutable contract binding, literal `attempt_limit = 1` and `action_limit = 1`,
and a positive finite `max_elapsed_seconds` window (60 seconds by default).
`window_started_at` is the durable journal acceptance time. The reservation
transaction validates the persisted policy and trusted service clock, then
sets both counters to one and records `DISPATCH_RECORDED` with the adapter
`device_id` in one SQLite `BEGIN IMMEDIATE` transaction. The adapter is called
only after that commit. A crash after the reservation therefore burns the one
action; recovery observes the addressed device and never presses again.
The focused proof covers crashes before adapter I/O and after the device effect;
both paths reopen and observe without a second press.

The original M1.8c proof assumed one active Robot instance: `BEGIN IMMEDIATE`
serializes a reservation but cannot fence external I/O after its commit.
The follow-up [exclusive local owner lock](EXCLUSIVE_ROBOT_OWNERSHIP.md) now
covers this gap for cooperating services on the same canonical database path,
including owner death and observe-only recovery. The original budget proof
remains a record of its earlier source snapshot; the new subprocess tests
supply the concurrency evidence.

The policy is compared only before a new press. A changed restart configuration
holds an unreserved accepted contract with `BUDGET_POLICY_CONFLICT`; it cannot
rewrite a snapshot. A contract whose dispatch is already durable, or whose
outcome is terminal, follows the existing attributed observe/replay path even
when its deadline or current configuration has changed. The existing clock
rollback guard remains in force before external observation.

A bound budget requires its external adapter even before dispatch and during
terminal replay; a missing adapter or a different durable device identity is
rejected before any dummy fallback.

If the service clock is behind durable acceptance or dispatch, processing waits
for a trusted catch-up and leaves the journal unchanged; rollback does not
invent an early terminal `HELD` event.

The dtt/0 transition table admits `ACCEPTED -> HELD` for a pre-dispatch
budget refusal; all other protocol states and payloads remain unchanged.
Operation scope is bound to the first revision-1 contract. A different
contract for the same operation is denied before the journal `effect_key`
constraint is reached. Its denial, stable HELD event, first envelope JSON, and
inbox completion are committed together. Repeated fresh envelopes reuse the
stored event bytes. Revision 2 is held before budget admission.

Schema migration 4 records v3 dispatches with a durable device identity as
`LEGACY_OBSERVE_ONLY`; these rows receive no invented policy or reservation.
An old `ACCEPTED` row without a budget becomes
`LEGACY_UNBUDGETED_HOLD`, with no adapter call or external proof. v3 dispatches
without a device identity retain the refusal to guess an adapter. Because v3
does not identify dummy versus external mode, every historical `ACCEPTED` row
is conservatively held; that legacy dummy distinction cannot be recovered.

This tranche deliberately leaves cross-revision semantic effect identity and
retry or multi-action execution open.
