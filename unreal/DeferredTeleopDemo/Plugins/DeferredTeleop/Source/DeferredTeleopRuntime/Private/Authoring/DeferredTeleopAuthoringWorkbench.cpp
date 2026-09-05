#include "Authoring/DeferredTeleopAuthoringWorkbench.h"

#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "Components/SceneComponent.h"
#include "DrawDebugHelpers.h"
#include "Engine/World.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Visualization/DeferredTeleopKinematicRobotActor.h"

ADeferredTeleopAuthoringWorkbench::ADeferredTeleopAuthoringWorkbench()
{
    PrimaryActorTick.bCanEverTick = true;
    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("WorkbenchRoot"));
    SetRootComponent(SceneRoot);
    TargetHandle = CreateDefaultSubobject<USceneComponent>(TEXT("TargetHandle"));
    TargetHandle->SetupAttachment(SceneRoot);
    Authoring = CreateDefaultSubobject<UDeferredTeleopGoalAuthoringComponent>(TEXT("GoalAuthoring"));
    // The handle is data, not a simulated rigid body or an actuator target.
}

void ADeferredTeleopAuthoringWorkbench::ConnectDelegates()
{
    Authoring->OnPreviewUpdated.AddUniqueDynamic(this, &ADeferredTeleopAuthoringWorkbench::HandlePreview);
    Authoring->OnAuthoringDiagnostic.AddUniqueDynamic(this, &ADeferredTeleopAuthoringWorkbench::HandleDiagnostic);
}

void ADeferredTeleopAuthoringWorkbench::BeginPlay()
{
    Super::BeginPlay();
    ConnectDelegates();
    // Queue at most the latest changed handle before the component's capped solve.
    Authoring->AddTickPrerequisiteActor(this);
    if (bInitializeSyntheticFixtureOnBeginPlay)
    {
        FString Error;
        InitializeSyntheticFixture(Error);
    }
}

bool ADeferredTeleopAuthoringWorkbench::ValidateStage(FString& OutError) const
{
    OutError.Reset();
    if (!IsInGameThread())
    {
        OutError = TEXT("Workbench operations require the Game Thread");
        return false;
    }
    if (!GetActorTransform().Equals(FTransform::Identity, 1.0e-6))
    {
        OutError = TEXT("Workbench must stay at identity: move the operator, not the robot stage");
        return false;
    }
    return true;
}

bool ADeferredTeleopAuthoringWorkbench::EnsureOwnedRobots(FString& OutError)
{
    if (IsValid(ReferenceRobot) && IsValid(CandidateRobot) && ReferenceRobot != CandidateRobot)
    {
        return true;
    }
    if (GetWorld() == nullptr)
    {
        OutError = TEXT("A gameplay world is required to create the workbench visuals");
        return false;
    }
    DestroyOwnedRobots();
    FActorSpawnParameters Parameters;
    Parameters.Owner = this;
    Parameters.ObjectFlags |= RF_Transient;
    Parameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    ReferenceRobot = GetWorld()->SpawnActor<ADeferredTeleopKinematicRobotActor>(
        ADeferredTeleopKinematicRobotActor::StaticClass(), FTransform::Identity, Parameters);
    CandidateRobot = GetWorld()->SpawnActor<ADeferredTeleopKinematicRobotActor>(
        ADeferredTeleopKinematicRobotActor::StaticClass(), FTransform::Identity, Parameters);
    if (!IsValid(ReferenceRobot) || !IsValid(CandidateRobot))
    {
        DestroyOwnedRobots();
        OutError = TEXT("Could not create both local workbench robots");
        return false;
    }
    return true;
}

