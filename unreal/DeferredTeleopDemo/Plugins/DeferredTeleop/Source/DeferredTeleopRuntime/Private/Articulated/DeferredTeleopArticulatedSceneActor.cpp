#include "Articulated/DeferredTeleopArticulatedSceneActor.h"

#include "Articulated/DeferredTeleopArticulatedSceneValidation.h"
#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "DeferredTeleopMissionClientComponent.h"
#include "DeferredTeleopRuntime.h"
#include "HAL/PlatformTime.h"
#include "Visualization/DeferredTeleopKinematicRobotActor.h"

namespace DeferredTeleop::ArticulatedScene::Private
{

FString FailureLabel(const ELayerValidationFailure Failure)
{
    switch (Failure)
    {
    case ELayerValidationFailure::InvalidModel:
        return TEXT("InvalidModel");
    case ELayerValidationFailure::InvalidLayer:
        return TEXT("InvalidLayer");
    default:
        return TEXT("InvalidLayer");
    }
}

bool IsMeasuredProvenance(const EDeferredTeleopProvenance Provenance)
{
    return Provenance == EDeferredTeleopProvenance::Measured
        || Provenance == EDeferredTeleopProvenance::Fused;
}

constexpr double MaxArticulatedDelaySeconds = 86'400.0;

bool ValidateLayerRole(
    const FDeferredTeleopArticulatedRobotState& RobotState,
    const bool bIsConfirmed,
    const bool bIsArrival,
    const bool bHasPredictedFor,
    const FDateTime& PredictedFor,
    const bool bHasEstimatedIntentArrival,
    const FDateTime& EstimatedIntentArrivalAt,
    const double LinkOneWayDelaySeconds,
    FString& OutError)
{
    OutError.Reset();
    if (bIsConfirmed)
    {
        if (!IsMeasuredProvenance(RobotState.Evidence.Provenance))
        {
            OutError = TEXT("InvalidLayer: confirmed evidence must be MEASURED or FUSED");
            return false;
        }
        return true;
    }

    if (!bIsArrival)
    {
        if (RobotState.Evidence.Provenance != EDeferredTeleopProvenance::OperatorAsserted)
        {
            OutError = TEXT("InvalidLayer: target evidence must be OPERATOR_ASSERTED");
            return false;
        }
        return true;
    }

    if (RobotState.Evidence.Provenance != EDeferredTeleopProvenance::Predicted)
    {
        OutError = TEXT("InvalidLayer: arrival evidence must be PREDICTED");
        return false;
    }
    if (!bHasPredictedFor
        || PredictedFor.GetTicks() == 0
        || PredictedFor <= RobotState.Evidence.ProducedAt)
    {
        OutError = TEXT("InvalidLayer: arrival predicted_for must be after evidence produced_at");
        return false;
    }
    if (!FMath::IsFinite(LinkOneWayDelaySeconds)
        || LinkOneWayDelaySeconds < 0.0
        || LinkOneWayDelaySeconds > MaxArticulatedDelaySeconds)
    {
        OutError = TEXT("InvalidLayer: arrival one-way delay must be finite in [0, 86400] seconds");
        return false;
    }
    if (bHasEstimatedIntentArrival && EstimatedIntentArrivalAt.GetTicks() == 0)
    {
        OutError = TEXT("InvalidLayer: estimated intent arrival must be a valid timestamp");
        return false;
    }
    return true;
}

} // namespace DeferredTeleop::ArticulatedScene::Private

ADeferredTeleopArticulatedSceneActor::ADeferredTeleopArticulatedSceneActor()
{
    PrimaryActorTick.bCanEverTick = true;
    MissionClient = CreateDefaultSubobject<UDeferredTeleopMissionClientComponent>(
        TEXT("ArticulatedMissionClient"));
    MissionClient->WireMode = EDeferredTeleopMissionWireMode::ArticulatedView;
}

