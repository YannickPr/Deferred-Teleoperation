#pragma once

#include "Articulated/DeferredTeleopArticulatedSceneTypes.h"

namespace DeferredTeleop::ArticulatedScene
{

/** Which contract branch rejected a candidate before actor mutation. */
enum class ELayerValidationFailure : uint8
{
    None,
    InvalidModel,
    InvalidLayer,
};

/** A fully prevalidated candidate.  No actor is touched while making one. */
struct FPreparedLayerState
{
    FDttCanonicalTransform RootTransform;
    TArray<FDttNamedJointPosition> OrderedJointPositions;
    FDttForwardKinematicsResult ForwardKinematics;
    FDeferredTeleopEvidence Evidence;
    FDeferredTeleopRobotModelReference ModelReference;
    FString ModelKey;
    bool bWithinJointLimits = true;
    ELayerValidationFailure Failure = ELayerValidationFailure::None;
};

/** Hash exact bytes as lowercase sha256:<64 hex digits>. */
DEFERREDTELEOPRUNTIME_API bool ComputeDescriptionHash(
    const TArray<uint8>& DescriptionBytes,
    FString& OutHash,
    FString& OutError);

/** Explicitly load, validate, and commit a local catalogue binding. */
DEFERREDTELEOPRUNTIME_API bool ConfigureBinding(
    const FDeferredTeleopArticulatedModelBinding& RequestedBinding,
    FDeferredTeleopArticulatedModelBinding& OutBinding,
    FString& OutError);

/** Explicitly reload the path in an already configured binding. */
DEFERREDTELEOPRUNTIME_API bool ReloadLocalDescription(
    FDeferredTeleopArticulatedModelBinding& InOutBinding,
    FString& OutError);

/** Convert a wire root pose without normalizing or otherwise changing it. */
DEFERREDTELEOPRUNTIME_API bool ConvertRootPose(
    const FDeferredTeleopPose& Pose,
    FDttCanonicalTransform& OutRootTransform,
    FString& OutError);

/** Stable display key for the identity tuple in a local catalogue. */
DEFERREDTELEOPRUNTIME_API FString MakeModelKey(
    const FDeferredTeleopRobotModelReference& ModelReference);

/**
 * Validate a layer against the cached local catalogue and precompute FK and
 * every Unreal conversion before an actor is initialized or updated.
 */
DEFERREDTELEOPRUNTIME_API bool PrepareLayerState(
    const FDeferredTeleopArticulatedModelBinding& Binding,
    const FDeferredTeleopArticulatedRobotState& RobotState,
    FPreparedLayerState& OutPrepared,
    FString& OutError);

} // namespace DeferredTeleop::ArticulatedScene