bool ADeferredTeleopAuthoringWorkbench::InitializeSyntheticFixture(FString& OutError)
{
    CancelLocalEdit();
    bReady = false;
    ConnectDelegates();
    if (IsValid(ReferenceRobot)) { ReferenceRobot->SetActorHiddenInGame(true); }
    if (IsValid(CandidateRobot)) { CandidateRobot->SetActorHiddenInGame(true); }
    if (!ValidateStage(OutError)) { StatusText = OutError; return false; }

    // Paths come only from this local example, never from a received message.
    const FString RepoRoot = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("../../"));
    FString Json;
    if (!FFileHelper::LoadFileToString(Json, *(RepoRoot
        / TEXT("fixtures/m2/articulated-state/valid-articulated-view.json"))))
    {
        OutError = TEXT("Synthetic fixture missing: use the full checkout, not a packaged plugin");
        StatusText = OutError;
        return false;
    }
    FDeferredTeleopArticulatedViewState View;
    if (!DeferredTeleop::ArticulatedView::ParseArticulated(Json, View, OutError))
    {
        StatusText = OutError;
        return false;
    }
    FDeferredTeleopArticulatedModelBinding Binding;
    Binding.RobotId = TEXT("so101-follower-1");
    Binding.DescriptionFilePath = RepoRoot / TEXT("robots/so101/generated/so101.kinematics.json");
    Binding.ExpectedFrameId = TEXT("field-world");
    Binding.ExpectedCalibrationVersion = TEXT("field-cal-1");
    FDttGoalAuthoringSettings Settings;
    for (const FDeferredTeleopArticulatedJointPosition& Joint : View.ConfirmedRobotState.Joints)
    {
        FDttPreviewJointVelocity Speed;
        Speed.JointName = FName(*Joint.JointName);
        Speed.MaximumRadiansPerSecond = 0.5; // presentation only, including the inactive gripper
        Settings.Preview.JointVelocities.Add(Speed);
    }
    if (!Authoring->ConfigureFromConfirmedView(Binding, View, Settings, OutError)
        || !EnsureOwnedRobots(OutError))
    {
        StatusText = OutError;
        return false;
    }
    FDttRobotDescription Model;
    FDttCanonicalTransform Root;
    TArray<FDttNamedJointPosition> Joints;
    if (!Authoring->GetSourceModelAndState(Model, Root, Joints))
    {
        OutError = TEXT("Configured source unavailable"); StatusText = OutError; return false;
    }
    // Optional presentation assets already distributed with the plugin. No downloaded assets.
    UMaterialInterface* Ref = ReferenceMaterial;
    UMaterialInterface* Candidate = CandidateMaterial;
    if (Ref == nullptr)
    {
        Ref = LoadObject<UMaterialInterface>(nullptr, TEXT("/DeferredTeleop/Materials/M_Confirmed.M_Confirmed"));
    }
    if (Candidate == nullptr)
    {
        Candidate = LoadObject<UMaterialInterface>(nullptr, TEXT("/DeferredTeleop/Materials/M_Target.M_Target"));
    }
    ReferenceRobot->SemanticLayer = EDeferredTeleopKinematicSemanticLayer::Confirmed;
    CandidateRobot->SemanticLayer = EDeferredTeleopKinematicSemanticLayer::Target;
    ReferenceRobot->LinkMaterial = Ref;
    ReferenceRobot->SegmentMaterial = Ref;
    CandidateRobot->LinkMaterial = Candidate;
    CandidateRobot->SegmentMaterial = Candidate;
    ReferenceRobot->bShowDebugNames = false;
    CandidateRobot->bShowDebugNames = false;
    if (!ReferenceRobot->InitializeModel(Model, Root, OutError)
        || !ReferenceRobot->ApplyState(Joints, OutError)
        || !CandidateRobot->InitializeModel(Model, Root, OutError)
        || !CandidateRobot->ApplyState(Joints, OutError))
    {
        StatusText = OutError;
        return false;
    }
    ReferenceRobot->SetActorHiddenInGame(false);
    CandidateRobot->SetActorHiddenInGame(true); // no fabricated initial accepted candidate
    bReady = true;
    return ResetTargetToSource(OutError);
}

bool ADeferredTeleopAuthoringWorkbench::QueueHandle(FString& OutError)
{
    if (!bReady || !ValidateStage(OutError))
    {
        if (OutError.IsEmpty()) { OutError = TEXT("Workbench is not initialized"); }
        CancelLocalEdit();
        StatusText = OutError;
        return false;
    }
    const FTransform Requested = TargetHandle->GetComponentTransform();
    if (!Authoring->QueueUnrealGoal(Requested, FTransform::Identity, GoalMode, OutError))
    {
        bFreezePending = false;
        StatusText = OutError;
        return false;
    }
    LastQueuedTarget = Requested;
    bHasQueuedTarget = true;
    bHasPresentationError = false;
    StatusText = TEXT("SYNTHETIC FIXTURE / local goal pending - no command");
    return true;
}

bool ADeferredTeleopAuthoringWorkbench::BeginTargetEdit(FString& OutError)
{
    bHasFrozenPreview = false;
    FrozenPreview = FDttKinematicPreview();
    bFreezePending = false;
    bEditing = QueueHandle(OutError);
    return bEditing;
}

bool ADeferredTeleopAuthoringWorkbench::EndTargetEdit(bool bFreezeLocally, FString& OutError)
{
    if (!bReady || !bEditing)
    {
        OutError = TEXT("No active edit to release; no previous candidate was frozen");
        return false;
    }
    bEditing = false;
    bFreezePending = bFreezeLocally;
    // Always include the last exact pose, even if no Tick has observed it yet.
    return QueueHandle(OutError);
}

