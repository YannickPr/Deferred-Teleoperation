#include "DeferredTeleopStateVisualizationActor.h"

#include "Camera/CameraComponent.h"
#include "Camera/PlayerCameraManager.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/PointLightComponent.h"
#include "Components/SceneComponent.h"
#include "Components/SplineComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/TextRenderComponent.h"
#include "DeferredTeleopMissionClientComponent.h"
#include "DeferredTeleopVisualizationLibrary.h"
#include "Engine/StaticMesh.h"
#include "Engine/TextureRenderTarget2D.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Pawn.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "Kismet/KismetRenderingLibrary.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "Misc/Parse.h"
#include "UObject/ConstructorHelpers.h"
#include "UnrealClient.h"

namespace
{
FString ProvenanceLabel(const EDeferredTeleopProvenance Provenance)
{
    switch (Provenance)
    {
    case EDeferredTeleopProvenance::Measured:
        return TEXT("MEASURED");
    case EDeferredTeleopProvenance::Fused:
        return TEXT("FUSED");
    case EDeferredTeleopProvenance::OperatorAsserted:
        return TEXT("OPERATOR_ASSERTED");
    case EDeferredTeleopProvenance::Inferred:
        return TEXT("INFERRED");
    case EDeferredTeleopProvenance::Predicted:
        return TEXT("PREDICTED");
    case EDeferredTeleopProvenance::Simulated:
        return TEXT("SIMULATED");
    default:
        return TEXT("UNKNOWN");
    }
}

FString ConnectionStateLabel(const EDeferredTeleopConnectionState State)
{
    switch (State)
    {
    case EDeferredTeleopConnectionState::Connected:
        return TEXT("CONNECTED");
    case EDeferredTeleopConnectionState::Connecting:
        return TEXT("CONNECTING");
    default:
        return TEXT("DISCONNECTED");
    }
}

double EvidenceAgeSeconds(const FDeferredTeleopEvidence& Evidence)
{
    return FMath::Max(0.0, (FDateTime::UtcNow() - Evidence.ProducedAt).GetTotalSeconds());
}

FVector PresentationOffset(const EDeferredTeleopTrajectorySource Source, const ADeferredTeleopStateVisualizationActor& Actor)
{
    return Source == EDeferredTeleopTrajectorySource::ConfirmedState
        ? Actor.ConfirmedPresentationOffset
        : Actor.ArrivalPresentationOffset;
}
}

