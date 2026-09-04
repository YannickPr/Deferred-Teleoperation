# Changelog

All notable public changes will be recorded here. The project follows semantic versioning only for
tagged releases; the experimental `dtt/0` protocol can still change incompatibly.

## Unreleased

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

## Planned: 0.1.0

The first runnable release will be dated and moved out of `Unreleased` only after every item in
`release/m1/release-checklist.json` passes. In particular, the current Windows Unreal result does
not substitute for the required Linux Unreal verification.
