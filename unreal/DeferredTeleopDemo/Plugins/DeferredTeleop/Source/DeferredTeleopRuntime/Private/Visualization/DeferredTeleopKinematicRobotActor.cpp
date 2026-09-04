#include "Visualization/DeferredTeleopKinematicRobotActor.h"

#include "Components/ArrowComponent.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/TextRenderComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Math/UnrealMathUtility.h"
#include "UObject/ConstructorHelpers.h"

namespace DeferredTeleop::KinematicRobotActor::Private
{
struct FTopologyBuffers
{
    TArray<TObjectPtr<USceneComponent>> LinkFrames;
    TArray<TObjectPtr<UStaticMeshComponent>> LinkMarkers;
    TArray<TObjectPtr<UArrowComponent>> LinkXAxis;
    TArray<TObjectPtr<UArrowComponent>> LinkYAxis;
    TArray<TObjectPtr<UArrowComponent>> LinkZAxis;
    TArray<TObjectPtr<UTextRenderComponent>> LinkLabels;
    TArray<TObjectPtr<UStaticMeshComponent>> LinkSegments;
    TArray<TObjectPtr<USceneComponent>> ToolFrames;
    TArray<TObjectPtr<UStaticMeshComponent>> ToolMarkers;
    TArray<TObjectPtr<UTextRenderComponent>> ToolLabels;
    TArray<TObjectPtr<UArrowComponent>> JointAxes;
};

bool Fail(FString& OutError, const FString& Message)
{
    OutError = Message;
    return false;
}

template <typename T>
T* AddRuntimeComponent(
    AActor* Owner,
    USceneComponent* AttachParent,
    const FName ComponentName,
    FString& OutError)
{
    T* Component = NewObject<T>(Owner, ComponentName);
    if (Component == nullptr)
    {
        Fail(
            OutError,
            FString::Printf(
                TEXT("could not create runtime component %s"),
                *ComponentName.ToString()));
        return nullptr;
    }

    Owner->AddInstanceComponent(Component);
    if (AttachParent != nullptr)
    {
        Component->SetupAttachment(AttachParent);
    }
    if (UWorld* World = Owner->GetWorld())
    {
        Component->RegisterComponentWithWorld(World);
    }
    return Component;
}

template <typename T>
void DestroyComponents(TArray<TObjectPtr<T>>& Components)
{
    for (TObjectPtr<T> Component : Components)
    {
        if (Component != nullptr)
        {
            Component->DestroyComponent();
        }
    }
    Components.Reset();
}

void DestroyBuffers(FTopologyBuffers& Buffers)
{
    // Destroy children before their link/tool frame parents.  DestroyComponent
    // is idempotent, but this order keeps the component tree valid while it is
    // being torn down.
    DestroyComponents(Buffers.LinkLabels);
    DestroyComponents(Buffers.LinkXAxis);
    DestroyComponents(Buffers.LinkYAxis);
    DestroyComponents(Buffers.LinkZAxis);
    DestroyComponents(Buffers.LinkMarkers);
    DestroyComponents(Buffers.LinkSegments);
    DestroyComponents(Buffers.ToolLabels);
    DestroyComponents(Buffers.ToolMarkers);
    DestroyComponents(Buffers.JointAxes);
    DestroyComponents(Buffers.LinkFrames);
    DestroyComponents(Buffers.ToolFrames);
}

void ConfigurePrimitive(
    UStaticMeshComponent* Component,
    UStaticMesh* Mesh,
    UMaterialInterface* Material,
    const FVector& RelativeScale)
{
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->SetGenerateOverlapEvents(false);
    Component->SetCastShadow(false);
    Component->SetRelativeScale3D(RelativeScale);
    if (Mesh != nullptr)
    {
        Component->SetStaticMesh(Mesh);
    }
    if (Material != nullptr)
    {
        Component->SetMaterial(0, Material);
    }
}

void ConfigureArrow(UArrowComponent* Component, const FColor& Color)
{
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->SetGenerateOverlapEvents(false);
    Component->SetCastShadow(false);
    Component->SetArrowFColor(Color);
    Component->SetArrowSize(0.35F);
}

void ConfigureLabel(
    UTextRenderComponent* Component,
    const FColor& Color,
    const FVector& RelativeLocation)
{
    Component->SetMobility(EComponentMobility::Movable);
    Component->SetHorizontalAlignment(EHTA_Center);
    Component->SetWorldSize(7.0F);
    Component->SetTextRenderColor(Color);
    Component->SetRelativeLocation(RelativeLocation);
    Component->SetRelativeRotation(FRotator(0.0F, 180.0F, 0.0F));
    Component->SetCastShadow(false);
}

FName ComponentName(const TCHAR* Prefix, const FName ItemName, const int32 Generation)
{
    return FName(*FString::Printf(
        TEXT("%s_%s_%d"),
        Prefix,
        *ItemName.ToString(),
        Generation));
}

FName IndexedComponentName(const TCHAR* Prefix, const int32 Index, const int32 Generation)
{
    return FName(*FString::Printf(TEXT("%s_%d_%d"), Prefix, Index, Generation));
}

bool IsFiniteTransform(const FTransform& Transform)
{
    const FVector Location = Transform.GetLocation();
    const FVector Scale = Transform.GetScale3D();
    const FQuat Rotation = Transform.GetRotation();
    return FMath::IsFinite(Location.X) && FMath::IsFinite(Location.Y)
        && FMath::IsFinite(Location.Z) && FMath::IsFinite(Scale.X)
        && FMath::IsFinite(Scale.Y) && FMath::IsFinite(Scale.Z)
        && Rotation.IsNormalized()
        && FMath::IsFinite(Rotation.X) && FMath::IsFinite(Rotation.Y)
        && FMath::IsFinite(Rotation.Z) && FMath::IsFinite(Rotation.W);
}

bool BuildTopology(
    ADeferredTeleopKinematicRobotActor* Owner,
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    USceneComponent* SceneRoot,
    UStaticMesh* CubeMesh,
    UStaticMesh* SphereMesh,
    UMaterialInterface* LinkMaterial,
    UMaterialInterface* ToolMaterial,
    UMaterialInterface* SegmentMaterial,
    const FColor& DebugXAxisColor,
    const FColor& DebugYAxisColor,
    const FColor& DebugZAxisColor,
    const FColor& DebugJointAxisColor,
    const FColor& DebugToolColor,
    const int32 Generation,
    FTopologyBuffers& OutBuffers,
    FString& OutError)
{
    (void)Model;
    OutError.Reset();
    OutBuffers.LinkFrames.Reserve(Description.Links.Num());
    OutBuffers.LinkMarkers.Reserve(Description.Links.Num());
    OutBuffers.LinkXAxis.Reserve(Description.Links.Num());
    OutBuffers.LinkYAxis.Reserve(Description.Links.Num());
    OutBuffers.LinkZAxis.Reserve(Description.Links.Num());
    OutBuffers.LinkLabels.Reserve(Description.Links.Num());
    OutBuffers.LinkSegments.SetNum(Description.Links.Num());
    OutBuffers.ToolFrames.Reserve(Description.ToolFrames.Num());
    OutBuffers.ToolMarkers.Reserve(Description.ToolFrames.Num());
    OutBuffers.ToolLabels.Reserve(Description.ToolFrames.Num());
    OutBuffers.JointAxes.SetNum(Description.Joints.Num());

    const FQuat XToY = FQuat::FindBetweenVectors(FVector::ForwardVector, FVector::RightVector);
    const FQuat XToZ = FQuat::FindBetweenVectors(FVector::ForwardVector, FVector::UpVector);

    for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
    {
        const FName LinkName = Description.Links[LinkIndex].Name;
        USceneComponent* LinkFrame = AddRuntimeComponent<USceneComponent>(
            Owner,
            SceneRoot,
            ComponentName(TEXT("DttLinkFrame"), LinkName, Generation),
            OutError);
        if (LinkFrame == nullptr)
        {
            DestroyBuffers(OutBuffers);
            return false;
        }
        LinkFrame->SetMobility(EComponentMobility::Movable);
        LinkFrame->SetAbsolute(true, true, true);
        OutBuffers.LinkFrames.Add(LinkFrame);

        UStaticMeshComponent* LinkMarker = AddRuntimeComponent<UStaticMeshComponent>(
            Owner,
            LinkFrame,
            ComponentName(TEXT("DttLinkMarker"), LinkName, Generation),
            OutError);
        if (LinkMarker != nullptr)
        {
            OutBuffers.LinkMarkers.Add(LinkMarker);
        }
        UArrowComponent* XAxis = AddRuntimeComponent<UArrowComponent>(
            Owner,
            LinkFrame,
            ComponentName(TEXT("DttLinkAxisX"), LinkName, Generation),
            OutError);
        if (XAxis != nullptr)
        {
            OutBuffers.LinkXAxis.Add(XAxis);
        }
        UArrowComponent* YAxis = AddRuntimeComponent<UArrowComponent>(
            Owner,
            LinkFrame,
            ComponentName(TEXT("DttLinkAxisY"), LinkName, Generation),
            OutError);
        if (YAxis != nullptr)
        {
            OutBuffers.LinkYAxis.Add(YAxis);
        }
        UArrowComponent* ZAxis = AddRuntimeComponent<UArrowComponent>(
            Owner,
            LinkFrame,
            ComponentName(TEXT("DttLinkAxisZ"), LinkName, Generation),
            OutError);
        if (ZAxis != nullptr)
        {
            OutBuffers.LinkZAxis.Add(ZAxis);
        }
        UTextRenderComponent* Label = AddRuntimeComponent<UTextRenderComponent>(
            Owner,
            LinkFrame,
            ComponentName(TEXT("DttLinkLabel"), LinkName, Generation),
            OutError);
        if (Label != nullptr)
        {
            OutBuffers.LinkLabels.Add(Label);
        }
        if (LinkMarker == nullptr || XAxis == nullptr || YAxis == nullptr || ZAxis == nullptr
            || Label == nullptr)
        {
            DestroyBuffers(OutBuffers);
            return false;
        }

        ConfigurePrimitive(LinkMarker, CubeMesh, LinkMaterial, FVector(0.06F));
        ConfigureArrow(XAxis, DebugXAxisColor);
        ConfigureArrow(YAxis, DebugYAxisColor);
        ConfigureArrow(ZAxis, DebugZAxisColor);
        YAxis->SetRelativeRotation(XToY);
        ZAxis->SetRelativeRotation(XToZ);
        ConfigureLabel(Label, FColor::White, FVector(0.0F, 0.0F, 12.0F));
        Label->SetText(FText::FromName(LinkName));

        LinkMarker->SetVisibility(false);
        XAxis->SetVisibility(false);
        YAxis->SetVisibility(false);
        ZAxis->SetVisibility(false);
        Label->SetVisibility(false);

        const int32 ParentJointIndex = Model.ParentJointByLink.IsValidIndex(LinkIndex)
            ? Model.ParentJointByLink[LinkIndex]
            : INDEX_NONE;
        if (ParentJointIndex != INDEX_NONE)
        {
            UStaticMeshComponent* Segment = AddRuntimeComponent<UStaticMeshComponent>(
                Owner,
                SceneRoot,
                ComponentName(TEXT("DttLinkSegment"), LinkName, Generation),
                OutError);
            if (Segment == nullptr)
            {
                DestroyBuffers(OutBuffers);
                return false;
            }
            OutBuffers.LinkSegments[LinkIndex] = Segment;
            Segment->SetAbsolute(true, true, true);
            ConfigurePrimitive(
                Segment,
                CubeMesh,
                SegmentMaterial,
                FVector(0.01F, 0.025F, 0.025F));
            Segment->SetVisibility(false);
        }
    }

    for (int32 ToolIndex = 0; ToolIndex < Description.ToolFrames.Num(); ++ToolIndex)
    {
        const FDttRobotToolFrameDescription& Tool = Description.ToolFrames[ToolIndex];
        USceneComponent* ToolFrame = AddRuntimeComponent<USceneComponent>(
            Owner,
            SceneRoot,
            ComponentName(TEXT("DttToolFrame"), Tool.Name, Generation),
            OutError);
        UStaticMeshComponent* ToolMarker = AddRuntimeComponent<UStaticMeshComponent>(
            Owner,
            ToolFrame,
            ComponentName(TEXT("DttToolMarker"), Tool.Name, Generation),
            OutError);
        if (ToolFrame != nullptr)
        {
            OutBuffers.ToolFrames.Add(ToolFrame);
        }
        if (ToolMarker != nullptr)
        {
            OutBuffers.ToolMarkers.Add(ToolMarker);
        }
        UTextRenderComponent* ToolLabel = AddRuntimeComponent<UTextRenderComponent>(
            Owner,
            ToolFrame,
            ComponentName(TEXT("DttToolLabel"), Tool.Name, Generation),
            OutError);
        if (ToolLabel != nullptr)
        {
            OutBuffers.ToolLabels.Add(ToolLabel);
        }
        if (ToolFrame == nullptr || ToolMarker == nullptr || ToolLabel == nullptr)
        {
            DestroyBuffers(OutBuffers);
            return false;
        }

        ToolFrame->SetMobility(EComponentMobility::Movable);
        ToolFrame->SetAbsolute(true, true, true);
        ConfigurePrimitive(
            ToolMarker,
            SphereMesh != nullptr ? SphereMesh : CubeMesh,
            ToolMaterial,
            FVector(0.10F));
        ConfigureLabel(ToolLabel, DebugToolColor, FVector(0.0F, 0.0F, 17.0F));
        ToolLabel->SetText(FText::FromString(FString::Printf(TEXT("TOOL %s"), *Tool.Name.ToString())));
        ToolMarker->SetVisibility(false);
        ToolLabel->SetVisibility(false);

    }

    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        UArrowComponent* JointAxis = AddRuntimeComponent<UArrowComponent>(
            Owner,
            SceneRoot,
            IndexedComponentName(TEXT("DttJointAxis"), JointIndex, Generation),
            OutError);
        if (JointAxis == nullptr)
        {
            DestroyBuffers(OutBuffers);
            return false;
        }
        OutBuffers.JointAxes[JointIndex] = JointAxis;
        ConfigureArrow(JointAxis, DebugJointAxisColor);
        JointAxis->SetAbsolute(true, true, true);
        JointAxis->SetVisibility(false);
    }

