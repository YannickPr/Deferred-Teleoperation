#pragma once

#include "CoreMinimal.h"
#include "DeferredTeleopMissionViewTypes.h"
#include "DeferredTeleopArticulatedViewTypes.generated.h"

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopRobotModelReference
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString ModelId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString ModelRevision;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString DescriptionHash;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedJointPosition
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString JointName;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    double PositionRadians = 0.0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedRobotState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString RobotId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopRobotModelReference ModelReference;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopPose RootPose;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    TArray<FDeferredTeleopArticulatedJointPosition> Joints;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopEvidence Evidence;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedArrivalRobotState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    bool bAvailable = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopArticulatedRobotState RobotState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDateTime PredictedFor;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    bool bHasEstimatedIntentArrival = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDateTime EstimatedIntentArrivalAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    double LinkOneWayDelaySeconds = 0.0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedConnectionStatus
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    EDeferredTeleopConnectionState MissionToField = EDeferredTeleopConnectionState::Disconnected;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDateTime ChangedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString Detail;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedViewState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString ProtocolVersion;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString MessageType;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FString SourceId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    int32 SourceSequence = 0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDateTime ProducedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopArticulatedConnectionStatus Connection;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopMissionStatus Status;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    bool bHasConfirmedRobotState = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopArticulatedRobotState ConfirmedRobotState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopArticulatedArrivalRobotState ArrivalRobotState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    bool bHasTargetRobotState = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated")
    FDeferredTeleopArticulatedRobotState TargetRobotState;
};