ADeferredTeleopStateVisualizationActor::ADeferredTeleopStateVisualizationActor()
{
    PrimaryActorTick.bCanEverTick = true;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
    SetRootComponent(SceneRoot);
    MissionClient = CreateDefaultSubobject<UDeferredTeleopMissionClientComponent>(TEXT("MissionClient"));

    DemoCamera = CreateDefaultSubobject<UCameraComponent>(TEXT("DemoCamera"));
    DemoCamera->SetupAttachment(SceneRoot);
    DemoCamera->SetRelativeLocation(FVector(-350.0, 0.0, 120.0));
    DemoCamera->SetRelativeRotation(FRotator(-10.0, 0.0, 0.0));
    DemoCamera->SetActive(true);

    EvidenceCamera = CreateDefaultSubobject<USceneCaptureComponent2D>(TEXT("EvidenceCamera"));
    EvidenceCamera->SetupAttachment(SceneRoot);
    EvidenceCamera->SetRelativeLocation(FVector(-350.0, 0.0, 120.0));
    EvidenceCamera->SetRelativeRotation(FRotator(-10.0, 0.0, 0.0));
    EvidenceCamera->FOVAngle = 90.0F;
    EvidenceCamera->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    EvidenceCamera->bCaptureEveryFrame = false;
    EvidenceCamera->bCaptureOnMovement = false;

    DemoLight = CreateDefaultSubobject<UPointLightComponent>(TEXT("DemoLight"));
    DemoLight->SetupAttachment(SceneRoot);
    DemoLight->SetRelativeLocation(FVector(-100.0, 0.0, 240.0));
    DemoLight->SetIntensity(10000.0F);
    DemoLight->SetAttenuationRadius(2000.0F);
    DemoLight->SetCastShadows(false);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeFinder(
        TEXT("/Engine/BasicShapes/Cube.Cube"));
    CubeMesh = CubeFinder.Object;

    ConfirmedMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("ConfirmedState"));
    ArrivalMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("ArrivalBelief"));
    TargetMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("TargetBranch"));
    TrajectoryLine = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("TrajectoryLine"));
    TrajectoryMarker = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("TrajectoryMarker"));

    const TArray<UStaticMeshComponent*> Meshes = {
        ConfirmedMesh,
        ArrivalMesh,
        TargetMesh,
        TrajectoryLine,
        TrajectoryMarker,
    };
    for (UStaticMeshComponent* Mesh : Meshes)
    {
        Mesh->SetupAttachment(SceneRoot);
        Mesh->SetStaticMesh(CubeMesh);
        Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Mesh->SetMobility(EComponentMobility::Movable);
        Mesh->SetGenerateOverlapEvents(false);
        Mesh->SetCastShadow(false);
        Mesh->SetVisibility(false);
    }
    ConfirmedMesh->SetRelativeScale3D(FVector(0.35));
    ArrivalMesh->SetRelativeScale3D(FVector(0.32));
    TargetMesh->SetRelativeScale3D(FVector(0.29));
    TrajectoryMarker->SetRelativeScale3D(FVector(0.08));

    ConfirmedLabel = CreateDefaultSubobject<UTextRenderComponent>(TEXT("ConfirmedLabel"));
    ArrivalLabel = CreateDefaultSubobject<UTextRenderComponent>(TEXT("ArrivalLabel"));
    TargetLabel = CreateDefaultSubobject<UTextRenderComponent>(TEXT("TargetLabel"));
    ConnectionLabel = CreateDefaultSubobject<UTextRenderComponent>(TEXT("ConnectionLabel"));
    const TArray<UTextRenderComponent*> Labels = {
        ConfirmedLabel,
        ArrivalLabel,
        TargetLabel,
        ConnectionLabel,
    };
    for (UTextRenderComponent* Label : Labels)
    {
        Label->SetupAttachment(SceneRoot);
        Label->SetHorizontalAlignment(EHTA_Center);
        Label->SetWorldSize(8.0F);
        Label->SetTextRenderColor(FColor::White);
        Label->SetRelativeRotation(FRotator(0.0, 180.0, 0.0));
    }
    ConnectionLabel->SetWorldSize(12.0F);

    TrajectorySpline = CreateDefaultSubobject<USplineComponent>(TEXT("TrajectorySamples"));
    TrajectorySpline->SetupAttachment(SceneRoot);
    TrajectorySpline->ClearSplinePoints(false);
}