void ADeferredTeleopArticulatedSceneActor::BeginPlay()
{
    Super::BeginPlay();

    if (MissionClient != nullptr)
    {
        // This actor is the explicit opt-in consumer.  The standalone Mission
        // component remains LegacyView by default for M1 compatibility.
        MissionClient->WireMode = EDeferredTeleopMissionWireMode::ArticulatedView;
        MissionClient->OnArticulatedViewStateUpdated.AddDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleArticulatedViewStateUpdated);
        MissionClient->OnMissionConnectionChanged.AddDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleMissionConnectionChanged);
        MissionClient->OnMissionMessageRejected.AddDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleMissionMessageRejected);
    }

    FString Error;
    if (!ActorReferencesAreValid(Error))
    {
        SetCriticalStatus(TEXT("CriticalMissingActorReferences"), Error);
        return;
    }
    LatchedConfirmedActor = ConfirmedActor;
    LatchedArrivalActor = ArrivalActor;
    LatchedTargetActor = TargetActor;
    bReferencesLatched = true;

    if (!bBindingConfigured && !ModelBinding.DescriptionFilePath.TrimStartAndEnd().IsEmpty())
    {
        FDeferredTeleopArticulatedModelBinding Requested = ModelBinding;
        if (!ConfigureBinding(Requested, Error))
        {
            UE_LOG(
                LogDeferredTeleop,
                Warning,
                TEXT("Articulated scene local catalogue is unavailable: %s"),
                *Error);
        }
    }

    if (!bBindingConfigured)
    {
        const FString Diagnostic =
            TEXT("ConfigureBinding or ReloadLocalDescription must succeed before applying a view");
        bSceneReady = false;
        MarkModelInvalid(Diagnostic);
        return;
    }

    bSceneReady = true;
}

void ADeferredTeleopArticulatedSceneActor::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    if (MissionClient != nullptr)
    {
        MissionClient->OnArticulatedViewStateUpdated.RemoveDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleArticulatedViewStateUpdated);
        MissionClient->OnMissionConnectionChanged.RemoveDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleMissionConnectionChanged);
        MissionClient->OnMissionMessageRejected.RemoveDynamic(
            this,
            &ADeferredTeleopArticulatedSceneActor::HandleMissionMessageRejected);
    }
    bSceneReady = false;
    Super::EndPlay(EndPlayReason);
}

void ADeferredTeleopArticulatedSceneActor::Tick(const float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    (void)DeltaSeconds;
    UpdateReceiptAges();
}

bool ADeferredTeleopArticulatedSceneActor::ConfigureBinding(
    const FDeferredTeleopArticulatedModelBinding& RequestedBinding,
    FString& OutError)
{
    if (bBindingConfigured)
    {
        OutError =
            TEXT("articulated binding is already configured; use ReloadLocalDescription to replace it");
        MarkModelInvalid(OutError);
        return false;
    }

    FDeferredTeleopArticulatedModelBinding Candidate;
    if (!DeferredTeleop::ArticulatedScene::ConfigureBinding(
            RequestedBinding,
            Candidate,
            OutError))
    {
        LastError = OutError;
        MarkModelInvalid(OutError);
        return false;
    }

    ModelBinding = MoveTemp(Candidate);
    CommittedBinding = ModelBinding;
    bHasCommittedBinding = true;
    bBindingConfigured = true;
    LastError.Reset();
    FString ReferenceError;
    bSceneReady = ActorReferencesAreValid(ReferenceError);
    if (!bSceneReady && !ReferenceError.IsEmpty())
    {
        LastError = ReferenceError;
    }
    return true;
}

bool ADeferredTeleopArticulatedSceneActor::ReloadLocalDescription(FString& OutError)
{
    // UPROPERTY edits can occur before this explicit operation.  Load into a
    // candidate first so a failed path, robot id, or calibration edit cannot
    // leave new metadata paired with the old catalogue payload.
    const FDeferredTeleopArticulatedModelBinding PreviousBinding = ModelBinding;
    FDeferredTeleopArticulatedModelBinding Candidate = ModelBinding;
    if (!DeferredTeleop::ArticulatedScene::ReloadLocalDescription(Candidate, OutError))
    {
        if (bHasCommittedBinding)
        {
            ModelBinding = CommittedBinding;
        }
        LastError = OutError;
        MarkModelInvalid(OutError);
        return false;
    }
    const bool bBindingIdentityChanged =
        !PreviousBinding.RobotId.Equals(Candidate.RobotId, ESearchCase::CaseSensitive)
        || !PreviousBinding.ExpectedFrameId.Equals(
            Candidate.ExpectedFrameId,
            ESearchCase::CaseSensitive)
        || !PreviousBinding.ExpectedCalibrationVersion.Equals(
            Candidate.ExpectedCalibrationVersion,
            ESearchCase::CaseSensitive)
        || !PreviousBinding.CachedModelKey.Equals(
            Candidate.CachedModelKey,
            ESearchCase::CaseSensitive);
    ModelBinding = MoveTemp(Candidate);
    CommittedBinding = ModelBinding;
    bHasCommittedBinding = true;
    bBindingConfigured = true;
    LastError.Reset();
    FString ReferenceError;
    bSceneReady = ActorReferencesAreValid(ReferenceError);
    if (!bSceneReady && !ReferenceError.IsEmpty())
    {
        LastError = ReferenceError;
    }
    if (bBindingIdentityChanged)
    {
        ResetCachedLayersForBindingChange();
    }
    return true;
}

