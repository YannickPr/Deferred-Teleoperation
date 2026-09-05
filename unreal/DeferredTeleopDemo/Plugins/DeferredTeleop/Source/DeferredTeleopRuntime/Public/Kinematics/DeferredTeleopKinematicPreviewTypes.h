#pragma once

#include "CoreMinimal.h"
#include "Articulated/DeferredTeleopArticulatedViewTypes.h"
#include "Kinematics/DeferredTeleopIKTypes.h"
#include "DeferredTeleopKinematicPreviewTypes.generated.h"

/** Provenance explicitly attached to a locally generated kinematic preview. */
UENUM(BlueprintType)
enum class EDttPreviewSourceKind : uint8
{
    Measured,
    Fused,
    Synthetic,
    OperatorAsserted,
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttPreviewSourceReference
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FString SourceMessageId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FString CorrelationId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    EDttPreviewSourceKind SourceKind = EDttPreviewSourceKind::Measured;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDeferredTeleopEvidence Evidence;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FString FrameId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FString CalibrationVersion;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttPreviewJointVelocity
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FName JointName = NAME_None;

    /** Presentation timing limit in radians per second. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    double MaximumRadiansPerSecond = 0.0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttKinematicPreviewSettings
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDttPreviewJointVelocity> JointVelocities;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    double SampleRateHz = 60.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    double MaximumDurationSeconds = 30.0;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    int32 MaximumSamples = 128;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    bool bAcceptPartial = false;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttKinematicPreviewRequest
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FGuid PreviewId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FGuid GoalId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDeferredTeleopRobotModelReference ModelReference;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttCanonicalTransform WorldTransformOfRoot;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDeferredTeleopArticulatedJointPosition> StartJointPositions;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttIKResult IKResult;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttPreviewSourceReference SourceReference;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttKinematicPreviewSettings Settings;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttKinematicPreviewSample
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    double TimeSeconds = 0.0;

    /** Joint values are in description order and in radians. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDeferredTeleopArticulatedJointPosition> JointPositions;

    /** Canonical ^world T_tool produced by FK for this sample. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttCanonicalTransform ToolTransform;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttKinematicPreview
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    bool bValid = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FGuid PreviewId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FGuid GoalId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDeferredTeleopRobotModelReference ModelReference;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttPreviewSourceReference SourceReference;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FDttCanonicalTransform WorldTransformOfRoot;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDeferredTeleopArticulatedJointPosition> StartJointPositions;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDeferredTeleopArticulatedJointPosition> GoalJointPositions;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FName ToolFrameName = NAME_None;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    EDttIKStatus IKStatus = EDttIKStatus::InvalidInput;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    bool bAcceptedPartial = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    double PositionResidualMetres = 0.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    double ApproachResidualRadians = 0.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    double DurationSeconds = 0.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FDttKinematicPreviewSample> Samples;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    FString Diagnostic;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Kinematics|Preview")
    TArray<FString> Diagnostics;
};