void ADeferredTeleopStateVisualizationActor::BeginPlay()
{
    Super::BeginPlay();
    bEvidenceCaptureMode = FParse::Param(FCommandLine::Get(), TEXT("DttCaptureEvidence"));
    if (bEvidenceCaptureMode)
    {
        EvidenceRenderTarget = NewObject<UTextureRenderTarget2D>(this, TEXT("M1EvidenceRenderTarget"));
        EvidenceRenderTarget->ClearColor = FLinearColor(0.008F, 0.012F, 0.025F, 1.0F);
        EvidenceRenderTarget->RenderTargetFormat = ETextureRenderTargetFormat::RTF_RGBA8;
        EvidenceRenderTarget->InitCustomFormat(1280, 720, PF_B8G8R8A8, true);
        EvidenceRenderTarget->UpdateResourceImmediate(true);
        EvidenceCamera->TextureTarget = EvidenceRenderTarget;
        EvidenceCamera->bCaptureEveryFrame = true;
    }
    if (ConfirmedMaterial == nullptr)
    {
        ConfirmedMaterial = LoadObject<UMaterialInterface>(
            nullptr, TEXT("/DeferredTeleop/Materials/M_Confirmed.M_Confirmed"));
    }
    if (ArrivalMaterial == nullptr)
    {
        ArrivalMaterial = LoadObject<UMaterialInterface>(
            nullptr, TEXT("/DeferredTeleop/Materials/M_Arrival.M_Arrival"));
    }
    if (TargetMaterial == nullptr)
    {
        TargetMaterial = LoadObject<UMaterialInterface>(
            nullptr, TEXT("/DeferredTeleop/Materials/M_Target.M_Target"));
    }
    if (TrajectoryMaterial == nullptr)
    {
        TrajectoryMaterial = LoadObject<UMaterialInterface>(
            nullptr, TEXT("/DeferredTeleop/Materials/M_Trajectory.M_Trajectory"));
    }
    MissionClient->OnMissionViewStateUpdated.AddDynamic(
        this,
        &ADeferredTeleopStateVisualizationActor::HandleMissionViewStateUpdated);
    MissionClient->OnMissionConnectionChanged.AddDynamic(
        this,
        &ADeferredTeleopStateVisualizationActor::HandleMissionConnectionChanged);

    if (ConfirmedMaterial != nullptr)
    {
        ConfirmedMaterial->EnsureIsComplete();
        ConfirmedMesh->SetMaterial(0, ConfirmedMaterial);
    }
    if (ArrivalMaterial != nullptr)
    {
        ArrivalMaterial->EnsureIsComplete();
        ArrivalMesh->SetMaterial(0, ArrivalMaterial);
    }
    if (TargetMaterial != nullptr)
    {
        TargetMaterial->EnsureIsComplete();
        TargetMesh->SetMaterial(0, TargetMaterial);
    }
    if (TrajectoryMaterial != nullptr)
    {
        TrajectoryMaterial->EnsureIsComplete();
        TrajectoryLine->SetMaterial(0, TrajectoryMaterial);
        TrajectoryMarker->SetMaterial(0, TrajectoryMaterial);
    }
    if (bUseAsViewTarget)
    {
        if (APlayerController* PlayerController = GetWorld()->GetFirstPlayerController())
        {
            if (APawn* Pawn = PlayerController->GetPawn())
            {
                Pawn->SetActorHiddenInGame(true);
                Pawn->SetActorEnableCollision(false);
            }
            PlayerController->SetViewTarget(this);
            UE_LOG(
                LogTemp,
                Display,
                TEXT("DTT_M1_CAMERA view-target=%s camera=%s"),
                *GetName(),
                *DemoCamera->GetComponentLocation().ToCompactString());
        }
        else
        {
            UE_LOG(LogTemp, Warning, TEXT("DTT_M1_CAMERA no player controller"));
        }
    }
    UpdateLabels();
}