bool ADeferredTeleopArticulatedSceneActor::ActorReferencesAreValid(FString& OutError) const
{
    OutError.Reset();
    if (!IsValid(ConfirmedActor) || !IsValid(ArrivalActor) || !IsValid(TargetActor))
    {
        OutError = TEXT("three persistent Confirmed, Arrival, and Target actor references are required");
        return false;
    }
    if (ConfirmedActor == ArrivalActor
        || ConfirmedActor == TargetActor
        || ArrivalActor == TargetActor)
    {
        OutError = TEXT("Confirmed, Arrival, and Target references must be three distinct actors");
        return false;
    }
    if (bReferencesLatched
        && (LatchedConfirmedActor.Get() != ConfirmedActor
            || LatchedArrivalActor.Get() != ArrivalActor
            || LatchedTargetActor.Get() != TargetActor))
    {
        OutError = TEXT("persistent articulated scene actor references cannot change after BeginPlay");
        return false;
    }
    return true;
}

bool ADeferredTeleopArticulatedSceneActor::EnsureSceneReady(FString& OutError)
{
    OutError.Reset();
    if (!ActorReferencesAreValid(OutError))
    {
        bSceneReady = false;
        return false;
    }
    if (!bBindingConfigured || !ModelBinding.bHasLoadedDescription)
    {
        bSceneReady = false;
        OutError = TEXT("InvalidModel: local articulated description is not configured");
        return false;
    }
    bSceneReady = true;
    return true;
}

void ADeferredTeleopArticulatedSceneActor::SetCriticalStatus(
    FString Reason,
    const FString& Diagnostic)
{
    LastError = Diagnostic;
    const auto Set = [&Reason, &Diagnostic](FDttArticulatedLayerStatus& Status)
    {
        Status = FDttArticulatedLayerStatus();
        Status.bDegraded = true;
        Status.bCritical = true;
        Status.Reason = Reason;
        Status.Diagnostics.Add(Diagnostic);
    };
    Set(ConfirmedStatus);
    Set(ArrivalStatus);
    Set(TargetStatus);
    if (IsValid(ConfirmedActor))
    {
        ConfirmedActor->SetActorHiddenInGame(true);
    }
    if (IsValid(ArrivalActor))
    {
        ArrivalActor->SetActorHiddenInGame(true);
    }
    if (IsValid(TargetActor))
    {
        TargetActor->SetActorHiddenInGame(true);
    }
    PublishStatus();
}

void ADeferredTeleopArticulatedSceneActor::MarkModelInvalid(const FString& Diagnostic)
{
    LastError = Diagnostic;
    const auto Mark = [this, &Diagnostic](
                          FDttArticulatedLayerStatus& Status,
                          const FLayerPoseCache& Cache,
                          ADeferredTeleopKinematicRobotActor* Actor)
    {
        if (Cache.bHasPose)
        {
            if (IsValid(Actor))
            {
                Actor->SetActorHiddenInGame(false);
            }
            CopyCacheToStatus(Status, Cache, Actor);
            Status.bAvailable = true;
            Status.bDegraded = true;
            Status.bCritical = false;
            Status.Reason = TEXT("InvalidModel");
            Status.Diagnostics = {Diagnostic};
            return;
        }

        Status = FDttArticulatedLayerStatus();
        Status.bAvailable = false;
        Status.bVisible = false;
        Status.bDegraded = true;
        Status.bCritical = false;
        Status.Reason = TEXT("InvalidModel");
        Status.Diagnostics = {Diagnostic};
        if (IsValid(Actor))
        {
            Actor->SetActorHiddenInGame(true);
        }
    };
    Mark(ConfirmedStatus, ConfirmedCache, ConfirmedActor);
    Mark(ArrivalStatus, ArrivalCache, ArrivalActor);
    Mark(TargetStatus, TargetCache, TargetActor);
    PublishStatus();
}