    return true;
}
} // namespace DeferredTeleop::KinematicRobotActor::Private

namespace DttKinematicRobotPrivate = DeferredTeleop::KinematicRobotActor::Private;

ADeferredTeleopKinematicRobotActor::ADeferredTeleopKinematicRobotActor()
{
    PrimaryActorTick.bCanEverTick = false;

    SceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("SceneRoot"));
    SetRootComponent(SceneRoot);
    SetActorScale3D(FVector::OneVector);

    static ConstructorHelpers::FObjectFinder<UStaticMesh> CubeFinder(
        TEXT("/Engine/BasicShapes/Cube.Cube"));
    CubeMesh = CubeFinder.Object;

    static ConstructorHelpers::FObjectFinder<UStaticMesh> SphereFinder(
        TEXT("/Engine/BasicShapes/Sphere.Sphere"));
    SphereMesh = SphereFinder.Object;
}

bool ADeferredTeleopKinematicRobotActor::InitializeModel(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    FString& OutError)
{
    OutError.Reset();

    FString Error;
    if (!HasUnitActorScale(Error))
    {
        OutError = Error;
        LastError = Error;
        return false;
    }

    FDttValidatedRobotModel CandidateModel;
    if (!DeferredTeleop::Kinematics::ValidateRobotDescription(
            Description,
            CandidateModel,
            Error))
    {
        OutError = Error;
        LastError = Error;
        return false;
    }

    FTransform CandidateRootWorldTransform;
    if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            WorldTransformOfRoot,
            CandidateRootWorldTransform,
            Error))
    {
        OutError = Error;
        LastError = Error;
        return false;
    }

    const bool bSameModel = bModelInitialized
        && AreDescriptionsEqual(CurrentDescription, Description)
        && LinkFrameComponents.Num() == Description.Links.Num();
    const bool bSameRoot = bSameModel
        && AreCanonicalTransformsEqual(
            WorldTransformOfRootCanonical,
            WorldTransformOfRoot);

    if (!bSameModel)
    {
        if (!BuildTopology(Description, CandidateModel, Error))
        {
            OutError = Error;
            LastError = Error;
            return false;
        }
    }

    CurrentDescription = Description;
    ValidatedModel = CandidateModel;
    WorldTransformOfRootCanonical = WorldTransformOfRoot;
    RootWorldRenderTransform = CandidateRootWorldTransform;
    LinkSlotByName = CandidateModel.LinkIndexByName;
    ToolSlotByName = CandidateModel.ToolIndexByName;
    ParentLinkIndexByLink.Init(INDEX_NONE, Description.Links.Num());
    for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
    {
        const int32 ParentJointIndex = CandidateModel.ParentJointByLink.IsValidIndex(LinkIndex)
            ? CandidateModel.ParentJointByLink[LinkIndex]
            : INDEX_NONE;
        if (ParentJointIndex != INDEX_NONE && Description.Joints.IsValidIndex(ParentJointIndex))
        {
            ParentLinkIndexByLink[LinkIndex] = CandidateModel.FindLinkIndex(
                Description.Joints[ParentJointIndex].ParentLink);
        }
    }

    bModelInitialized = true;
    if (!bSameModel || !bSameRoot)
    {
        bHasValidPose = false;
        bLastStateWithinJointLimits = true;
        LastDiagnostics.Reset();
        LastJointPositions.Reset();
        LastLinkWorldTransforms.Reset();
        LastToolWorldTransforms.Reset();
        HidePoseComponents();
    }
    LastError.Reset();
    return true;
}

