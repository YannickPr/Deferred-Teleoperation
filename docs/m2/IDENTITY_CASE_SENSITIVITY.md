# M2 identity case sensitivity

Status: validated on LinuxEditor and WindowsEditor with Unreal Engine 5.8.2.

The M2 runtime treats the three fields of a robot model reference as exact
wire identity values: `model_id`, `model_revision`, and `description_hash`.
Comparisons in the articulated view, kinematic preview, and kinematic actor
therefore use case-sensitive string equality. A value that differs only by
letter case identifies a different model reference and fails the previous
reference comparison or the actor's same-model topology reuse check.

`description_hash` has the canonical form `sha256:` followed by 64 lowercase
hexadecimal digits. The prefix is case-sensitive as well as the digest
validation. Uppercase `SHA256:` is rejected even when the digest is otherwise
valid.

Protocol literals are exact too. The articulated parser rejects casing changes
in the protocol header, provenance, connection state, and terminal state. The
robot-description parser applies the same rule to its schema literal and the
coordinate-convention and `fixed`/`revolute` joint type literals. This keeps
wire vocabulary distinct from free-form identifiers.

This correction applies to field values. Unreal's JSON object lookup still
accepts case-only aliases in field names, unlike the Python wire models, and
can collapse those keys before validation. Exact key validation and shared
parser-conformance cases are tracked in
[issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47).
The final parsed model-reference values still undergo the exact comparisons above.

Model topology names stored as `FName` retain Unreal's case-insensitive lookup
semantics. Consumers needing exact joint-name matching must compare the wire
strings before conversion; this change does not redefine native name lookups.
Articulated wire `joint_name` strings use exact comparison for duplicate
detection, so two wire names that differ only by case remain distinct until
the model-bound `FName` validation boundary.

The corresponding automation tests include mutation cases for model IDs,
revisions, hash prefix, parser header/enums, robot-description schema and
joint types, and actor reinitialization. Both native runs pass all 43 contextual
tests, with build and editor exit code 0. The existing test registrations are
unchanged; the mutation cases strengthen their assertions. Source and report
hashes are recorded in the [platform validation](evidence/identity-platform-validation.json).
Earlier JSON evidence remains an unchanged record of its own source snapshot.