void ADeferredTeleopArticulatedSceneActor::ResetCachedLayersForBindingChange()
{
    const FString Diagnostic =
        TEXT("local catalogue identity changed; awaiting a matching articulated view");
    const auto Reset = [this, &Diagnostic](
                           FLayerPoseCache& Cache,
                           FDttArticulatedLayerStatus& Status,
                           ADeferredTeleopKinematicRobotActor* Actor)
    {
        Cache = FLayerPoseCache();
        Status = FDttArticulatedLayerStatus();
        Status.bDegraded = true;
        Status.Reason = TEXT("InvalidModel");
        Status.Diagnostics.Add(Diagnostic);
        if (IsValid(Actor))
        {
            Actor->SetActorHiddenInGame(true);
        }
    };
    Reset(ConfirmedCache, ConfirmedStatus, ConfirmedActor);
    Reset(ArrivalCache, ArrivalStatus, ArrivalActor);
    Reset(TargetCache, TargetStatus, TargetActor);
    bHasCurrentView = false;
    PublishStatus();
}

void ADeferredTeleopArticulatedSceneActor::UpdateReceiptAges()
{
    const double Now = FPlatformTime::Seconds();
    const auto Update = [Now](FDttArticulatedLayerStatus& Status, const FLayerPoseCache& Cache)
    {
        if (Cache.bHasPose)
        {
            Status.bHasLastGoodPose = true;
            Status.ReceiptAgeSeconds = FMath::Max(
                0.0,
                Now - Cache.ReceiptMonotonicSeconds);
        }
        else
        {
            Status.bHasLastGoodPose = false;
            Status.ReceiptAgeSeconds = -1.0;
        }
    };
    Update(ConfirmedStatus, ConfirmedCache);
    Update(ArrivalStatus, ArrivalCache);
    Update(TargetStatus, TargetCache);
}

void ADeferredTeleopArticulatedSceneActor::PublishStatus()
{
    SceneStatus.Confirmed = ConfirmedStatus;
    SceneStatus.Arrival = ArrivalStatus;
    SceneStatus.Target = TargetStatus;
    OnSceneStatusUpdated.Broadcast(SceneStatus);
}

FDttArticulatedSceneStatus ADeferredTeleopArticulatedSceneActor::GetSceneStatus() const
{
    return SceneStatus;
}

void ADeferredTeleopArticulatedSceneActor::MarkUnavailable(
    FDttArticulatedLayerStatus& Status,
    ADeferredTeleopKinematicRobotActor* Actor)
{
    Status.bAvailable = false;
    Status.bVisible = false;
    Status.bDegraded = false;
    Status.bCritical = false;
    Status.Reason = TEXT("Unavailable");
    Status.Diagnostics.Reset();
    if (IsValid(Actor))
    {
        Actor->SetActorHiddenInGame(true);
    }
}

void ADeferredTeleopArticulatedSceneActor::CopyCacheToStatus(
    FDttArticulatedLayerStatus& Status,
    const FLayerPoseCache& Cache,
    ADeferredTeleopKinematicRobotActor* Actor)
{
    Status.bHasLastGoodPose = Cache.bHasPose;
    Status.bVisible = Cache.bHasPose && IsValid(Actor);
    Status.Provenance = Cache.bHasPose
        ? Cache.Evidence.Provenance
        : EDeferredTeleopProvenance::Unknown;
    Status.SourceIds.Reset();
    if (Cache.bHasPose)
    {
        Status.SourceIds = Cache.Evidence.SourceIds;
    }
    Status.ObservedAt = Cache.bHasPose ? Cache.Evidence.ObservedAt : FDateTime();
    Status.ProducedAt = Cache.bHasPose ? Cache.Evidence.ProducedAt : FDateTime();
    Status.WorldRevision = Cache.bHasPose ? Cache.Evidence.WorldRevision : 0;
    Status.ReceiptAgeSeconds = Cache.bHasPose
        ? FMath::Max(0.0, FPlatformTime::Seconds() - Cache.ReceiptMonotonicSeconds)
        : -1.0;
    Status.ModelKey = Cache.bHasPose ? Cache.ModelKey : FString();
    Status.bHasFreshUntil = Cache.bHasPose && Cache.bHasFreshUntil;
    Status.FreshUntil = Status.bHasFreshUntil ? Cache.FreshUntil : FDateTime();
    Status.EvidenceModelVersion = Cache.bHasPose ? Cache.EvidenceModelVersion : FString();
    Status.bHasEvidenceModelVersion = Cache.bHasPose && Cache.bHasEvidenceModelVersion;
    Status.bHasPredictedFor = Cache.bHasPose && Cache.bHasPredictedFor;
    Status.PredictedFor = Status.bHasPredictedFor ? Cache.PredictedFor : FDateTime();
    Status.bHasEstimatedIntentArrival =
        Cache.bHasPose && Cache.bHasEstimatedIntentArrival;
    Status.EstimatedIntentArrivalAt = Status.bHasEstimatedIntentArrival
        ? Cache.EstimatedIntentArrivalAt
        : FDateTime();
    Status.LinkOneWayDelaySeconds = Cache.bHasPose ? Cache.LinkOneWayDelaySeconds : 0.0;
}

