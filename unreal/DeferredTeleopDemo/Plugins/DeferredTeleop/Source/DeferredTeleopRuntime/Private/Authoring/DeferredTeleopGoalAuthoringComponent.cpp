#include "Authoring/DeferredTeleopGoalAuthoringComponent.h"

#include "Articulated/DeferredTeleopArticulatedSceneValidation.h"
#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "HAL/PlatformTime.h"
#include "Kinematics/DeferredTeleopIKLibrary.h"
#include "Kinematics/DeferredTeleopKinematicPreviewLibrary.h"

UDeferredTeleopGoalAuthoringComponent::UDeferredTeleopGoalAuthoringComponent()
{
    PrimaryComponentTick.bCanEverTick = true;
}

void UDeferredTeleopGoalAuthoringComponent::PublishDiagnostic(const FString& Reason)
{
    const FString PublishedDiagnostic = Reason;
    LastDiagnostic = PublishedDiagnostic;
    OnAuthoringDiagnostic.Broadcast(PublishedDiagnostic);
}

bool UDeferredTeleopGoalAuthoringComponent::Reject(const FString& Reason, FString& OutError)
{
    OutError = Reason;
    bHasPendingGoal = false;
    CurrentInputId = FGuid::NewGuid();
    PublishDiagnostic(Reason);
    return false;
}