bool ADeferredTeleopKinematicRobotActor::ApplyState(
    const TArray<FDttNamedJointPosition>& JointPositions,
    FString& OutError)
{
    OutError.Reset();
    auto Reject = [this, &OutError](const FString& Message)
    {
        OutError = Message;
        LastError = Message;
        return false;
    };

    if (!bModelInitialized)
    {
        return Reject(TEXT("cannot apply state before InitializeModel"));
    }

    FString Error;
    if (!HasUnitActorScale(Error))
    {
        return Reject(Error);
    }

    FDttForwardKinematicsResult CandidateResult;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            CurrentDescription,
            WorldTransformOfRootCanonical,
            JointPositions,
            CandidateResult))
    {
        return Reject(
            CandidateResult.ErrorMessage.IsEmpty()
                ? TEXT("forward kinematics rejected the state")
                : CandidateResult.ErrorMessage);
    }
    if (!CandidateResult.bSuccess)
    {
        return Reject(
            CandidateResult.ErrorMessage.IsEmpty()
                ? TEXT("forward kinematics returned an unsuccessful result")
                : CandidateResult.ErrorMessage);
    }

    TArray<FDttCanonicalTransform> CandidateCanonicalLinks;
    CandidateCanonicalLinks.SetNum(CurrentDescription.Links.Num());
    TArray<uint8> LinkProvided;
    LinkProvided.Init(0, CurrentDescription.Links.Num());
    TArray<FTransform> CandidateLinkWorldTransforms;
    CandidateLinkWorldTransforms.SetNum(CurrentDescription.Links.Num());

    for (const FDttNamedCanonicalTransform& NamedTransform : CandidateResult.LinkTransforms)
    {
        const int32* Slot = LinkSlotByName.Find(NamedTransform.Name);
        if (Slot == nullptr || !CandidateCanonicalLinks.IsValidIndex(*Slot))
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned unknown link: %s"),
                *NamedTransform.Name.ToString()));
        }
        if (LinkProvided[*Slot] != 0)
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned duplicate link: %s"),
                *NamedTransform.Name.ToString()));
        }
        if (!NamedTransform.Transform.IsRigid())
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned a non-rigid link transform: %s"),
                *NamedTransform.Name.ToString()));
        }

        FTransform UnrealTransform;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                NamedTransform.Transform,
                UnrealTransform,
                Error))
        {
            return Reject(FString::Printf(
                TEXT("link %s could not be converted to Unreal: %s"),
                *NamedTransform.Name.ToString(),
                *Error));
        }
        if (!DttKinematicRobotPrivate::IsFiniteTransform(UnrealTransform))
        {
            return Reject(FString::Printf(
                TEXT("link %s conversion produced a non-finite transform"),
                *NamedTransform.Name.ToString()));
        }

        CandidateCanonicalLinks[*Slot] = NamedTransform.Transform;
        CandidateLinkWorldTransforms[*Slot] = UnrealTransform;
        LinkProvided[*Slot] = 1;
    }
    for (int32 LinkIndex = 0; LinkIndex < LinkProvided.Num(); ++LinkIndex)
    {
        if (LinkProvided[LinkIndex] == 0)
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics omitted link transform: %s"),
                *CurrentDescription.Links[LinkIndex].Name.ToString()));
        }
    }

    TArray<FTransform> CandidateToolWorldTransforms;
    CandidateToolWorldTransforms.SetNum(CurrentDescription.ToolFrames.Num());
    TArray<uint8> ToolProvided;
    ToolProvided.Init(0, CurrentDescription.ToolFrames.Num());
    for (const FDttNamedCanonicalTransform& NamedTransform : CandidateResult.ToolTransforms)
    {
        const int32* Slot = ToolSlotByName.Find(NamedTransform.Name);
        if (Slot == nullptr || !CandidateToolWorldTransforms.IsValidIndex(*Slot))
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned unknown tool frame: %s"),
                *NamedTransform.Name.ToString()));
        }
        if (ToolProvided[*Slot] != 0)
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned duplicate tool frame: %s"),
                *NamedTransform.Name.ToString()));
        }
        if (!NamedTransform.Transform.IsRigid())
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics returned a non-rigid tool transform: %s"),
                *NamedTransform.Name.ToString()));
        }
        FTransform UnrealTransform;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                NamedTransform.Transform,
                UnrealTransform,
                Error))
        {
            return Reject(FString::Printf(
                TEXT("tool frame %s could not be converted to Unreal: %s"),
                *NamedTransform.Name.ToString(),
                *Error));
        }
        if (!DttKinematicRobotPrivate::IsFiniteTransform(UnrealTransform))
        {
            return Reject(FString::Printf(
                TEXT("tool frame %s conversion produced a non-finite transform"),
                *NamedTransform.Name.ToString()));
        }
        CandidateToolWorldTransforms[*Slot] = UnrealTransform;
        ToolProvided[*Slot] = 1;
    }
    for (int32 ToolIndex = 0; ToolIndex < ToolProvided.Num(); ++ToolIndex)
    {
        if (ToolProvided[ToolIndex] == 0)
        {
            return Reject(FString::Printf(
                TEXT("forward kinematics omitted tool frame: %s"),
                *CurrentDescription.ToolFrames[ToolIndex].Name.ToString()));
        }
    }

    // Derive joint-frame arrows from the already evaluated canonical link
    // transforms.  This adds no tree traversal or joint motion implementation:
    // the shared FK result remains authoritative.
    TArray<FTransform> CandidateJointAxisWorldTransforms;
    CandidateJointAxisWorldTransforms.SetNum(CurrentDescription.Joints.Num());
    TArray<uint8> JointAxisProvided;
    JointAxisProvided.Init(0, CurrentDescription.Joints.Num());
    for (int32 JointIndex = 0; JointIndex < CurrentDescription.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = CurrentDescription.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const int32 ParentLinkIndex = ValidatedModel.FindLinkIndex(Joint.ParentLink);
        if (!CandidateCanonicalLinks.IsValidIndex(ParentLinkIndex))
        {
            return Reject(FString::Printf(
                TEXT("joint %s references an unavailable parent link"),
                *Joint.Name.ToString()));
        }
        const FDttCanonicalTransform JointFrameCanonical =
            CandidateCanonicalLinks[ParentLinkIndex] * Joint.ParentToJoint;
        const FQuat4d AxisRotation = FQuat4d::FindBetweenVectors(
            FVector3d(1.0, 0.0, 0.0),
            Joint.AxisJointFrame.ToVector3d());
        const FDttCanonicalTransform JointAxisCanonical =
            JointFrameCanonical
            * FDttCanonicalTransform::FromTranslationRotation(
                FVector3d::ZeroVector,
                AxisRotation);
        FTransform JointAxisWorldTransform;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                JointAxisCanonical,
                JointAxisWorldTransform,
                Error))
        {
            return Reject(FString::Printf(
                TEXT("joint %s frame could not be converted to Unreal: %s"),
                *Joint.Name.ToString(),
                *Error));
        }

        if (!DttKinematicRobotPrivate::IsFiniteTransform(JointAxisWorldTransform))
        {
            return Reject(FString::Printf(
                TEXT("joint %s axis conversion produced a non-finite transform"),
                *Joint.Name.ToString()));
        }
        CandidateJointAxisWorldTransforms[JointIndex] = JointAxisWorldTransform;
        JointAxisProvided[JointIndex] = 1;
    }

    TArray<FTransform> CandidateSegmentWorldTransforms;
    CandidateSegmentWorldTransforms.SetNum(LinkSegmentComponents.Num());
    TArray<uint8> SegmentProvided;
    SegmentProvided.Init(0, LinkSegmentComponents.Num());
    for (int32 LinkIndex = 0; LinkIndex < LinkSegmentComponents.Num(); ++LinkIndex)
    {
        if (LinkSegmentComponents[LinkIndex] == nullptr)
        {
            continue;
        }
        const int32 ParentLinkIndex = ParentLinkIndexByLink.IsValidIndex(LinkIndex)
            ? ParentLinkIndexByLink[LinkIndex]
            : INDEX_NONE;
        if (!CandidateLinkWorldTransforms.IsValidIndex(ParentLinkIndex))
        {
            return Reject(FString::Printf(
                TEXT("link %s has no valid parent transform for its segment"),
                *CurrentDescription.Links[LinkIndex].Name.ToString()));
        }
        const FVector Start = CandidateLinkWorldTransforms[ParentLinkIndex].GetLocation();
        const FVector End = CandidateLinkWorldTransforms[LinkIndex].GetLocation();
        const FVector Difference = End - Start;
        const float Length = Difference.Size();
        if (!FMath::IsFinite(Length)
            || !FMath::IsFinite(Start.X) || !FMath::IsFinite(Start.Y)
            || !FMath::IsFinite(Start.Z) || !FMath::IsFinite(End.X)
            || !FMath::IsFinite(End.Y) || !FMath::IsFinite(End.Z))
        {
            return Reject(FString::Printf(
                TEXT("link %s segment has a non-finite endpoint"),
                *CurrentDescription.Links[LinkIndex].Name.ToString()));
        }

        FTransform SegmentTransform = FTransform::Identity;
        SegmentTransform.SetLocation((Start + End) * 0.5F);
        if (Length > UE_KINDA_SMALL_NUMBER)
        {
            SegmentTransform.SetRotation(Difference.GetSafeNormal().Rotation().Quaternion());
        }
        SegmentTransform.SetScale3D(FVector(FMath::Max(Length / 100.0F, 0.01F), 0.025F, 0.025F));
        CandidateSegmentWorldTransforms[LinkIndex] = SegmentTransform;
        SegmentProvided[LinkIndex] = 1;
    }

    if (LinkFrameComponents.Num() != CandidateLinkWorldTransforms.Num()
        || LinkMarkerComponents.Num() != CandidateLinkWorldTransforms.Num()
        || LinkXAxisComponents.Num() != CandidateLinkWorldTransforms.Num()
        || LinkYAxisComponents.Num() != CandidateLinkWorldTransforms.Num()
        || LinkZAxisComponents.Num() != CandidateLinkWorldTransforms.Num()
        || LinkLabelComponents.Num() != CandidateLinkWorldTransforms.Num()
        || ToolFrameComponents.Num() != CandidateToolWorldTransforms.Num()
        || ToolMarkerComponents.Num() != CandidateToolWorldTransforms.Num()
        || ToolLabelComponents.Num() != CandidateToolWorldTransforms.Num()
        || JointAxisComponents.Num() != CandidateJointAxisWorldTransforms.Num())
    {
        return Reject(TEXT("installed model topology is incomplete for the candidate pose"));
    }
    for (int32 LinkIndex = 0; LinkIndex < CandidateLinkWorldTransforms.Num(); ++LinkIndex)
    {
        if (LinkFrameComponents[LinkIndex] == nullptr || LinkMarkerComponents[LinkIndex] == nullptr
            || LinkXAxisComponents[LinkIndex] == nullptr || LinkYAxisComponents[LinkIndex] == nullptr
            || LinkZAxisComponents[LinkIndex] == nullptr || LinkLabelComponents[LinkIndex] == nullptr)
        {
            return Reject(TEXT("installed model topology contains a null link component"));
        }
    }
    for (int32 ToolIndex = 0; ToolIndex < CandidateToolWorldTransforms.Num(); ++ToolIndex)
    {
        if (ToolFrameComponents[ToolIndex] == nullptr || ToolMarkerComponents[ToolIndex] == nullptr
            || ToolLabelComponents[ToolIndex] == nullptr)
        {
            return Reject(TEXT("installed model topology contains a null tool component"));
        }
    }
    for (int32 JointIndex = 0; JointIndex < JointAxisProvided.Num(); ++JointIndex)
    {
        if (JointAxisProvided[JointIndex] != 0 && JointAxisComponents[JointIndex] == nullptr)
        {
            return Reject(TEXT("installed model topology contains a null joint-axis component"));
        }
    }

    // Every possible failure above occurs before this point.  From here on,
    // the candidate is complete and may replace the last valid pose.
    for (int32 LinkIndex = 0; LinkIndex < CandidateLinkWorldTransforms.Num(); ++LinkIndex)
    {
        LinkFrameComponents[LinkIndex]->SetWorldTransform(
            CandidateLinkWorldTransforms[LinkIndex],
            false);
        if (SegmentProvided.IsValidIndex(LinkIndex) && SegmentProvided[LinkIndex] != 0)
        {
            LinkSegmentComponents[LinkIndex]->SetWorldTransform(
                CandidateSegmentWorldTransforms[LinkIndex],
                false);
        }
        LinkLabelComponents[LinkIndex]->SetText(FText::FromString(FString::Printf(
            TEXT("%s\n%s"),
            *SemanticLayerLabel(SemanticLayer),
            *CurrentDescription.Links[LinkIndex].Name.ToString())));
    }
    for (int32 ToolIndex = 0; ToolIndex < CandidateToolWorldTransforms.Num(); ++ToolIndex)
    {
        ToolFrameComponents[ToolIndex]->SetWorldTransform(
            CandidateToolWorldTransforms[ToolIndex],
            false);
        ToolLabelComponents[ToolIndex]->SetText(FText::FromString(FString::Printf(
            TEXT("%s\nTOOL %s"),
            *SemanticLayerLabel(SemanticLayer),
            *CurrentDescription.ToolFrames[ToolIndex].Name.ToString())));
    }
    for (int32 JointIndex = 0; JointIndex < JointAxisComponents.Num(); ++JointIndex)
    {
        if (JointAxisComponents[JointIndex] != nullptr
            && JointAxisProvided.IsValidIndex(JointIndex)
            && JointAxisProvided[JointIndex] != 0)
        {
            JointAxisComponents[JointIndex]->SetWorldTransform(
                CandidateJointAxisWorldTransforms[JointIndex],
                false);
        }
    }

    LastLinkWorldTransforms = MoveTemp(CandidateLinkWorldTransforms);
    LastToolWorldTransforms = MoveTemp(CandidateToolWorldTransforms);
    LastJointPositions = JointPositions;
    bLastStateWithinJointLimits = CandidateResult.bWithinJointLimits;
    LastDiagnostics = CandidateResult.Diagnostics;
    bHasValidPose = true;
    SetTopologyVisible(true);
    LastError.Reset();
    return true;
}