ADeferredTeleopArticulatedSceneActor::FLayerTemporalMetadata
ADeferredTeleopArticulatedSceneActor::MakeLayerTemporalMetadata(
    const FDeferredTeleopArticulatedArrivalRobotState* ArrivalState)
{
    FLayerTemporalMetadata Metadata;
    if (ArrivalState == nullptr || !ArrivalState->bAvailable)
    {
        return Metadata;
    }
    Metadata.bHasPredictedFor = true;
    Metadata.PredictedFor = ArrivalState->PredictedFor;
    Metadata.bHasEstimatedIntentArrival = ArrivalState->bHasEstimatedIntentArrival;
    Metadata.EstimatedIntentArrivalAt = ArrivalState->EstimatedIntentArrivalAt;
    Metadata.LinkOneWayDelaySeconds = ArrivalState->LinkOneWayDelaySeconds;
    return Metadata;
}

void ADeferredTeleopArticulatedSceneActor::MarkInvalidLayer(
    FDttArticulatedLayerStatus& Status,
    ADeferredTeleopKinematicRobotActor* Actor,
    const FLayerPoseCache& Cache,
    const FString& Reason,
    const TArray<FString>& Diagnostics)
{
    if (Cache.bHasPose)
    {
        if (IsValid(Actor))
        {
            // A previously explicit null hides the layer.  A later invalid
            // candidate must show its last valid pose as STALE/DEGRADED.
            Actor->SetActorHiddenInGame(false);
        }
        CopyCacheToStatus(Status, Cache, Actor);
    }
    else
    {
        Status.bHasLastGoodPose = false;
        Status.bVisible = false;
        Status.Provenance = EDeferredTeleopProvenance::Unknown;
        Status.SourceIds.Reset();
        Status.ObservedAt = FDateTime();
        Status.ProducedAt = FDateTime();
        Status.WorldRevision = 0;
        Status.ReceiptAgeSeconds = -1.0;
        Status.ModelKey.Reset();
        Status.bHasFreshUntil = false;
        Status.FreshUntil = FDateTime();
        Status.EvidenceModelVersion.Reset();
        Status.bHasEvidenceModelVersion = false;
        Status.bHasPredictedFor = false;
        Status.PredictedFor = FDateTime();
        Status.bHasEstimatedIntentArrival = false;
        Status.EstimatedIntentArrivalAt = FDateTime();
        Status.LinkOneWayDelaySeconds = 0.0;
        if (IsValid(Actor))
        {
            Actor->SetActorHiddenInGame(true);
        }
    }
    Status.bAvailable = true;
    Status.bDegraded = true;
    Status.bCritical = false;
    Status.Reason = Reason;
    Status.Diagnostics = Diagnostics;
}

bool ADeferredTeleopArticulatedSceneActor::RestoreCachedLayer(
    ADeferredTeleopKinematicRobotActor* Actor,
    const FLayerPoseCache& Cache,
    FString& OutError)
{
    OutError.Reset();
    if (!Cache.bHasPose || !IsValid(Actor))
    {
        return false;
    }
    if (!Actor->InitializeModel(Cache.Description, Cache.RootTransform, OutError))
    {
        return false;
    }
    if (!Actor->ApplyState(Cache.OrderedJointPositions, OutError))
    {
        return false;
    }
    Actor->SetActorHiddenInGame(false);
    return true;
}

