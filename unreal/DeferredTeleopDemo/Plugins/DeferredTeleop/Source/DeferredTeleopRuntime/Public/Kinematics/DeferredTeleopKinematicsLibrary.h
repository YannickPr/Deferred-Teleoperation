#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "RobotModel/DeferredTeleopRobotModelTypes.h"
#include "DeferredTeleopKinematicsLibrary.generated.h"

/**
 * Runtime lookup/index data produced from a validated robot description.
 *
 * The maps are used only at the semantic boundary.  FK traverses the stable
 * arrays below, so TMap iteration order can never affect a result.
 */
struct DEFERREDTELEOPRUNTIME_API FDttValidatedRobotModel
{
    TMap<FName, int32> LinkIndexByName;
    TMap<FName, int32> JointIndexByName;
    TMap<FName, int32> ToolIndexByName;
    TArray<int32> ParentJointByLink;
    TArray<int32> LinkTraversalOrder;
    TArray<int32> JointTraversalOrder;

    void Reset();
    int32 FindLinkIndex(FName LinkName) const;
    int32 FindJointIndex(FName JointName) const;
    int32 FindToolIndex(FName ToolName) const;
};

namespace DeferredTeleop::Kinematics
{
/** Validate names, transforms, axes, and the rooted fixed/revolute tree. */
DEFERREDTELEOPRUNTIME_API bool ValidateRobotDescription(
    const FDttRobotDescription& Description,
    FDttValidatedRobotModel& OutModel,
    FString& OutError);

/**
 * Evaluate generic tree FK from named revolute positions.
 *
 * Fixed joints consume no state entry.  Every revolute joint must have one
 * finite named state entry.  Limit violations are reported as diagnostics and
 * never clamped; malformed state or model data returns false.
 */
DEFERREDTELEOPRUNTIME_API bool EvaluateForwardKinematics(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    const TArray<FDttNamedJointPosition>& JointPositions,
    FDttForwardKinematicsResult& OutResult);

/** Convert the canonical RH/Z-up/metres transform at the single Unreal boundary. */
DEFERREDTELEOPRUNTIME_API bool ConvertCanonicalToUnrealTransform(
    const FDttCanonicalTransform& CanonicalTransform,
    FTransform& OutUnrealTransform,
    FString& OutError);

/** Inverse boundary conversion used by authoring/tests; it does not accept scale. */
DEFERREDTELEOPRUNTIME_API bool ConvertUnrealToCanonicalTransform(
    const FTransform& UnrealTransform,
    FDttCanonicalTransform& OutCanonicalTransform,
    FString& OutError);
} // namespace DeferredTeleop::Kinematics

/** Minimal Blueprint boundary for model validation, FK, and frame conversion. */
UCLASS()
class DEFERREDTELEOPRUNTIME_API UDeferredTeleopKinematicsLibrary final
    : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Robot Model")
    static bool ParseRobotDescriptionJson(
        const FString& Json,
        FDttRobotDescription& OutDescription,
        FString& OutError);

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematics")
    static bool ValidateRobotDescription(
        const FDttRobotDescription& Description,
        FString& OutError);

    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Kinematics")
    static bool EvaluateForwardKinematics(
        const FDttRobotDescription& Description,
        const FDttCanonicalTransform& WorldTransformOfRoot,
        const TArray<FDttNamedJointPosition>& JointPositions,
        FDttForwardKinematicsResult& OutResult);

    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Kinematics")
    static bool ConvertCanonicalToUnrealTransform(
        const FDttCanonicalTransform& CanonicalTransform,
        FTransform& OutUnrealTransform,
        FString& OutError);

    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Kinematics")
    static bool ConvertUnrealToCanonicalTransform(
        const FTransform& UnrealTransform,
        FDttCanonicalTransform& OutCanonicalTransform,
        FString& OutError);
};
