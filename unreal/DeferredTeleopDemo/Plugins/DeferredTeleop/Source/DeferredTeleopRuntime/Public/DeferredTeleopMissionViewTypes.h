#pragma once

#include "CoreMinimal.h"
#include "DeferredTeleopMissionViewTypes.generated.h"

UENUM(BlueprintType)
enum class EDeferredTeleopConnectionState : uint8
{
    Disconnected,
    Connecting,
    Connected,
};

UENUM(BlueprintType)
enum class EDeferredTeleopProvenance : uint8
{
    Unknown,
    Measured,
    Fused,
    OperatorAsserted,
    Inferred,
    Predicted,
    Simulated,
};

UENUM(BlueprintType)
enum class EDeferredTeleopTrajectorySource : uint8
{
    ConfirmedState,
    ArrivalBelief,
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopPose
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FVector PositionMetres = FVector::ZeroVector;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FQuat Orientation = FQuat::Identity;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString FrameId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString CalibrationVersion;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopEvidence
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    TArray<FString> SourceIds;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime ObservedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime ProducedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    EDeferredTeleopProvenance Provenance = EDeferredTeleopProvenance::Unknown;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    int32 WorldRevision = 0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    bool bHasFreshUntil = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime FreshUntil;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString ModelVersion;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopConfirmedState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    bool bAvailable = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString SiteId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString RobotId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopPose Pose;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopEvidence Evidence;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArrivalBelief
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    bool bAvailable = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString RobotId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopPose Pose;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime PredictedFor;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    bool bHasEstimatedIntentArrival = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime EstimatedIntentArrivalAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    float LinkOneWayDelaySeconds = 0.0F;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopEvidence Evidence;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopTargetBranch
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    bool bAvailable = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString EntityId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString RequestedState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString Condition;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopPose Pose;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopEvidence Evidence;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopTimedTrajectorySample
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime SampleTime;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString TimestampBasis;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopPose Pose;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    EDeferredTeleopTrajectorySource Source = EDeferredTeleopTrajectorySource::ConfirmedState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    EDeferredTeleopProvenance Provenance = EDeferredTeleopProvenance::Unknown;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopPredictionManifestSummary
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString ManifestId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString SiteId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    TArray<FString> ForecastIds;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    int32 GeneratedForWorldRevision = 0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopEvidence Evidence;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopMissionStatus
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString OperationId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString CorrelationId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString TerminalState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString TerminalContractId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    int32 ReceivedMessageCount = 0;
};

USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopMissionViewState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString ProtocolVersion;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FString SourceId;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    int32 SourceSequence = 0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime ProducedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    EDeferredTeleopConnectionState MissionToField = EDeferredTeleopConnectionState::Disconnected;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDateTime MissionConnectionChangedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopConfirmedState ConfirmedState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopArrivalBelief ArrivalBelief;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopTargetBranch TargetBranch;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    TArray<FDeferredTeleopTimedTrajectorySample> TrajectoryForecasts;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    TArray<FDeferredTeleopPredictionManifestSummary> PredictionManifests;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation")
    FDeferredTeleopMissionStatus Status;
};