bool ADeferredTeleopKinematicRobotActor::GetLinkTransform(
    const FName LinkName,
    FTransform& OutWorldTransform,
    FString& OutError) const
{
    OutError.Reset();
    if (!bModelInitialized)
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("cannot query a link before InitializeModel"));
    }
    if (!bHasValidPose)
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("cannot query a link before a valid state is applied"));
    }
    const int32* Slot = LinkSlotByName.Find(LinkName);
    if (Slot == nullptr || !LastLinkWorldTransforms.IsValidIndex(*Slot))
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            FString::Printf(TEXT("unknown link: %s"), *LinkName.ToString()));
    }
    OutWorldTransform = LastLinkWorldTransforms[*Slot];
    return true;
}

bool ADeferredTeleopKinematicRobotActor::GetToolTransform(
    const FName ToolName,
    FTransform& OutWorldTransform,
    FString& OutError) const
{
    OutError.Reset();
    if (!bModelInitialized)
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("cannot query a tool before InitializeModel"));
    }
    if (!bHasValidPose)
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("cannot query a tool before a valid state is applied"));
    }
    const int32* Slot = ToolSlotByName.Find(ToolName);
    if (Slot == nullptr || !LastToolWorldTransforms.IsValidIndex(*Slot))
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            FString::Printf(TEXT("unknown tool frame: %s"), *ToolName.ToString()));
    }
    OutWorldTransform = LastToolWorldTransforms[*Slot];
    return true;
}