void ADeferredTeleopStateVisualizationActor::Tick(const float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (bUseAsViewTarget)
    {
        if (APlayerController* PlayerController = GetWorld()->GetFirstPlayerController())
        {
            if (PlayerController->GetViewTarget() != this)
            {
                PlayerController->SetViewTarget(this);
            }
        }
    }
    UpdateLabels();

    const double NowMonotonic = FPlatformTime::Seconds();
    if (bEvidenceCaptureMode
        && !bEvidenceCaptureRequested
        && EvidenceCaptureAtMonotonicSeconds > 0.0
        && NowMonotonic >= EvidenceCaptureAtMonotonicSeconds)
    {
        const FString EvidencePath =
            FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("Screenshots/M1_DeferredStates.png"));
        IFileManager::Get().MakeDirectory(*FPaths::GetPath(EvidencePath), true);
        UKismetRenderingLibrary::ExportRenderTarget(
            this,
            EvidenceRenderTarget,
            FPaths::GetPath(EvidencePath),
            FPaths::GetCleanFilename(EvidencePath));
        bEvidenceCaptureRequested = true;
        APlayerController* PlayerController = GetWorld()->GetFirstPlayerController();
        const APlayerCameraManager* CameraManager =
            PlayerController != nullptr ? PlayerController->PlayerCameraManager : nullptr;
        FVector2D ConfirmedScreen = FVector2D::ZeroVector;
        FVector2D ArrivalScreen = FVector2D::ZeroVector;
        FVector2D TargetScreen = FVector2D::ZeroVector;
        const bool bConfirmedProjected = PlayerController != nullptr
            && PlayerController->ProjectWorldLocationToScreen(
                ConfirmedMesh->GetComponentLocation(), ConfirmedScreen);
        const bool bArrivalProjected = PlayerController != nullptr
            && PlayerController->ProjectWorldLocationToScreen(
                ArrivalMesh->GetComponentLocation(), ArrivalScreen);
        const bool bTargetProjected = PlayerController != nullptr
            && PlayerController->ProjectWorldLocationToScreen(
                TargetMesh->GetComponentLocation(), TargetScreen);
        UE_LOG(
            LogTemp,
            Display,
            TEXT("DTT_M1_EVIDENCE_SCREENSHOT %s confirmed=%s arrival=%s target=%s "
                 "view-target=%s camera-location=%s camera-rotation=%s"),
            *EvidencePath,
            ConfirmedMesh->IsVisible() ? TEXT("visible") : TEXT("hidden"),
            ArrivalMesh->IsVisible() ? TEXT("visible") : TEXT("hidden"),
            TargetMesh->IsVisible() ? TEXT("visible") : TEXT("hidden"),
            PlayerController != nullptr && PlayerController->GetViewTarget() != nullptr
                ? *PlayerController->GetViewTarget()->GetName()
                : TEXT("none"),
            CameraManager != nullptr
                ? *CameraManager->GetCameraLocation().ToCompactString()
                : TEXT("none"),
            CameraManager != nullptr
                ? *CameraManager->GetCameraRotation().ToCompactString()
                : TEXT("none"));
        UE_LOG(
            LogTemp,
            Display,
            TEXT("DTT_M1_PROJECTION confirmed=%s:%s arrival=%s:%s target=%s:%s materials=%s|%s|%s"),
            bConfirmedProjected ? TEXT("yes") : TEXT("no"),
            *ConfirmedScreen.ToString(),
            bArrivalProjected ? TEXT("yes") : TEXT("no"),
            *ArrivalScreen.ToString(),
            bTargetProjected ? TEXT("yes") : TEXT("no"),
            *TargetScreen.ToString(),
            *GetPathNameSafe(ConfirmedMesh->GetMaterial(0)),
            *GetPathNameSafe(ArrivalMesh->GetMaterial(0)),
            *GetPathNameSafe(TargetMesh->GetMaterial(0)));
    }
    if (bEvidenceCaptureMode
        && !bEvidenceExitRequested
        && EvidenceExitAtMonotonicSeconds > 0.0
        && NowMonotonic >= EvidenceExitAtMonotonicSeconds)
    {
        bEvidenceExitRequested = true;
        FPlatformMisc::RequestExit(false, TEXT("DeferredTeleop M1 evidence captured"));
    }

    if (!bHasCurrentView || CurrentView.TrajectoryForecasts.Num() < 2)
    {
        return;
    }
    const FDeferredTeleopTimedTrajectorySample& Start = CurrentView.TrajectoryForecasts[0];
    const FDeferredTeleopTimedTrajectorySample& End = CurrentView.TrajectoryForecasts.Last();
    const double Duration = (End.SampleTime - Start.SampleTime).GetTotalSeconds();
    const double Elapsed = (FDateTime::UtcNow() - Start.SampleTime).GetTotalSeconds();
    const float Alpha = Duration > UE_DOUBLE_KINDA_SMALL_NUMBER
        ? static_cast<float>(FMath::Clamp(Elapsed / Duration, 0.0, 1.0))
        : 1.0F;
    const FVector StartLocation =
        UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(Start.Pose).GetLocation()
        + PresentationOffset(Start.Source, *this);
    const FVector EndLocation =
        UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(End.Pose).GetLocation()
        + PresentationOffset(End.Source, *this);
    TrajectoryMarker->SetRelativeLocation(FMath::Lerp(StartLocation, EndLocation, Alpha));
}