bool ADeferredTeleopArticulatedSceneActor::ApplyPreparedLayer(
    const DeferredTeleop::ArticulatedScene::FPreparedLayerState& Prepared,
    const bool bIsConfirmed,
    const FLayerTemporalMetadata& TemporalMetadata,
    ADeferredTeleopKinematicRobotActor* Actor,
    FLayerPoseCache& Cache,
    FDttArticulatedLayerStatus& Status,
    FString& OutError)
{
    OutError.Reset();
    if (!IsValid(Actor))
    {
        OutError = TEXT("critical articulated scene actor reference is invalid");
        Status.bAvailable = true;
        Status.bDegraded = true;
        Status.bCritical = true;
        Status.bVisible = false;
        Status.Reason = TEXT("CriticalMissingActorReferences");
        Status.Diagnostics = {OutError};
        return false;
    }

    if (!Prepared.bWithinJointLimits)
    {
        const bool bMeasuredOutlier = bIsConfirmed
            && DeferredTeleop::ArticulatedScene::Private::IsMeasuredProvenance(
                Prepared.Evidence.Provenance);
        const FString Reason = bMeasuredOutlier ? TEXT("MeasuredOutlier") : TEXT("InvalidLayer");
        MarkInvalidLayer(Status, Actor, Cache, Reason, Prepared.ForwardKinematics.Diagnostics);
        OutError = FString::Printf(
            TEXT("%s: joint-limit validation rejected the candidate pose"),
            *Reason);
        return false;
    }

    FString ApplyError;
    const bool bInitialized = Actor->InitializeModel(
        ModelBinding.Description,
        Prepared.RootTransform,
        ApplyError);
    bool bApplied = false;
    if (bInitialized)
    {
#if WITH_DEV_AUTOMATION_TESTS
        if (bTestFailNextApply)
        {
            bTestFailNextApply = false;
            ApplyError = TEXT("test-only forced ApplyState failure");
        }
        else
#endif
        {
            bApplied = Actor->ApplyState(Prepared.OrderedJointPositions, ApplyError);
        }
    }

    if (!bInitialized || !bApplied)
    {
        FString RestoreError;
        if (Cache.bHasPose && !RestoreCachedLayer(Actor, Cache, RestoreError))
        {
            Actor->SetActorHiddenInGame(true);
            Status.bAvailable = true;
            Status.bHasLastGoodPose = Cache.bHasPose;
            Status.bVisible = false;
            Status.bDegraded = true;
            Status.bCritical = true;
            Status.Reason = TEXT("CriticalRestoreFailure");
            Status.Diagnostics.Reset();
            if (!ApplyError.IsEmpty())
            {
                Status.Diagnostics.Add(ApplyError);
            }
            if (!RestoreError.IsEmpty())
            {
                Status.Diagnostics.Add(RestoreError);
            }
            OutError = FString::Printf(
                TEXT("candidate apply failed and last-good restoration failed: %s"),
                *RestoreError);
            return false;
        }

        TArray<FString> Diagnostics;
        if (!ApplyError.IsEmpty())
        {
            Diagnostics.Add(ApplyError);
        }
        if (!RestoreError.IsEmpty())
        {
            Diagnostics.Add(FString::Printf(TEXT("restored last-good pose: %s"), *RestoreError));
        }
        MarkInvalidLayer(Status, Actor, Cache, TEXT("STALE/DEGRADED"), Diagnostics);
        OutError = ApplyError.IsEmpty()
            ? TEXT("articulated candidate apply failed")
            : ApplyError;
        return false;
    }

    Cache.bHasPose = true;
    Cache.Description = ModelBinding.Description;
    Cache.RootTransform = Prepared.RootTransform;
    Cache.OrderedJointPositions = Prepared.OrderedJointPositions;
    Cache.Evidence = Prepared.Evidence;
    Cache.ModelKey = Prepared.ModelKey;
    Cache.ReceiptMonotonicSeconds = LastViewReceiptMonotonicSeconds;
    Cache.bHasFreshUntil = Prepared.Evidence.bHasFreshUntil;
    Cache.FreshUntil = Prepared.Evidence.FreshUntil;
    Cache.EvidenceModelVersion = Prepared.Evidence.ModelVersion;
    Cache.bHasEvidenceModelVersion = !Prepared.Evidence.ModelVersion.IsEmpty();
    Cache.bHasPredictedFor = TemporalMetadata.bHasPredictedFor;
    Cache.PredictedFor = TemporalMetadata.PredictedFor;
    Cache.bHasEstimatedIntentArrival = TemporalMetadata.bHasEstimatedIntentArrival;
    Cache.EstimatedIntentArrivalAt = TemporalMetadata.EstimatedIntentArrivalAt;
    Cache.LinkOneWayDelaySeconds = TemporalMetadata.LinkOneWayDelaySeconds;

    CopyCacheToStatus(Status, Cache, Actor);
    Status.bAvailable = true;
    Status.bHasLastGoodPose = true;
    Status.bVisible = true;
    Status.bDegraded = false;
    Status.bCritical = false;
    Status.Reason = TEXT("VALID");
    Status.Diagnostics = Prepared.ForwardKinematics.Diagnostics;
    Actor->SetActorHiddenInGame(false);
    return true;
}