bool UDeferredTeleopGoalAuthoringComponent::ConfigureFromConfirmedView(
    const FDeferredTeleopArticulatedModelBinding& Binding,
    const FDeferredTeleopArticulatedViewState& View,
    const FDttGoalAuthoringSettings& Settings,
    FString& OutError)
{
    OutError.Reset();
    // A failed rebase cannot leave a previously selected source actionable.
    // Its last preview may remain on screen, explicitly not current.
    bConfigured = false;
    bHasPendingGoal = false;
    CurrentInputId = FGuid::NewGuid();
    if (!View.ProtocolVersion.Equals(TEXT("dtt/0"), ESearchCase::CaseSensitive)
        || !View.MessageType.Equals(TEXT("mission.articulated_view_state"), ESearchCase::CaseSensitive)
        || !View.bHasConfirmedRobotState || View.SourceId.TrimStartAndEnd().IsEmpty()
        || View.SourceSequence < 0 || View.Status.CorrelationId.TrimStartAndEnd().IsEmpty())
    {
        return Reject(TEXT("A confirmed articulated view with source/correlation identity is required"), OutError);
    }
    if (!FMath::IsFinite(Settings.MaximumSolveRateHz)
        || Settings.MaximumSolveRateHz < 1.0 || Settings.MaximumSolveRateHz > 90.0)
    {
        return Reject(TEXT("MaximumSolveRateHz must be finite in [1,90]"), OutError);
    }
    const EDeferredTeleopProvenance Provenance = View.ConfirmedRobotState.Evidence.Provenance;
    if (Provenance != EDeferredTeleopProvenance::Measured
        && Provenance != EDeferredTeleopProvenance::Fused)
    {
        return Reject(TEXT("This slice starts only from declared MEASURED/FUSED confirmed evidence"), OutError);
    }

    FDeferredTeleopArticulatedModelBinding CandidateBinding;
    DeferredTeleop::ArticulatedScene::FPreparedLayerState Prepared;
    FString Error;
    if (!DeferredTeleop::ArticulatedScene::ConfigureBinding(Binding, CandidateBinding, Error)
        || !DeferredTeleop::ArticulatedScene::PrepareLayerState(
            CandidateBinding, View.ConfirmedRobotState, Prepared, Error))
    {
        return Reject(Error, OutError);
    }
    if (!Prepared.bWithinJointLimits)
    {
        return Reject(TEXT("The selected start state exceeds structural limits"), OutError);
    }
    const FDttNamedCanonicalTransform* Tool = Prepared.ForwardKinematics.ToolTransforms.FindByPredicate(
        [&Settings](const FDttNamedCanonicalTransform& Value) { return Value.Name == Settings.ToolFrameName; });
    if (Tool == nullptr)
    {
        return Reject(TEXT("Requested authoring tool frame is missing"), OutError);
    }

    FDttIKRequest InitialIK;
    InitialIK.JointGroupName = Settings.JointGroupName;
    InitialIK.ToolFrameName = Settings.ToolFrameName;
    InitialIK.WorldTransformOfRoot = Prepared.RootTransform;
    InitialIK.SeedJointPositions = Prepared.OrderedJointPositions;
    InitialIK.TargetPositionMetres = Tool->Transform.TranslationMetres;
    InitialIK.Mode = EDttIKMode::PositionPlusApproachAxis;
    InitialIK.LocalToolApproachAxis = Settings.LocalToolApproachAxis;
    InitialIK.TargetApproachDirectionCanonical = FDttCanonicalVector::FromVector3d(
        Tool->Transform.GetRotationQuaternion().RotateVector(Settings.LocalToolApproachAxis.ToVector3d()));
    FDttIKResult InitialResult;
    if (!DeferredTeleop::Kinematics::SolveInverseKinematics(
            CandidateBinding.Description, InitialIK, Settings.IK, InitialResult)
        || !InitialResult.bSuccess)
    {
        return Reject(TEXT("Invalid authoring IK settings or source: ") + InitialResult.Diagnostic, OutError);
    }

    FDttKinematicPreviewRequest Candidate;
    Candidate.PreviewId = FGuid::NewGuid();
    Candidate.GoalId = FGuid::NewGuid();
    Candidate.ModelReference = View.ConfirmedRobotState.ModelReference;
    Candidate.WorldTransformOfRoot = Prepared.RootTransform;
    Candidate.StartJointPositions = View.ConfirmedRobotState.Joints;
    Candidate.IKResult = InitialResult;
    Candidate.Settings = Settings.Preview;
    // A stable Mission VIEW key, not an invented sensor-message UUID.
    Candidate.SourceReference.SourceMessageId = FString::Printf(
        TEXT("%s/view/%d"), *View.SourceId, View.SourceSequence);
    Candidate.SourceReference.CorrelationId = View.Status.CorrelationId;
    Candidate.SourceReference.FrameId = Binding.ExpectedFrameId;
    Candidate.SourceReference.CalibrationVersion = Binding.ExpectedCalibrationVersion;
    Candidate.SourceReference.Evidence = View.ConfirmedRobotState.Evidence;
    Candidate.SourceReference.SourceKind = Provenance == EDeferredTeleopProvenance::Measured
        ? EDttPreviewSourceKind::Measured : EDttPreviewSourceKind::Fused;
    FDttKinematicPreview InitialPreview;
    if (!DeferredTeleop::Kinematics::BuildPreview(
            CandidateBinding.Description, Candidate, InitialPreview, Error))
    {
        return Reject(Error, OutError);
    }

    SourceBinding = MoveTemp(CandidateBinding);
    SourceRequest = MoveTemp(Candidate);
    CurrentSettings = Settings;
    WarmSeed = Prepared.OrderedJointPositions;
    StartToolTransform = Tool->Transform;
    ClearCandidate();
    bConfigured = true;
    NextSolveAt = 0.0;
    LastPumpAt = -1.0;
    PublishDiagnostic(TEXT("Source snapshot selected; local preview only, no command path"));
    return true;
}

bool UDeferredTeleopGoalAuthoringComponent::ConfigureFromConfirmedJson(
    const FDeferredTeleopArticulatedModelBinding& Binding,
    const FString& ViewJson,
    const FDttGoalAuthoringSettings& Settings,
    FString& OutError)
{
    FDeferredTeleopArticulatedViewState View;
    if (!DeferredTeleop::ArticulatedView::ParseArticulated(ViewJson, View, OutError))
    {
        bConfigured = false;
        const FString Error = OutError;
        return Reject(Error, OutError);
    }
    return ConfigureFromConfirmedView(Binding, View, Settings, OutError);
}