void ADeferredTeleopStateVisualizationActor::HandleMissionViewStateUpdated(
    const FDeferredTeleopMissionViewState& ViewState)
{
    const bool bShouldLog = !bHasCurrentView
        || CurrentView.Status.TerminalState != ViewState.Status.TerminalState
        || CurrentView.ConfirmedState.bAvailable != ViewState.ConfirmedState.bAvailable
        || CurrentView.ArrivalBelief.bAvailable != ViewState.ArrivalBelief.bAvailable
        || CurrentView.TargetBranch.bAvailable != ViewState.TargetBranch.bAvailable;
    CurrentView = ViewState;
    bHasCurrentView = true;
    if (bShouldLog)
    {
        UE_LOG(
            LogTemp,
            Display,
            TEXT("DTT_M1_VIEW sequence=%d confirmed=%s arrival=%s target=%s terminal=%s"),
            CurrentView.SourceSequence,
            CurrentView.ConfirmedState.bAvailable ? TEXT("yes") : TEXT("no"),
            CurrentView.ArrivalBelief.bAvailable ? TEXT("yes") : TEXT("no"),
            CurrentView.TargetBranch.bAvailable ? TEXT("yes") : TEXT("no"),
            *CurrentView.Status.TerminalState);
    }
    if (bEvidenceCaptureMode
        && EvidenceCaptureAtMonotonicSeconds <= 0.0
        && CurrentView.Status.TerminalState == TEXT("SUCCEEDED"))
    {
        const double NowMonotonic = FPlatformTime::Seconds();
        EvidenceCaptureAtMonotonicSeconds = NowMonotonic + 3.0;
        EvidenceExitAtMonotonicSeconds = NowMonotonic + 4.5;
    }
    ApplyStateTransforms();
    UpdateTrajectory();
    UpdateLabels();
}

void ADeferredTeleopStateVisualizationActor::HandleMissionConnectionChanged(
    const EDeferredTeleopConnectionState NewState)
{
    (void)NewState;
    UpdateLabels();
}

void ADeferredTeleopStateVisualizationActor::ApplyStateTransforms()
{
    const auto Apply = [this](
                           UStaticMeshComponent* Mesh,
                           UTextRenderComponent* Label,
                           const bool bAvailable,
                           const FDeferredTeleopPose& Pose,
                           const FVector& Offset)
    {
        Mesh->SetVisibility(bAvailable);
        Label->SetVisibility(bAvailable);
        if (!bAvailable)
        {
            return;
        }
        const FTransform Transform =
            UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(Pose);
        Mesh->SetRelativeLocationAndRotation(
            Transform.GetLocation() + Offset,
            Transform.GetRotation());
        Label->SetRelativeLocation(Transform.GetLocation() + Offset + FVector(0.0, 0.0, 55.0));
    };

    Apply(
        ConfirmedMesh,
        ConfirmedLabel,
        CurrentView.ConfirmedState.bAvailable,
        CurrentView.ConfirmedState.Pose,
        ConfirmedPresentationOffset);
    Apply(
        ArrivalMesh,
        ArrivalLabel,
        CurrentView.ArrivalBelief.bAvailable,
        CurrentView.ArrivalBelief.Pose,
        ArrivalPresentationOffset);
    Apply(
        TargetMesh,
        TargetLabel,
        CurrentView.TargetBranch.bAvailable,
        CurrentView.TargetBranch.Pose,
        TargetPresentationOffset);
}

