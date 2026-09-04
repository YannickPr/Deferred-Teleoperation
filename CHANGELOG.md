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
- strict M2.2 articulated robot-state/model-reference protocol (#14), including finite canonical
  pose/joint values, provenance-preserving Field relay, the opt-in Mission articulated view, and
  an explicit description-backed validator with visible diagnostics;
- LinuxEditor and WindowsEditor Unreal Engine 5.8.2 evidence for the three M2.2 ArticulatedView
  tests, with the compact platform record binding raw report hashes to 19 source/fixture hashes
  and six unchanged M1 golden files;
- canonical Unreal C++ transforms, schema-scoped robot-description parsing, generic tree FK,
  tool-frame propagation, Blueprint functions and Automation Test coverage. The roadmap tracks
  the articulated-state protocol as M2.2 (#14) and this FK implementation as M2.3 (#15).
- M2.4 cross-language SO-101 FK oracle (#16), with nine fixture cases, six independent Python
  reference tests and three Unreal Automation tests added to the 11-test baseline. Generator
  version 2 uses explicit left-to-right reductions so Python 3.11 and 3.12 produce identical
  fixture bytes. The oracle snapshot reports 121 Python tests. Final native validation passes the
  eight-test Kinematics selector on Linux and Win64; the earlier full 14-test reports remain
  contextual evidence in the [platform summary](docs/m2/evidence/fk-oracle-platform-validation.json).
- bounded M2.5 generic rigid-link actor (#17) with explicit Confirmed/Arrival/Target semantic
  layers, deterministic invalid-input preservation, reusable flat link topology, and debug
  primitives without robot mesh assets. Five actor Automation tests pass in each platform
  report (19 full Linux tests and 22 full Win64 tests); the committed 1920x1080 PNG is a
  synthetic visual demonstration and is not FK proof, measured telemetry, an operational UI,
  or VR authoring evidence.

### Status

- M1.8 remains in progress after M1.8b: durable autonomy-budget accounting and stable effect
  identity across contract revisions are not implemented, and no physical hardware validation is
  claimed.
- M2.4 is complete, including the cross-version reference check and native Linux/Win64 Kinematics
  validation.
- M2.2 is complete. Its raw articulated feed does not validate model geometry; an FK consumer must
  call the explicit description-backed validator. Root's post-rebase integrated validation passes
  141 Python tests; the historical M2.2 135/20 snapshot remains identified in its platform record.
- M2.5 is complete for the bounded kinematic-actor slice; M2 remains open for Jacobian, IK,
  `KinematicPreview`, and VR authoring.

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