void ADeferredTeleopKinematicRobotActor::SetDebugFramesVisible(const bool bVisible)
{
    bDebugFramesVisible = bVisible;
    UpdateDebugVisibility();
}

void ADeferredTeleopKinematicRobotActor::DestroyTopology()
{
    DttKinematicRobotPrivate::DestroyComponents(LinkLabelComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkXAxisComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkYAxisComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkZAxisComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkMarkerComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkSegmentComponents);
    DttKinematicRobotPrivate::DestroyComponents(ToolLabelComponents);
    DttKinematicRobotPrivate::DestroyComponents(ToolMarkerComponents);
    DttKinematicRobotPrivate::DestroyComponents(JointAxisComponents);
    DttKinematicRobotPrivate::DestroyComponents(LinkFrameComponents);
    DttKinematicRobotPrivate::DestroyComponents(ToolFrameComponents);
}

bool ADeferredTeleopKinematicRobotActor::BuildTopology(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    FString& OutError)
{
    DttKinematicRobotPrivate::FTopologyBuffers NewTopology;
    if (!DttKinematicRobotPrivate::BuildTopology(
            this,
            Description,
            Model,
            SceneRoot,
            CubeMesh,
            SphereMesh,
            LinkMaterial,
            ToolMaterial,
            SegmentMaterial,
            DebugXAxisColor,
            DebugYAxisColor,
            DebugZAxisColor,
            DebugJointAxisColor,
            DebugToolColor,
            TopologyGeneration + 1,
            NewTopology,
            OutError))
    {
        return false;
    }

    DestroyTopology();
    LinkFrameComponents = MoveTemp(NewTopology.LinkFrames);
    LinkMarkerComponents = MoveTemp(NewTopology.LinkMarkers);
    LinkXAxisComponents = MoveTemp(NewTopology.LinkXAxis);
    LinkYAxisComponents = MoveTemp(NewTopology.LinkYAxis);
    LinkZAxisComponents = MoveTemp(NewTopology.LinkZAxis);
    LinkLabelComponents = MoveTemp(NewTopology.LinkLabels);
    LinkSegmentComponents = MoveTemp(NewTopology.LinkSegments);
    ToolFrameComponents = MoveTemp(NewTopology.ToolFrames);
    ToolMarkerComponents = MoveTemp(NewTopology.ToolMarkers);
    ToolLabelComponents = MoveTemp(NewTopology.ToolLabels);
    JointAxisComponents = MoveTemp(NewTopology.JointAxes);
    ++TopologyGeneration;
    return true;
}