bool ADeferredTeleopArticulatedSceneActor::ProcessLayer(
    const FDeferredTeleopArticulatedRobotState* RobotState,
    const bool bIsConfirmed,
    const bool bIsArrival,
    const FLayerTemporalMetadata& TemporalMetadata,
    ADeferredTeleopKinematicRobotActor* Actor,
    FLayerPoseCache& Cache,
    FDttArticulatedLayerStatus& Status,
    FString& OutError)
{
    OutError.Reset();
    if (RobotState == nullptr)
    {
        if (Cache.bHasPose)
        {
            CopyCacheToStatus(Status, Cache, Actor);
        }
        MarkUnavailable(Status, Actor);
        return true;
    }

    if (!DeferredTeleop::ArticulatedScene::Private::ValidateLayerRole(
            *RobotState,
            bIsConfirmed,
            bIsArrival,
            TemporalMetadata.bHasPredictedFor,
            TemporalMetadata.PredictedFor,
            TemporalMetadata.bHasEstimatedIntentArrival,
            TemporalMetadata.EstimatedIntentArrivalAt,
            TemporalMetadata.LinkOneWayDelaySeconds,
            OutError))
    {
        TArray<FString> Diagnostics;
        Diagnostics.Add(OutError);
        MarkInvalidLayer(Status, Actor, Cache, TEXT("InvalidLayer"), Diagnostics);
        return false;
    }

    DeferredTeleop::ArticulatedScene::FPreparedLayerState Prepared;
    if (!DeferredTeleop::ArticulatedScene::PrepareLayerState(
            ModelBinding,
            *RobotState,
            Prepared,
            OutError))
    {
        const FString Reason =
            DeferredTeleop::ArticulatedScene::Private::FailureLabel(Prepared.Failure);
        TArray<FString> Diagnostics;
        Diagnostics.Add(OutError);
        MarkInvalidLayer(Status, Actor, Cache, Reason, Diagnostics);
        return false;
    }

    return ApplyPreparedLayer(
        Prepared,
        bIsConfirmed,
        TemporalMetadata,
        Actor,
        Cache,
        Status,
        OutError);
}

bool ADeferredTeleopArticulatedSceneActor::ApplyArticulatedViewState(
    const FDeferredTeleopArticulatedViewState& ViewState,
    FString& OutError)
{
    OutError.Reset();
    FString ReadyError;
    if (!EnsureSceneReady(ReadyError))
    {
        SetCriticalStatus(TEXT("CriticalSceneNotReady"), ReadyError);
        OutError = ReadyError;
        return false;
    }

    LastViewState = ViewState;
    bHasCurrentView = true;
    LastViewReceiptMonotonicSeconds = FPlatformTime::Seconds();

    FString FirstError;
    bool bAllAccepted = true;
    FString LayerError;
    const FLayerTemporalMetadata NoArrivalMetadata;
    const FLayerTemporalMetadata ArrivalMetadata = MakeLayerTemporalMetadata(
        ViewState.ArrivalRobotState.bAvailable ? &ViewState.ArrivalRobotState : nullptr);
    if (!ProcessLayer(
            ViewState.bHasConfirmedRobotState ? &ViewState.ConfirmedRobotState : nullptr,
            true,
            false,
            NoArrivalMetadata,
            ConfirmedActor,
            ConfirmedCache,
            ConfirmedStatus,
            LayerError))
    {
        bAllAccepted = false;
        FirstError = LayerError;
    }
    if (!ProcessLayer(
            ViewState.ArrivalRobotState.bAvailable
                ? &ViewState.ArrivalRobotState.RobotState
                : nullptr,
            false,
            true,
            ArrivalMetadata,
            ArrivalActor,
            ArrivalCache,
            ArrivalStatus,
            LayerError))
    {
        bAllAccepted = false;
        if (FirstError.IsEmpty())
        {
            FirstError = LayerError;
        }
    }
    if (!ProcessLayer(
            ViewState.bHasTargetRobotState ? &ViewState.TargetRobotState : nullptr,
            false,
            false,
            NoArrivalMetadata,
            TargetActor,
            TargetCache,
            TargetStatus,
            LayerError))
    {
        bAllAccepted = false;
        if (FirstError.IsEmpty())
        {
            FirstError = LayerError;
        }
    }

    UpdateReceiptAges();
    LastError = FirstError;
    OutError = FirstError;
    PublishStatus();
    return bAllAccepted;
}