bool UDeferredTeleopGoalAuthoringComponent::QueueCanonicalGoal(
    const FDttCanonicalTransform& Goal, EDttIKMode Mode, FString& OutError)
{
    OutError.Reset();
    if (!bConfigured || !Goal.IsRigid()
        || (Mode != EDttIKMode::PositionOnly && Mode != EDttIKMode::PositionPlusApproachAxis))
    {
        return Reject(TEXT("A configured source, rigid canonical goal and supported IK mode are required"), OutError);
    }
    PendingGoal = Goal;
    PendingMode = Mode;
    PendingGoalId = FGuid::NewGuid();
    CurrentInputId = PendingGoalId;
    bHasPendingGoal = true; // Replace, never append to a hand-sample queue.
    LastDiagnostic = TEXT("Pending latest local goal");
    return true;
}

bool UDeferredTeleopGoalAuthoringComponent::QueueUnrealGoal(
    const FTransform& TargetWorld, const FTransform& SiteToUnrealWorld,
    EDttIKMode Mode, FString& OutError)
{
    FDttCanonicalTransform Checked, CanonicalGoal;
    // Validate both operands before relative transform: inverse scale must not hide a bad input.
    if (!DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(TargetWorld, Checked, OutError)
        || !DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(SiteToUnrealWorld, Checked, OutError)
        || !DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            TargetWorld.GetRelativeTransform(SiteToUnrealWorld), CanonicalGoal, OutError))
    {
        const FString Error = OutError;
        return Reject(Error, OutError);
    }
    return QueueCanonicalGoal(CanonicalGoal, Mode, OutError);
}

void UDeferredTeleopGoalAuthoringComponent::ProcessPendingAt(double MonotonicSeconds)
{
    if (!FMath::IsFinite(MonotonicSeconds) || MonotonicSeconds < 0.0)
    {
        FString Error;
        Reject(TEXT("Invalid monotonic authoring clock"), Error);
        return;
    }
    if (LastPumpAt >= 0.0 && MonotonicSeconds < LastPumpAt)
    {
        NextSolveAt = MonotonicSeconds + 1.0 / CurrentSettings.MaximumSolveRateHz;
    }
    LastPumpAt = MonotonicSeconds;
    if (!bConfigured || !bHasPendingGoal || MonotonicSeconds < NextSolveAt)
    {
        return;
    }
    NextSolveAt = MonotonicSeconds + 1.0 / CurrentSettings.MaximumSolveRateHz;
    bHasPendingGoal = false;
    const FGuid SolvedId = PendingGoalId;
    const double Started = FPlatformTime::Seconds();
    ++SolveCount;
    FDttIKRequest Request;
    Request.JointGroupName = CurrentSettings.JointGroupName;
    Request.ToolFrameName = CurrentSettings.ToolFrameName;
    Request.Mode = PendingMode;
    Request.WorldTransformOfRoot = SourceRequest.WorldTransformOfRoot;
    Request.SeedJointPositions = WarmSeed;
    Request.TargetPositionMetres = PendingGoal.TranslationMetres;
    Request.LocalToolApproachAxis = CurrentSettings.LocalToolApproachAxis;
    Request.TargetApproachDirectionCanonical = FDttCanonicalVector::FromVector3d(
        PendingGoal.GetRotationQuaternion().RotateVector(CurrentSettings.LocalToolApproachAxis.ToVector3d()));
    DeferredTeleop::Kinematics::SolveInverseKinematics(
        SourceBinding.Description, Request, CurrentSettings.IK, LastIKResult);

    FDttKinematicPreviewRequest PreviewRequest = SourceRequest;
    PreviewRequest.PreviewId = FGuid::NewGuid();
    PreviewRequest.GoalId = SolvedId;
    PreviewRequest.IKResult = LastIKResult;
    FDttKinematicPreview Candidate;
    FString Error;
    const bool bValid = DeferredTeleop::Kinematics::BuildPreview(
        SourceBinding.Description, PreviewRequest, Candidate, Error);
    LastSolveMilliseconds = (FPlatformTime::Seconds() - Started) * 1000.0;
    if (!bValid)
    {
        PublishDiagnostic(TEXT("Local goal not accepted: ") + Error + TEXT("; ") + LastIKResult.Diagnostic);
        return; // Keep the old drawing, but HasCurrentPreview remains false.
    }
    LastValidPreview = MoveTemp(Candidate);
    AcceptedInputId = SolvedId;
    WarmSeed = LastIKResult.JointPositions;
    const FDttKinematicPreview PublishedPreview = LastValidPreview;
    const FString Diagnostic = LastValidPreview.bAcceptedPartial
        ? TEXT("Partial LOCAL preview accepted explicitly; not an executable plan")
        : TEXT("Current LOCAL kinematic preview; no collision/dynamics validation");
    LastDiagnostic = Diagnostic;
    // A Blueprint listener may rebase or queue another goal during a delegate.
    // Publish an immutable copy, not storage that such a callback can replace.
    OnPreviewUpdated.Broadcast(PublishedPreview);
    if (HasCurrentPreview() && AcceptedInputId == SolvedId)
    {
        OnAuthoringDiagnostic.Broadcast(Diagnostic);
    }
}

