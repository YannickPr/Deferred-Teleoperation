#pragma once

#include "CoreMinimal.h"
#include "RobotModel/DeferredTeleopRobotModelTypes.h"
#include "DeferredTeleopIKTypes.generated.h"

/** The bounded task exposed by the M2 constrained IK solver. */
UENUM(BlueprintType)
enum class EDttIKMode : uint8
{
    PositionOnly,
    PositionPlusApproachAxis,
};

/** A solver outcome.  Unreachable is reserved for a future global proof. */
UENUM(BlueprintType)
enum class EDttIKStatus : uint8
{
    Converged,
    Partial,
    IterationLimit,
    InvalidInput,
    NumericalFailure,
    Unreachable,
};

/** One or both structural limits constrain a returned joint value. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttIKActiveLimit
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FName JointName = NAME_None;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    bool bAtLowerLimit = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    bool bAtUpperLimit = false;
};

/** Numeric policy for deterministic, bounded damped-least-squares IK. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttIKSettings
{
    GENERATED_BODY()

    /** Position rows are expressed in metres and use this task weight. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double PositionWeight = 1.0;

    /** Approach-axis rows are expressed in radians and use this task weight. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double OrientationWeight = 0.10;

    /** Initial DLS damping lambda. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double DampingLambda = 0.02;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double MinimumDampingLambda = 0.001;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double MaximumDampingLambda = 1.0;

    /** Maximum absolute change of one joint in one accepted iteration. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double MaxJointStepRadians = 0.12;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double PositionToleranceMetres = 1.0e-3;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double ApproachToleranceRadians = 0.034906585039886591538473815369;

    /** Central-difference h.  The M2 contract fixes the default at 1e-5 rad. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double CentralDifferenceStepRadians = 1.0e-5;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 MaxIterations = 64;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 MaxFKEvaluations = 1024;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 MaxLineSearchCandidates = 5;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 MaxDampingTrials = 4;

    /** A strictly smaller weighted cost is required to accept an iteration. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    double StagnationCostTolerance = 1.0e-14;
};

/** Complete canonical input to one deterministic IK solve. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttIKRequest
{
    GENERATED_BODY()

    /** Semantic group whose revolute joints may move. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FName JointGroupName = NAME_None;

    /** Explicit validated tool frame; no implicit last-link selection is made. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FName ToolFrameName = NAME_None;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    EDttIKMode Mode = EDttIKMode::PositionOnly;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FDttCanonicalVector TargetPositionMetres;

    /** Used only by PositionPlusApproachAxis. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FDttCanonicalVector TargetApproachDirectionCanonical;

    /** Tool-local axis used only by PositionPlusApproachAxis. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FDttCanonicalVector LocalToolApproachAxis;

    /** Canonical ^world T_root, supplied explicitly for every solve. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    FDttCanonicalTransform WorldTransformOfRoot;

    /** Exactly one finite named value is required for every revolute joint. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FDttNamedJointPosition> SeedJointPositions;
};

/** Full, inspectable result of one bounded solve. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttIKResult
{
    GENERATED_BODY()

    /** True only when Status is Converged; partial results remain inspectable. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    bool bSuccess = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    EDttIKStatus Status = EDttIKStatus::InvalidInput;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FString ModelId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FString ModelRevision;

    /** Explicit tool frame used to produce the achieved transform. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FName ToolFrameName = NAME_None;

    /** Every revolute joint, in description-array order, including inactive joints. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FDttNamedJointPosition> JointPositions;

    /** Active group order used for Jacobian columns and diagnostics. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FName> ActiveJointNames;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FDttIKActiveLimit> ActiveLimits;

    /** Convenience name-only view of ActiveLimits for Blueprint callers. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FName> ActiveJointLimits;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    double PositionResidualMetres = 0.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    double ApproachResidualRadians = 0.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 Iterations = 0;

    /** Number of full FK calls, including the initial, central and candidates. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    int32 FKEvaluations = 0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FDttCanonicalTransform AchievedToolTransform;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    FString Diagnostic;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|IK")
    TArray<FString> Diagnostics;
};