bool ADeferredTeleopArticulatedSceneActor::ApplyArticulatedViewJson(
    const FString& Json,
    FString& OutError)
{
    FDeferredTeleopArticulatedViewState Parsed;
    if (!DeferredTeleop::ArticulatedView::ParseArticulated(Json, Parsed, OutError))
    {
        LastError = OutError;
        MarkMessageRejected(OutError);
        return false;
    }
    return ApplyArticulatedViewState(Parsed, OutError);
}

void ADeferredTeleopArticulatedSceneActor::MarkMessageRejected(const FString& Reason)
{
    LastError = Reason;
    const FString Diagnostic = FString::Printf(TEXT("STALE/DEGRADED: %s"), *Reason);
    const auto Mark = [this, &Diagnostic](
                          FDttArticulatedLayerStatus& Status,
                          const FLayerPoseCache& Cache,
                          ADeferredTeleopKinematicRobotActor* Actor)
    {
        if (!Cache.bHasPose)
        {
            if (IsValid(Actor))
            {
                Actor->SetActorHiddenInGame(true);
            }
            Status.bDegraded = true;
            Status.bCritical = false;
            Status.bVisible = false;
            Status.Reason = TEXT("STALE/DEGRADED");
            Status.Diagnostics = {Diagnostic};
            return;
        }
        if (IsValid(Actor))
        {
            Actor->SetActorHiddenInGame(false);
        }
        CopyCacheToStatus(Status, Cache, Actor);
        Status.bAvailable = true;
        Status.bDegraded = true;
        Status.bCritical = false;
        Status.Reason = TEXT("STALE/DEGRADED");
        Status.Diagnostics = {Diagnostic};
    };
    Mark(ConfirmedStatus, ConfirmedCache, ConfirmedActor);
    Mark(ArrivalStatus, ArrivalCache, ArrivalActor);
    Mark(TargetStatus, TargetCache, TargetActor);
    PublishStatus();
}

void ADeferredTeleopArticulatedSceneActor::MarkDisconnected()
{
    const FString Diagnostic = TEXT("STALE/DEGRADED: Mission connection disconnected");
    const auto Mark = [this, &Diagnostic](
                          FDttArticulatedLayerStatus& Status,
                          const FLayerPoseCache& Cache,
                          ADeferredTeleopKinematicRobotActor* Actor)
    {
        if (!Cache.bHasPose)
        {
            if (IsValid(Actor))
            {
                Actor->SetActorHiddenInGame(true);
            }
            Status.bAvailable = false;
            Status.bHasLastGoodPose = false;
            Status.bVisible = false;
            Status.bDegraded = true;
            Status.bCritical = false;
            Status.Reason = TEXT("STALE/DEGRADED");
            Status.Diagnostics = {Diagnostic};
            return;
        }
        if (IsValid(Actor))
        {
            Actor->SetActorHiddenInGame(false);
        }
        CopyCacheToStatus(Status, Cache, Actor);
        Status.bAvailable = true;
        Status.bDegraded = true;
        Status.bCritical = false;
        Status.Reason = TEXT("STALE/DEGRADED");
        Status.Diagnostics = {Diagnostic};
    };
    Mark(ConfirmedStatus, ConfirmedCache, ConfirmedActor);
    Mark(ArrivalStatus, ArrivalCache, ArrivalActor);
    Mark(TargetStatus, TargetCache, TargetActor);
    PublishStatus();
}

void ADeferredTeleopArticulatedSceneActor::HandleArticulatedViewStateUpdated(
    const FDeferredTeleopArticulatedViewState& ViewState)
{
    FString Error;
    if (!ApplyArticulatedViewState(ViewState, Error) && !Error.IsEmpty())
    {
        UE_LOG(LogDeferredTeleop, Warning, TEXT("Articulated scene rejected a view: %s"), *Error);
    }
}

void ADeferredTeleopArticulatedSceneActor::HandleMissionConnectionChanged(
    const EDeferredTeleopConnectionState ConnectionState)
{
    if (ConnectionState == EDeferredTeleopConnectionState::Disconnected)
    {
        MarkDisconnected();
    }
}

void ADeferredTeleopArticulatedSceneActor::HandleMissionMessageRejected(
    const FString& Reason)
{
    MarkMessageRejected(Reason);
}

#if WITH_DEV_AUTOMATION_TESTS
void ADeferredTeleopArticulatedSceneActor::SetTestFailNextApply(const bool bShouldFail)
{
    bTestFailNextApply = bShouldFail;
}
#endif