void ADeferredTeleopKinematicRobotActor::SetTopologyVisible(const bool bPoseVisible)
{
    for (TObjectPtr<UStaticMeshComponent> Component : LinkMarkerComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bPoseVisible);
        }
    }
    for (TObjectPtr<UStaticMeshComponent> Component : ToolMarkerComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bPoseVisible);
        }
    }
    UpdateDebugVisibility();
}

void ADeferredTeleopKinematicRobotActor::UpdateDebugVisibility()
{
    const bool bDebugVisible = bHasValidPose && bDebugFramesVisible;
    const bool bLabelVisible = bDebugVisible && bShowDebugNames;
    for (TObjectPtr<UArrowComponent> Component : LinkXAxisComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bDebugVisible);
        }
    }
    for (TObjectPtr<UArrowComponent> Component : LinkYAxisComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bDebugVisible);
        }
    }
    for (TObjectPtr<UArrowComponent> Component : LinkZAxisComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bDebugVisible);
        }
    }
    for (TObjectPtr<UArrowComponent> Component : JointAxisComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bDebugVisible);
        }
    }
    for (TObjectPtr<UStaticMeshComponent> Component : LinkSegmentComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bDebugVisible);
        }
    }
    for (TObjectPtr<UTextRenderComponent> Component : LinkLabelComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bLabelVisible);
        }
    }
    for (TObjectPtr<UTextRenderComponent> Component : ToolLabelComponents)
    {
        if (Component != nullptr)
        {
            Component->SetVisibility(bLabelVisible);
        }
    }
}

