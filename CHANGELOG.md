# Changelog

All notable public changes will be recorded here. The project follows semantic versioning only for
tagged releases; the experimental `dtt/0` protocol can still change incompatibly.

## Unreleased

### Fixed

- exact model-reference and protocol-literal comparisons in the M2 articulated view,
  robot-description parser, preview and actor topology reuse. Case-only mutations are covered
  by strengthened assertions in the existing 43-test Unreal suite, passing on Linux and Win64;
  native `FName` topology lookup semantics remain unchanged. See the
  [identity contract and evidence](docs/m2/IDENTITY_CASE_SENSITIVITY.md).

### Added

- experimental external-effect recovery with a separate persistent simulated button, durable
  device binding, observe-only recovery after uncertain dispatch, and immutable outcome evidence;
- explicit historical dummy-fixture compatibility; normal Field reconciliation requires compatible
  Robot telemetry instead of manufacturing a measured completion pose from a terminal event;
- combined M1.8b virtual-time proof that drives the independent non-idempotent device through
  symmetric 1200-second and asymmetric 900-second outbound / 1200-second return delays, including
  duplicate-contract recovery and receiving-site expiry boundaries;
- bounded M1.8c Robot-local external-action budget for one revision-1 reservation per operation,
  a finite service-clock window, atomic dispatch/device binding, durable pre-dispatch holds, and
  conservative v3-to-v4 legacy classification; cross-revision identity and multiprocess fencing
  remain open;
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
- bounded M2.7 constrained IK implementation (#19) with named generic joint groups and tool
  frames, PositionOnly and PositionPlusApproachAxis tasks, deterministic damped least squares,
  central finite-difference Jacobians, structural-limit projection, inspectable result diagnostics
  and a 13-test acceptance selector. Linux and Win64 each record 35 contextual successes (13 IK
  plus 22 contextual tests), with no warnings, failures or not-run tests in process and
  build/editor exit code 0. See the [M2.7 platform record](docs/m2/evidence/constrained-ik-platform-validation.json).
- bounded M2.8a local `KinematicPreview` math core related to #20: pure C++/Blueprint
  `BuildPreview`, deterministic time-sampled joint states, FK recomputation for every tool sample,
  exact endpoints, explicit inactive-joint rejection, opt-in partial IK, declared provenance and
  bounded preview timing. The eight-test selector passes inside 43-test contextual Linux and Win64
  Unreal Engine 5.8.2 reports with build/editor exit code 0. See the [preview guide](docs/m2/KINEMATIC_PREVIEW.md)
  and [platform record](docs/m2/evidence/kinematic-preview-platform-validation.json).
- bounded M2.9a opt-in articulated-scene tranche with persistent Confirmed, Arrival and Target
  kinematic actors, exact local description-byte hashing, strict provenance/model/FK validation,
  per-connection wire ordering, last-good transactional rollback, and an editor fixture replay
  labelled `SYNTHETIC FIXTURE REPLAY`. Its seven grouped production Automation tests are included
  in the final Linux and Win64 receipts: each platform records build/editor exit code 0 and 50
  tests (48 `Success` plus two expected `SuccessWithWarnings` for missing-model and
  duplicate-sequence negative cases), with zero failures. The final desktop image is a 1920x1080
  `RenderOffscreenVulkan` capture; JSON field-name exactness remains tracked by [issue #47](https://github.com/YannickPr/Deferred-Teleoperation/issues/47),
  and full M2.9/#20/#21 remain open.

### Status

- M1.8 remains in progress after the bounded M1.8c budget slice: stable effect identity across
  contract revisions and multiprocess fencing remain open, and no physical hardware validation is
  claimed.
- M2.4 is complete, including the cross-version reference check and native Linux/Win64 Kinematics
  validation.
- M2.2 is complete. Its raw articulated feed does not validate model geometry; an FK consumer must
  call the explicit description-backed validator. The integrated Python suite passes 152 tests;
  the historical M2.2 135/20 snapshot remains identified in its platform record.
- M2.5 is complete for the bounded kinematic-actor slice. M2.7 constrained IK and the M2.8a
  preview math core are complete with Linux/Win64 evidence. The bounded M2.9a articulated-scene
  tranche is complete with Linux/Win64 native evidence and a synthetic desktop capture; full M2.9,
  desktop/VR authoring and the #20/#21 integration gates remain open, and no physical or
  hardware-control path is claimed.

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