void ADeferredTeleopAuthoringWorkbench::CancelLocalEdit()
{
    bEditing = false;
    bFreezePending = false;
    bHasFrozenPreview = false;
    bHasQueuedTarget = false;
    bHasPresentationError = false;
    FrozenPreview = FDttKinematicPreview();
    Authoring->ClearCandidate();
    TargetHandle->AttachToComponent(SceneRoot, FAttachmentTransformRules::KeepWorldTransform);
    if (IsValid(CandidateRobot)) { CandidateRobot->SetActorHiddenInGame(true); }
    StatusText = TEXT("SYNTHETIC FIXTURE / local edit cleared - no command");
}

bool ADeferredTeleopAuthoringWorkbench::ResetTargetToSource(FString& OutError)
{
    CancelLocalEdit();
    if (!bReady || !ValidateStage(OutError))
    {
        if (OutError.IsEmpty()) { OutError = TEXT("Initialize a source before resetting the handle"); }
        StatusText = OutError;
        return false;
    }
    FTransform Tool;
    if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
        Authoring->StartToolTransform, Tool, OutError))
    {
        StatusText = OutError; return false;
    }
    // A previous grab may have reparented the handle. Reset restores example ownership.
    TargetHandle->AttachToComponent(SceneRoot, FAttachmentTransformRules::KeepWorldTransform);
    TargetHandle->SetWorldTransform(Tool);
    StatusText = TEXT("SYNTHETIC FIXTURE / ready - select the handle to edit");
    return true;
}

bool ADeferredTeleopAuthoringWorkbench::SetGoalMode(EDttIKMode NewMode, FString& OutError)
{
    OutError.Reset();
    if (NewMode != EDttIKMode::PositionOnly && NewMode != EDttIKMode::PositionPlusApproachAxis)
    {
        OutError = TEXT("Unsupported workbench IK mode");
        return false;
    }
    const bool bWasEditing = bEditing;
    CancelLocalEdit();
    GoalMode = NewMode;
    if (!bReady) { return true; }
    bEditing = bWasEditing;
    return QueueHandle(OutError);
}

void ADeferredTeleopAuthoringWorkbench::HandlePreview(const FDttKinematicPreview& Preview)
{
    if (!bReady || !Authoring->HasCurrentPreview() || !Preview.bValid
        || Preview.GoalId != Authoring->LastValidPreview.GoalId) { return; }
    FString Error;
    // A controller may have written again after release but before the capped solve.
    // Such a late transform must not turn the older queued pose into a frozen selection.
    if (!TargetHandle->GetComponentTransform().Equals(LastQueuedTarget, 1.0e-6))
    {
        CancelLocalEdit();
        StatusText = TEXT("Handle changed after queued input; begin a new edit");
        return;
    }
    TArray<FDttNamedJointPosition> GoalJoints;
    for (const FDeferredTeleopArticulatedJointPosition& Joint : Preview.GoalJointPositions)
    {
        FDttNamedJointPosition Named;
        Named.JointName = FName(*Joint.JointName);
        Named.PositionRadians = Joint.PositionRadians;
        GoalJoints.Add(Named);
    }
    if (!IsValid(CandidateRobot) || !CandidateRobot->ApplyState(GoalJoints, Error))
    {
        bFreezePending = false;
        bHasPresentationError = true;
        StatusText = TEXT("Candidate display failed: ") + Error;
        return;
    }
    CandidateRobot->SetActorHiddenInGame(false);
    if (bFreezePending)
    {
        bFreezePending = false;
        bHasFrozenPreview = Authoring->CopyCurrentPreview(FrozenPreview, Error);
        if (!bHasFrozenPreview) { StatusText = Error; }
    }
}

void ADeferredTeleopAuthoringWorkbench::HandleDiagnostic(const FString& Diagnostic)
{
    if (!bReady || bHasPresentationError) { return; }
    if (!Authoring->HasCurrentPreview() && !Authoring->bHasPendingGoal)
    {
        bFreezePending = false; // failed final solve can never freeze an earlier success
        if (IsValid(CandidateRobot)) { CandidateRobot->SetActorHiddenInGame(true); }
    }
    StatusText = FString(bHasFrozenPreview ? TEXT("FROZEN LOCALLY / ") : TEXT("LOCAL / ")) + Diagnostic;
}