void ADeferredTeleopKinematicRobotActor::HidePoseComponents()
{
    SetTopologyVisible(false);
}

bool ADeferredTeleopKinematicRobotActor::AreDescriptionsEqual(
    const FDttRobotDescription& Left,
    const FDttRobotDescription& Right)
{
    if (Left.ModelId != Right.ModelId || Left.ModelRevision != Right.ModelRevision
        || Left.RootLinkName != Right.RootLinkName || Left.Links.Num() != Right.Links.Num()
        || Left.Joints.Num() != Right.Joints.Num() || Left.JointGroups.Num() != Right.JointGroups.Num()
        || Left.ToolFrames.Num() != Right.ToolFrames.Num())
    {
        return false;
    }

    for (int32 Index = 0; Index < Left.Links.Num(); ++Index)
    {
        if (Left.Links[Index].Name != Right.Links[Index].Name)
        {
            return false;
        }
    }
    for (int32 Index = 0; Index < Left.Joints.Num(); ++Index)
    {
        const FDttRobotJointDescription& A = Left.Joints[Index];
        const FDttRobotJointDescription& B = Right.Joints[Index];
        if (A.Name != B.Name || A.Type != B.Type || A.ParentLink != B.ParentLink
            || A.ChildLink != B.ChildLink || !AreCanonicalTransformsEqual(A.ParentToJoint, B.ParentToJoint)
            || A.AxisJointFrame.X != B.AxisJointFrame.X || A.AxisJointFrame.Y != B.AxisJointFrame.Y
            || A.AxisJointFrame.Z != B.AxisJointFrame.Z || A.bHasPositionLimits != B.bHasPositionLimits
            || A.LowerPositionRadians != B.LowerPositionRadians
            || A.UpperPositionRadians != B.UpperPositionRadians)
        {
            return false;
        }
    }
    for (int32 Index = 0; Index < Left.JointGroups.Num(); ++Index)
    {
        if (Left.JointGroups[Index].Name != Right.JointGroups[Index].Name
            || Left.JointGroups[Index].JointNames.Num()
                != Right.JointGroups[Index].JointNames.Num())
        {
            return false;
        }
        for (int32 JointNameIndex = 0;
             JointNameIndex < Left.JointGroups[Index].JointNames.Num();
             ++JointNameIndex)
        {
            if (Left.JointGroups[Index].JointNames[JointNameIndex]
                != Right.JointGroups[Index].JointNames[JointNameIndex])
            {
                return false;
            }
        }
    }
    for (int32 Index = 0; Index < Left.ToolFrames.Num(); ++Index)
    {
        if (Left.ToolFrames[Index].Name != Right.ToolFrames[Index].Name
            || Left.ToolFrames[Index].LinkName != Right.ToolFrames[Index].LinkName
            || !AreCanonicalTransformsEqual(
                Left.ToolFrames[Index].LinkToTool,
                Right.ToolFrames[Index].LinkToTool))
        {
            return false;
        }
    }
    return true;
}

