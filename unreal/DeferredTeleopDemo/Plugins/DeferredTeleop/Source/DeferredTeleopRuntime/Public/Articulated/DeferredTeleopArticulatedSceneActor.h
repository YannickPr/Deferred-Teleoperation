#pragma once

#include "GameFramework/Actor.h"
#include "Articulated/DeferredTeleopArticulatedSceneTypes.h"
#include "Articulated/DeferredTeleopArticulatedViewTypes.h"
#include "DeferredTeleopArticulatedSceneActor.generated.h"

class ADeferredTeleopKinematicRobotActor;
class UDeferredTeleopMissionClientComponent;

#if WITH_DEV_AUTOMATION_TESTS
struct FDeferredTeleopArticulatedSceneTestAccess;
#endif

namespace DeferredTeleop::ArticulatedScene
{
struct FPreparedLayerState;
}

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FDeferredTeleopArticulatedSceneStatusUpdated,
    const FDttArticulatedSceneStatus&,
    SceneStatus);

/**
 * Opt-in presentation controller for exactly three persistent kinematic
 * actors: Confirmed, Arrival, and Target.
 *
 * The controller derives no robot model from the wire.  It consumes the
 * articulated Mission view, validates each layer against one explicitly
 * configured local catalogue entry, and then performs a transactional
 * InitializeModel + ApplyState on the referenced actors.
 */
UCLASS(Blueprintable)
class DEFERREDTELEOPRUNTIME_API ADeferredTeleopArticulatedSceneActor : public AActor
{
    GENERATED_BODY()

public:
    ADeferredTeleopArticulatedSceneActor();

    /** The only Mission client consumed by this opt-in scene. */
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    TObjectPtr<UDeferredTeleopMissionClientComponent> MissionClient;