void ADeferredTeleopStateVisualizationActor::UpdateTrajectory()
{
    TrajectorySpline->ClearSplinePoints(false);
    for (const FDeferredTeleopTimedTrajectorySample& Sample : CurrentView.TrajectoryForecasts)
    {
        const FVector Location =
            UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(Sample.Pose).GetLocation()
            + PresentationOffset(Sample.Source, *this);
        TrajectorySpline->AddSplinePoint(Location, ESplineCoordinateSpace::Local, false);
    }
    TrajectorySpline->UpdateSpline();

    const bool bShow = CurrentView.TrajectoryForecasts.Num() >= 2;
    TrajectoryLine->SetVisibility(bShow);
    TrajectoryMarker->SetVisibility(bShow);
    if (!bShow)
    {
        return;
    }
    const FVector Start = TrajectorySpline->GetLocationAtSplinePoint(0, ESplineCoordinateSpace::Local);
    const FVector End = TrajectorySpline->GetLocationAtSplinePoint(
        CurrentView.TrajectoryForecasts.Num() - 1,
        ESplineCoordinateSpace::Local);
    const FVector Difference = End - Start;
    TrajectoryLine->SetRelativeLocation((Start + End) * 0.5);
    TrajectoryLine->SetRelativeRotation(Difference.Rotation());
    TrajectoryLine->SetRelativeScale3D(
        FVector(FMath::Max(Difference.Length() / 100.0, 0.01), 0.025, 0.025));
    TrajectoryMarker->SetRelativeLocation(Start);
}

void ADeferredTeleopStateVisualizationActor::UpdateLabels()
{
    const float ReceiptAge = MissionClient->GetLastValidStateAgeSeconds();
    const bool bSocketConnected =
        MissionClient->ConnectionState == EDeferredTeleopConnectionState::Connected;
    ConnectionLabel->SetText(FText::FromString(FString::Printf(
        TEXT("Unreal->Mission %s | Mission->Field %s | receipt age %.1fs%s"),
        *ConnectionStateLabel(MissionClient->ConnectionState),
        bHasCurrentView ? *ConnectionStateLabel(CurrentView.MissionToField) : TEXT("UNKNOWN"),
        FMath::Max(ReceiptAge, 0.0F),
        bSocketConnected ? TEXT("") : TEXT(" | STALE"))));
    ConnectionLabel->SetTextRenderColor(bSocketConnected ? FColor::Green : FColor::Red);
    ConnectionLabel->SetRelativeLocation(FVector(0.0, 0.0, 140.0));

    if (!bHasCurrentView)
    {
        return;
    }
    if (CurrentView.ConfirmedState.bAvailable)
    {
        ConfirmedLabel->SetText(FText::FromString(FString::Printf(
            TEXT("CONFIRMED\n%s | age %.1fs"),
            *ProvenanceLabel(CurrentView.ConfirmedState.Evidence.Provenance),
            EvidenceAgeSeconds(CurrentView.ConfirmedState.Evidence))));
        ConfirmedLabel->SetTextRenderColor(FColor(180, 180, 180));
    }
    if (CurrentView.ArrivalBelief.bAvailable)
    {
        const double Horizon =
            (CurrentView.ArrivalBelief.PredictedFor - FDateTime::UtcNow()).GetTotalSeconds();
        ArrivalLabel->SetText(FText::FromString(FString::Printf(
            TEXT("ARRIVAL BELIEF\n%s | horizon %+.1fs\nage %.1fs | %s"),
            *ProvenanceLabel(CurrentView.ArrivalBelief.Evidence.Provenance),
            Horizon,
            EvidenceAgeSeconds(CurrentView.ArrivalBelief.Evidence),
            *CurrentView.ArrivalBelief.Evidence.ModelVersion)));
        ArrivalLabel->SetTextRenderColor(FColor::White);
    }
    if (CurrentView.TargetBranch.bAvailable)
    {
        TargetLabel->SetText(FText::FromString(FString::Printf(
            TEXT("TARGET BRANCH\n%s\nCONDITIONAL%s"),
            *ProvenanceLabel(CurrentView.TargetBranch.Evidence.Provenance),
            CurrentView.Status.TerminalState == TEXT("SUCCEEDED")
                ? TEXT(" | RECONCILED")
                : TEXT(""))));
        TargetLabel->SetTextRenderColor(FColor(40, 120, 255));
    }
}