bool ADeferredTeleopKinematicRobotActor::AreCanonicalTransformsEqual(
    const FDttCanonicalTransform& Left,
    const FDttCanonicalTransform& Right)
{
    return Left.TranslationMetres.X == Right.TranslationMetres.X
        && Left.TranslationMetres.Y == Right.TranslationMetres.Y
        && Left.TranslationMetres.Z == Right.TranslationMetres.Z
        && Left.Rotation.X == Right.Rotation.X
        && Left.Rotation.Y == Right.Rotation.Y
        && Left.Rotation.Z == Right.Rotation.Z
        && Left.Rotation.W == Right.Rotation.W;
}

FString ADeferredTeleopKinematicRobotActor::SemanticLayerLabel(
    const EDeferredTeleopKinematicSemanticLayer Layer)
{
    switch (Layer)
    {
    case EDeferredTeleopKinematicSemanticLayer::Confirmed:
        return TEXT("CONFIRMED");
    case EDeferredTeleopKinematicSemanticLayer::Arrival:
        return TEXT("ARRIVAL");
    case EDeferredTeleopKinematicSemanticLayer::Target:
        return TEXT("TARGET");
    default:
        return TEXT("UNKNOWN");
    }
}

bool ADeferredTeleopKinematicRobotActor::HasUnitActorScale(FString& OutError) const
{
    const FVector Scale = GetActorScale3D();
    if (!FMath::IsFinite(Scale.X) || !FMath::IsFinite(Scale.Y)
        || !FMath::IsFinite(Scale.Z))
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("actor scale must contain finite values"));
    }
    if (!FMath::IsNearlyEqual(Scale.X, 1.0F, 1.0e-5F)
        || !FMath::IsNearlyEqual(Scale.Y, 1.0F, 1.0e-5F)
        || !FMath::IsNearlyEqual(Scale.Z, 1.0F, 1.0e-5F))
    {
        return DttKinematicRobotPrivate::Fail(
            OutError,
            TEXT("actor scale must remain unit (1,1,1); canonical metres are converted explicitly"));
    }
    return true;
}