    /** Persistent actor references supplied by the level or editor recipe. */
    UPROPERTY(EditInstanceOnly, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    TObjectPtr<ADeferredTeleopKinematicRobotActor> ConfirmedActor;

    UPROPERTY(EditInstanceOnly, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    TObjectPtr<ADeferredTeleopKinematicRobotActor> ArrivalActor;

    UPROPERTY(EditInstanceOnly, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    TObjectPtr<ADeferredTeleopKinematicRobotActor> TargetActor;

    /** One explicit local catalogue binding shared by the three layers. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Deferred Teleoperation|Articulated Scene")
    FDeferredTeleopArticulatedModelBinding ModelBinding;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus ConfirmedStatus;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus ArrivalStatus;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedLayerStatus TargetStatus;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedSceneStatus SceneStatus;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bSceneReady = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    bool bHasCurrentView = false;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FDeferredTeleopArticulatedViewState LastViewState;

    UPROPERTY(BlueprintReadOnly, Category = "Deferred Teleoperation|Articulated Scene")
    FString LastError;

    UPROPERTY(BlueprintAssignable, Category = "Deferred Teleoperation|Articulated Scene")
    FDeferredTeleopArticulatedSceneStatusUpdated OnSceneStatusUpdated;

    /** Read and validate the local description, committing it only on success. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Articulated Scene")
    bool ConfigureBinding(
        const FDeferredTeleopArticulatedModelBinding& RequestedBinding,
        FString& OutError);

    /** Explicitly reread the configured path and replace the local catalogue. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Articulated Scene")
    bool ReloadLocalDescription(FString& OutError);

    /** Apply one already parsed articulated Mission view transactionally. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Articulated Scene")
    bool ApplyArticulatedViewState(
        const FDeferredTeleopArticulatedViewState& ViewState,
        FString& OutError);

    /** Parse one strict articulated fixture/message and apply it transactionally. */
    UFUNCTION(BlueprintCallable, Category = "Deferred Teleoperation|Articulated Scene")
    bool ApplyArticulatedViewJson(
        const FString& Json,
        FString& OutError);

    UFUNCTION(BlueprintPure, Category = "Deferred Teleoperation|Articulated Scene")
    FDttArticulatedSceneStatus GetSceneStatus() const;

#if WITH_DEV_AUTOMATION_TESTS
    /** Minimal fault injection used only by the rollback automation test. */
    void SetTestFailNextApply(bool bShouldFail);
#endif

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    virtual void Tick(float DeltaSeconds) override;

private:
#if WITH_DEV_AUTOMATION_TESTS
    friend struct FDeferredTeleopArticulatedSceneTestAccess;
#endif
    struct FLayerPoseCache
    {
        bool bHasPose = false;
        FDttRobotDescription Description;
        FDttCanonicalTransform RootTransform;
        TArray<FDttNamedJointPosition> OrderedJointPositions;
        FDeferredTeleopEvidence Evidence;
        FString ModelKey;
        double ReceiptMonotonicSeconds = 0.0;
        bool bHasFreshUntil = false;
        FDateTime FreshUntil;
        FString EvidenceModelVersion;
        bool bHasEvidenceModelVersion = false;
        bool bHasPredictedFor = false;
        FDateTime PredictedFor;
        bool bHasEstimatedIntentArrival = false;
        FDateTime EstimatedIntentArrivalAt;
        double LinkOneWayDelaySeconds = 0.0;
    };

    struct FLayerTemporalMetadata
    {
        bool bHasPredictedFor = false;
        FDateTime PredictedFor;
        bool bHasEstimatedIntentArrival = false;
        FDateTime EstimatedIntentArrivalAt;
        double LinkOneWayDelaySeconds = 0.0;
    };

    FLayerPoseCache ConfirmedCache;
    FLayerPoseCache ArrivalCache;
    FLayerPoseCache TargetCache;
    /** Last atomically committed binding, including its catalogue payload. */
    FDeferredTeleopArticulatedModelBinding CommittedBinding;
    bool bHasCommittedBinding = false;
    double LastViewReceiptMonotonicSeconds = 0.0;
    bool bBindingConfigured = false;
    bool bReferencesLatched = false;
    TWeakObjectPtr<ADeferredTeleopKinematicRobotActor> LatchedConfirmedActor;
    TWeakObjectPtr<ADeferredTeleopKinematicRobotActor> LatchedArrivalActor;
    TWeakObjectPtr<ADeferredTeleopKinematicRobotActor> LatchedTargetActor;

#if WITH_DEV_AUTOMATION_TESTS
    bool bTestFailNextApply = false;
#endif

    bool EnsureSceneReady(FString& OutError);
    bool ActorReferencesAreValid(FString& OutError) const;
    void SetCriticalStatus(FString Reason, const FString& Diagnostic);
    void MarkModelInvalid(const FString& Diagnostic);
    void ResetCachedLayersForBindingChange();
    void UpdateReceiptAges();
    void PublishStatus();
    void MarkMessageRejected(const FString& Reason);
    void MarkDisconnected();
    void MarkUnavailable(FDttArticulatedLayerStatus& Status, ADeferredTeleopKinematicRobotActor* Actor);
    void MarkInvalidLayer(
        FDttArticulatedLayerStatus& Status,
        ADeferredTeleopKinematicRobotActor* Actor,
        const FLayerPoseCache& Cache,
        const FString& Reason,
        const TArray<FString>& Diagnostics);
    void CopyCacheToStatus(
        FDttArticulatedLayerStatus& Status,
        const FLayerPoseCache& Cache,
        ADeferredTeleopKinematicRobotActor* Actor);
    static FLayerTemporalMetadata MakeLayerTemporalMetadata(
        const FDeferredTeleopArticulatedArrivalRobotState* ArrivalState);
    bool ProcessLayer(
        const FDeferredTeleopArticulatedRobotState* RobotState,
        bool bIsConfirmed,
        bool bIsArrival,
        const FLayerTemporalMetadata& TemporalMetadata,
        ADeferredTeleopKinematicRobotActor* Actor,
        FLayerPoseCache& Cache,
        FDttArticulatedLayerStatus& Status,
        FString& OutError);
    bool ApplyPreparedLayer(
        const DeferredTeleop::ArticulatedScene::FPreparedLayerState& Prepared,
        bool bIsConfirmed,
        const FLayerTemporalMetadata& TemporalMetadata,
        ADeferredTeleopKinematicRobotActor* Actor,
        FLayerPoseCache& Cache,
        FDttArticulatedLayerStatus& Status,
        FString& OutError);
    bool RestoreCachedLayer(
        ADeferredTeleopKinematicRobotActor* Actor,
        const FLayerPoseCache& Cache,
        FString& OutError);

    UFUNCTION()
    void HandleArticulatedViewStateUpdated(
        const FDeferredTeleopArticulatedViewState& ViewState);

    UFUNCTION()
    void HandleMissionConnectionChanged(EDeferredTeleopConnectionState ConnectionState);

    UFUNCTION()
    void HandleMissionMessageRejected(const FString& Reason);
};