void ADeferredTeleopAuthoringWorkbench::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    FString Error;
    if (bReady && !ValidateStage(Error))
    {
        CancelLocalEdit();
        bReady = false;
        StatusText = Error;
        if (IsValid(ReferenceRobot)) { ReferenceRobot->SetActorHiddenInGame(true); }
    }
    if (bReady && !bEditing && bHasQueuedTarget
        && !TargetHandle->GetComponentTransform().Equals(LastQueuedTarget, 1.0e-6))
    {
        CancelLocalEdit();
        StatusText = TEXT("Handle moved outside an edit; previous selection cleared");
    }
    if (bReady && bEditing && (!bHasQueuedTarget
        || !TargetHandle->GetComponentTransform().Equals(LastQueuedTarget, 1.0e-6)))
    {
        QueueHandle(Error);
    }
    if (bDrawDevelopmentOverlay) { DrawOverlay(); }
}

void ADeferredTeleopAuthoringWorkbench::DrawOverlay()
{
    if (GetWorld() == nullptr) { return; }
    // Debug drawing is for Editor/Development only. The Blueprint UX can replace this renderer.
    DrawDebugString(GetWorld(), FVector(0.0, 0.0, 55.0),
        TEXT("SYNTHETIC FIXTURE REPLAY / NO NETWORK / NO HARDWARE\n") + StatusText,
        nullptr, FColor::White, 0.0F, false);
    if (!bReady) { return; }
    const FTransform Handle = TargetHandle->GetComponentTransform();
    if (!Handle.IsValid()) { return; }
    DrawDebugSphere(GetWorld(), Handle.GetLocation(), 1.5F, 12, FColor::Cyan, false, 0.0F, 0, 0.15F);
    DrawDebugDirectionalArrow(GetWorld(), Handle.GetLocation(),
        Handle.GetLocation() + Handle.GetUnitAxis(EAxis::Z) * 6.0, 1.0F, FColor::Cyan, false, 0.0F);
    const FDttKinematicPreview& Preview = Authoring->LastValidPreview;
    if (!Preview.bValid || Preview.Samples.IsEmpty()) { return; }
    DrawDebugString(GetWorld(), FVector(0.0, 0.0, 48.0),
        FString::Printf(TEXT("source r%d / %.1f mm / %.2f deg / %.2f ms / %s"),
            Preview.SourceReference.Evidence.WorldRevision,
            Authoring->LastIKResult.PositionResidualMetres * 1000.0,
            FMath::RadiansToDegrees(Authoring->LastIKResult.ApproachResidualRadians),
            Authoring->LastSolveMilliseconds,
            Authoring->HasCurrentPreview() ? TEXT("current local") : TEXT("OLD DRAWING")),
        nullptr, FColor::White, 0.0F, false);
    const FColor Color = Authoring->HasCurrentPreview() ? FColor(40, 130, 255) : FColor(110, 110, 110);
    FVector Previous = FVector::ZeroVector;
    for (int32 Index = 0; Index < Preview.Samples.Num(); ++Index)
    {
        const FDttKinematicPreviewSample& Sample = Preview.Samples[Index];
        FTransform Tool; FString Error;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(Sample.ToolTransform, Tool, Error)) { return; }
        const FVector Point = Tool.GetLocation();
        if (Index > 0) { DrawDebugLine(GetWorld(), Previous, Point, Color, false, 0.0F, 0, 0.2F); }
        // At most five labels, based on sample index. Values retain their actual sample times.
        const int32 Stride = FMath::Max(1, (Preview.Samples.Num() - 1 + 3) / 4);
        if (Index % Stride == 0 || Index == Preview.Samples.Num() - 1)
        {
            DrawDebugString(GetWorld(), Point + FVector(0, 0, 2),
                FString::Printf(TEXT("+%.2fs (preview)"), Sample.TimeSeconds), nullptr, Color, 0.0F, false);
        }
        Previous = Point;
    }
    DrawDebugSphere(GetWorld(), Previous, 0.7F, 8, Color, false, 0.0F, 0, 0.15F);
}

void ADeferredTeleopAuthoringWorkbench::DestroyOwnedRobots()
{
    if (IsValid(ReferenceRobot) && ReferenceRobot->GetOwner() == this) { ReferenceRobot->Destroy(); }
    if (IsValid(CandidateRobot) && CandidateRobot->GetOwner() == this) { CandidateRobot->Destroy(); }
    ReferenceRobot = nullptr;
    CandidateRobot = nullptr;
}

void ADeferredTeleopAuthoringWorkbench::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    CancelLocalEdit();
    bReady = false;
    Authoring->OnPreviewUpdated.RemoveDynamic(this, &ADeferredTeleopAuthoringWorkbench::HandlePreview);
    Authoring->OnAuthoringDiagnostic.RemoveDynamic(this, &ADeferredTeleopAuthoringWorkbench::HandleDiagnostic);
    Authoring->RemoveTickPrerequisiteActor(this);
    DestroyOwnedRobots();
    Super::EndPlay(EndPlayReason);
}
