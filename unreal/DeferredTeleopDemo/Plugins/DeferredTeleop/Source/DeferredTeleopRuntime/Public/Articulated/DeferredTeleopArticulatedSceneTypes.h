#pragma once

#include "CoreMinimal.h"
#include "Articulated/DeferredTeleopArticulatedViewTypes.h"
#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "DeferredTeleopArticulatedSceneTypes.generated.h"

/**
 * The local catalogue entry used by the opt-in articulated presentation.
 *
 * DescriptionBytes, Description and ValidatedModel are deliberately runtime
 * fields.  They are populated only by ConfigureBinding or
 * ReloadLocalDescription; a wire reference never causes a file read.
 */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDeferredTeleopArticulatedModelBinding
{
    GENERATED_BODY()

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    FString RobotId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    FString DescriptionFilePath;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    FString ExpectedFrameId;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    FString ExpectedCalibrationVersion;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString CachedDescriptionHash;

    UPROPERTY(Transient, BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString CachedModelKey;

    /** Exact bytes read from DescriptionFilePath on the last successful load. */
    TArray<uint8> DescriptionBytes;

    /** Parsed and validated model corresponding to DescriptionBytes. */
    FDttRobotDescription Description;
    FDttValidatedRobotModel ValidatedModel;
    bool bHasLoadedDescription = false;

    void ResetRuntime();
};

/** Public status for one of the three independent semantic layers. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttArticulatedLayerStatus
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bAvailable = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasLastGoodPose = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bVisible = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bDegraded = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bCritical = false;

    /** Stable machine-readable presentation reason, e.g. InvalidModel or STALE. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString Reason;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    EDeferredTeleopProvenance Provenance = EDeferredTeleopProvenance::Unknown;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    TArray<FString> SourceIds;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDateTime ObservedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDateTime ProducedAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    int32 WorldRevision = 0;

    /** Monotonic receipt age.  This is intentionally separate from ObservedAt. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    double ReceiptAgeSeconds = -1.0;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString ModelKey;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    TArray<FString> Diagnostics;

    /** Optional evidence freshness metadata, copied without re-timestamping. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasFreshUntil = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDateTime FreshUntil;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString EvidenceModelVersion;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasEvidenceModelVersion = false;

    /** Arrival-only forecast metadata; absent for Confirmed and Target. */
    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasPredictedFor = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDateTime PredictedFor;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasEstimatedIntentArrival = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDateTime EstimatedIntentArrivalAt;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    double LinkOneWayDelaySeconds = 0.0;
};

/** Snapshot of the three layer statuses for a Blueprint/UI consumer. */
USTRUCT(BlueprintType)
struct DEFERREDTELEOPRUNTIME_API FDttArticulatedSceneStatus
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus Confirmed;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus Arrival;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus Target;
};