void UDeferredTeleopGoalAuthoringComponent::TickComponent(
    float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
    Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
    ProcessPendingAt(FPlatformTime::Seconds());
}

bool UDeferredTeleopGoalAuthoringComponent::HasCurrentPreview() const
{
    return bConfigured && !bHasPendingGoal && LastValidPreview.bValid
        && CurrentInputId.IsValid() && CurrentInputId == AcceptedInputId;
}

bool UDeferredTeleopGoalAuthoringComponent::CopyCurrentPreview(
    FDttKinematicPreview& OutPreview, FString& OutError) const
{
    OutPreview = FDttKinematicPreview();
    OutError.Reset();
    if (!HasCurrentPreview())
    {
        OutError = TEXT("No preview for the current input/source; stale candidate cannot be frozen");
        return false;
    }
    OutPreview = LastValidPreview;
    return true;
}

bool UDeferredTeleopGoalAuthoringComponent::GetSourceModelAndState(
    FDttRobotDescription& OutDescription, FDttCanonicalTransform& OutRoot,
    TArray<FDttNamedJointPosition>& OutJoints) const
{
    OutDescription = FDttRobotDescription();
    OutRoot = FDttCanonicalTransform::Identity();
    OutJoints.Reset();
    if (!bConfigured)
    {
        return false;
    }
    OutDescription = SourceBinding.Description;
    OutRoot = SourceRequest.WorldTransformOfRoot;
    for (const FDeferredTeleopArticulatedJointPosition& Joint : SourceRequest.StartJointPositions)
    {
        FDttNamedJointPosition Named;
        Named.JointName = FName(*Joint.JointName);
        Named.PositionRadians = Joint.PositionRadians;
        OutJoints.Add(Named);
    }
    return true;
}

void UDeferredTeleopGoalAuthoringComponent::ClearCandidate()
{
    bHasPendingGoal = false;
    LastValidPreview = FDttKinematicPreview();
    LastIKResult = FDttIKResult();
    CurrentInputId.Invalidate();
    AcceptedInputId.Invalidate();
    PendingGoalId.Invalidate();
    WarmSeed.Reset();
    for (const FDeferredTeleopArticulatedJointPosition& Joint : SourceRequest.StartJointPositions)
    {
        FDttNamedJointPosition Named;
        Named.JointName = FName(*Joint.JointName);
        Named.PositionRadians = Joint.PositionRadians;
        WarmSeed.Add(Named);
    }
    LastDiagnostic = TEXT("Local candidate cleared");
}
