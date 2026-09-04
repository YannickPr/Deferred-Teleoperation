# M2.8a — local kinematic preview

M2.8a provides a small, deterministic `KinematicPreview` math core for future
desktop authoring. `BuildPreview` consumes a validated robot description, one explicit
articulated start state, and an already computed `FDttIKResult`. It returns
named joint samples and a canonical tool pose produced by forward kinematics at
each sample.

The API is available through `DeferredTeleop::Kinematics::BuildPreview` and the
`UDeferredTeleopKinematicPreviewLibrary` Blueprint function library. It does not
read a world, actor, WebSocket, clock, physics scene, VR controller, or
hardware, and it does not execute a command. The result is a local candidate for
presentation; it is not a motion plan, collision check, safety proof, or
admission decision.

## Inputs and validation

`PreviewId` and `GoalId` must be non-zero `FGuid` values. The supplied model
reference must match the description and the IK result. Its description hash is
required to have the structural form `sha256:` followed by 64 lowercase hex
digits. The function checks this shape only; it does not read description bytes
or authenticate that hash.

The source reference carries a message id, correlation id, frame id,
calibration version, and an `FDeferredTeleopEvidence` value. Evidence must have
at least one source id, valid ordered dates, and a positive world revision.
`Measured`, `Fused`, `Synthetic`, and `OperatorAsserted` map explicitly to
`Measured`, `Fused`, `Simulated`, and `OperatorAsserted` evidence provenance.
The mapping records provenance and does not prove origin.

The robot description is passed through `ValidateRobotDescription`. The root
transform must be finite, rigid, and canonical (right-handed, Z-up, metres and
radians). Start and IK states must contain exactly one finite value for every
revolute description joint, with no unknown, fixed, or duplicate name and no
limit violation. The output uses description order. Active names from the IK
result must be distinct revolute joints. For an inactive revolute, the supplied
IK value must equal the start value exactly; otherwise `BuildPreview` rejects
the request before interpolation. The preview goal is exactly the start value,
which also prevents an inactive gripper from changing.

Only a converged IK result with `bSuccess=true` is accepted by default. A
`Partial` or `IterationLimit` result is accepted only when
`Settings.bAcceptPartial` is true and its `bSuccess` flag is false. Other
statuses and incoherent status/success pairs are rejected. Residuals must be
finite and non-negative, the tool frame must be known, and the achieved tool
transform must be finite and rigid.

Each revolute joint has one finite, strictly positive maximum speed in rad/s.
The sample rate is in `(0, 1000]` Hz, the duration bound is in `(0, 30]` s, and
the sample cap is in `[2, 128]`.

## Timing and poses

For each revolute joint, the duration is computed in radians:

```text
T = max(abs(goal_i - start_i) / velocity_i)
```

`T` must be finite and no greater than the configured maximum; the builder does
not silently slow or clamp it. If `T` is zero, exactly one sample at `t=0` is
returned. Otherwise the requested count is:

```text
N = min(MaximumSamples, max(2, ceil(T * SampleRateHz) + 1))
t_k = T * k / (N - 1)
q_i(t_k) = start_i + (goal_i - start_i) * t_k / T
```

The first and last times and joint values are assigned directly from the start
and goal snapshots, preserving exact endpoints. Intermediate values are checked
against joint limits. Every sample calls canonical FK with the supplied root
and IK tool frame; tool poses are never interpolated.

Before sampling, FK is recomputed for the final preview goal and compared with
`IKResult.AchievedToolTransform`. Position error must be at most `1e-9` metres
and rotation error at most `1e-9` radians. This catches an incompatible root or
tool frame and a forged or stale achieved transform before output is committed.

The output is assembled in a temporary value and assigned only after all checks
and samples succeed. Each call first resets the output to its default invalid
state and clears its error string, so a caller can retain its last valid value
transactionally outside this pure function.

## Verification

The eight automation tests under `DeferredTeleop.M2.KinematicPreview` cover:

1. converged identity/reference/source/root snapshots, exact endpoints, and FK
   tool poses;
2. multi-joint maximum travel time, radians, linear joint interpolation, and
   inactive gripper preservation;
3. the zero-duration one-sample path;
4. the 128-sample cap and exact endpoint preservation;
5. missing, duplicate, zero, and non-finite speeds plus rate, duration, and cap
   bounds;
6. forged achieved poses, wrong root/tool, model identity, revision, and hash
   shape;
7. all four provenance mappings, source/frame/calibration identifiers, evidence
   dates, and GUID validation;
8. unknown, fixed, duplicate, non-finite, and out-of-limit joints, inactive
   gripper handling, and accepted/rejected IK statuses.

Linux and Win64 Unreal Engine 5.8.2 validation each report 43/43 contextual
successes (35 existing M2 successes plus the eight preview oracles), with build
and headless-editor exit code 0 and no warnings, failures, or not-run tests in
process. The machine-readable [platform record](evidence/kinematic-preview-platform-validation.json)
binds the selected source hashes and both report hashes. The eight tests call
the production `DeferredTeleop::Kinematics::BuildPreview` function.

This document covers the bounded #20 support in M2.8a only. It does not claim
desktop or VR authoring, trajectory visualization, or closure of #20/#21; those
integration slices remain open.
