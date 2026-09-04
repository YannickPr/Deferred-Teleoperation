# M2.4 cross-language SO-101 kinematics fixtures (#16)

M2.4 is complete for the fixed/revolute forward-kinematics numerical oracle.
The committed fixture, independent Python reference, and Unreal Automation
checks are defined and validated across Python 3.11/3.12 and native Linux and
Win64 runs.  This document describes the contract and the reproducible gate;
it does not extend the scope to articulated-state protocol work, inverse
kinematics, or hardware.

`fixtures/m2/kinematics/so101-fk.json` is the reviewable contract between the
Python reference and the Unreal FK implementation.  It is generated from the
committed `robots/so101/generated/so101.kinematics.json` description.  Every
case contains a named root pose, named revolute joint positions, and expected
homogeneous 4×4 matrices for every link and tool frame.  The matrix convention
is right-handed, Z-up, metres, radians, column vectors, and
`^A T_C = ^A T_B * ^B T_C`.

The fixture records the model identifier, model revision, generated-description
SHA-256, and upstream source blob SHA-1.  It also records the reference
generator version and source SHA-256.  These bindings make a changed model or
generator fail the drift check instead of silently changing the expected data.
Generator version 2 uses explicit left-to-right addition for every reduction
in the reference evaluator.  This keeps the serialized bytes stable across
Python 3.11 and 3.12, whose built-in `sum` behavior differs
([Python 3.12 change](https://docs.python.org/3.12/whatsnew/3.12.html#other-language-changes)).
The version is part of the fixture metadata and must be bumped when the
reference arithmetic changes.

The fixture contains nine SO-101 cases: `zero`, `shoulder_pan_only`,
`shoulder_and_elbow`, `multi_joint_nonsymmetric`, `joint_limits_lower`,
`joint_limits_upper`, `tool_fixed`, `root_transform_noncommuting`, and
`reordered_joint_positions`.  They cover the zero pose, single- and
multi-joint motion, both limit edges, a non-commuting root pose, fixed-tool
propagation, and named-position reordering.  The tool frame is checked with
every case.  The six Python reference tests additionally assert the known
analytical +90° Z rotation, metadata binding, named-position semantics, and
explicit rejection of unknown, duplicate, missing, or drifted inputs.  Unreal
Automation Tests run the same nine cases through the production parser and FK
API and compare matrix entries by name.

The two tolerances are intentionally separate: `position_m` is the absolute
translation budget in metres and `rotation` is the absolute budget for the
unitless 3×3 rotation entries.  They are both `1e-9`, below the precision used
by this M2 fixture while allowing independent double-precision implementations
to round at different steps.

The Python `--check` is a mandatory gate before any Unreal fixture test is run.
It checks the model and generator hashes as well as the complete serialized
fixture.  Use the cross-platform build and Automation recipe in
[Canonical kinematics in Unreal](CANONICAL_KINEMATICS.md); that guide is the
single source for the Linux and Win64 command lines, including the explicit
Windows `ProcessStartInfo` quoting required for `-ExecCmds`.  Do not run the
Unreal queue when `--check` fails.  Keep both Python 3.11 and 3.12 in CI so
that this portability property remains checked.

The Automation Tests require an intact monorepo checkout: they resolve both the
fixture and the generated robot description through `ProjectDir()/../../`.
An installed or packaged plugin alone does not contain those repository test
inputs and is not a valid fixture-test environment.

The `FrameConversionContract` Automation Test is a separate boundary contract
for the same canonical convention.  It checks the origin and the three metre
unit vectors, then checks all three basis-vector images for ±90° around each
axis under `S R S`, where `S = diag(1,-1,1)`.  It also checks unit norms,
pairwise orthogonality, determinant `+1`, one-time metre-to-centimetre scaling,
and a non-commuting rotation/translation example with a non-zero point.  The
round-trip assertion compares `abs(dot(q_in,q_out))` against `cos(angle/2)`;
this accounts for the q/−q representation while avoiding an ill-conditioned
inverse cosine near zero error.  These checks use separate named budgets for
centimetres, metres, quaternion angle, orthonormality, quaternion norm, and
scale because those quantities have different units and numerical failure
modes.

Regenerate after an intentional model or reference change:

```bash
PYTHONPATH=python/src python -m deferred_teleop.robot_model.kinematics_reference
```

The default command writes the fixture.  CI and local verification use:

```bash
PYTHONPATH=python/src python -m deferred_teleop.robot_model.kinematics_reference --check
```

Run the six reference tests as an additional local check:

```bash
PYTHONPATH=python/src pytest -q tests/test_kinematics_reference.py
```

The Unreal run includes the 11-test M1/M2 baseline (four M1 and seven M2
tests) plus these three M2.4 tests:

- `DeferredTeleop.M2.Kinematics.CrossLanguageSO101Fixtures`
- `DeferredTeleop.M2.Kinematics.CrossLanguageFixtureInputValidation`
- `DeferredTeleop.M2.Kinematics.FrameConversionContract`

The final version-2 Automation run selects `DeferredTeleop.M2.Kinematics` and
reports 8 successes, zero failures, and zero tests not run on each target.  It
contains the five existing M2 kinematics tests plus the three M2.4 tests listed
above.  Linux was recorded on 4 September 2026 at 22:25:36 UTC with Unreal
Engine 5.8.2 from source commit
`ff8421f2b8cb4feb76fff57965a1effc53a6eb7b` and Clang 20.1.8; the headless
editor exited 0.  Win64 was recorded at 22:26:33 UTC with Unreal Engine 5.8.2,
MSVC `cl.exe` 19.50.35737.0, and Windows SDK 10.0.26100.0; its headless editor
also exited 0.  The rerun reused the existing successful runtime-module build;
no rebuild was required for the fixture-only update.  Earlier full 14-test
Linux/Win64 reports remain context in the
[M2.4 platform summary](evidence/fk-oracle-platform-validation.json).

The six-test Python reference suite reports `6 passed`, and the integrated
suite reports `121 passed`.  Python 3.11.15 and 3.12.3 generate byte-identical
fixtures, with every non-generator field unchanged from the previous validated
fixture.  The machine-readable [M2.4 platform summary](evidence/fk-oracle-platform-validation.json)
records this portability result and the final native counts.

The current v2 review snapshot pins the fixture
`fixtures/m2/kinematics/so101-fk.json` to SHA-256
`2fd05f90c8f0344d8687b06d11086d8ba4bc1e43b0fc248869b19369bfa8c3b8` and the
generator source to SHA-256
`983a1d298d4ec4ef4bb3a3f8d44df20107bbea21c9a5d624b1d7ef29b1438fa9`.
The C++ test inputs remain pinned to
`489c209cba968cf198750cf7228844092ad7c1d93613e1208f5c499f5248300b` for
`DeferredTeleopKinematicsFixtureTests.cpp` and
`9aa5cf021b6839fa54165630d9e3915d82d91c41ae1b359955f9832eb49e1fbc` for
`DeferredTeleopFrameConversionContractTests.cpp`.

`--check` compares the committed bytes with a fresh generation, so it fails for
fixture, generator, or model drift.  This oracle covers fixed/revolute forward
kinematics and frame composition only.  It does not claim inverse kinematics,
dynamics, physical metrology, collision, or rendered-artifact equivalence.
