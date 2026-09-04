# Changelog

All notable public changes will be recorded here. The project follows semantic versioning only for
tagged releases; the experimental `dtt/0` protocol can still change incompatibly.

## Unreleased

### Added

- experimental external-effect recovery with a separate persistent simulated button, durable
  device binding, observe-only recovery after uncertain dispatch, and immutable outcome evidence;
- explicit historical dummy-fixture compatibility; normal Field reconciliation requires compatible
  Robot telemetry instead of manufacturing a measured completion pose from a terminal event;
- combined M1.8b virtual-time proof that drives the independent non-idempotent device through
  symmetric 1200-second and asymmetric 900-second outbound / 1200-second return delays, including
  duplicate-contract recovery and receiving-site expiry boundaries;
- pinned SO-101 structural source, canonical generated description and cross-platform drift gate.
- canonical Unreal C++ transforms, schema-scoped robot-description parsing, generic tree FK,
  tool-frame propagation, Blueprint functions and Automation Test coverage. The roadmap tracks
  the articulated-state protocol as M2.2 (#14), this FK implementation as M2.3 (#15), and the
  numerical cross-language oracle as the planned M2.4 increment (#16). M2.3 is validated on
  Linux and Win64 with Unreal Engine 5.8.2; the generated SO-101 check remains structural and
  is not an M2.4 FK oracle.

### Status

- M1.8 remains in progress after M1.8b: durable autonomy-budget accounting and stable effect
  identity across contract revisions are not implemented, and no physical hardware validation is
  claimed.

## 0.1.0 - 2026-09-04

### Added

- strict `dtt/0` models, generated schema, conformance fixtures, and durable node stores;
- deterministic delayed/faulted link with persistent endpoint retry semantics;
- separate Mission, Field, and dummy-Robot processes for the no-hardware `PRESS_BUTTON` slice;
- strict Unreal Mission view with confirmed, arrival-belief, target, and trajectory presentation;
- deterministic golden session, adversarial scenario matrix, and explicit `v0.1.0` release gate.

### Safety

- hardware control remains unavailable in the public repository and disabled by default;
- predicted, simulated, and operator-asserted data cannot be relabelled as measured evidence;
- duplicate contract delivery is bounded by the dummy effect-once journal.

The first runnable release. Every required item in `release/m1/release-checklist.json` passes.
Windows is the reference Unreal platform; portable Python checks remain covered on Linux in CI.
