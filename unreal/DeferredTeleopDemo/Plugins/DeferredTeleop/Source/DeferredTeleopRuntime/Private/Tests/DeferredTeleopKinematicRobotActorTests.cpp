#if WITH_DEV_AUTOMATION_TESTS

#include "Visualization/DeferredTeleopKinematicRobotActor.h"

#include "Components/SceneComponent.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "UObject/UObjectGlobals.h"

#include <limits>

namespace DeferredTeleop::Tests::KinematicRobotActor
{
constexpr double Pi = 3.1415926535897932384626433832795;

FDttCanonicalTransform Translation(const double X, const double Y, const double Z)
{
    return FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(X, Y, Z),
        FQuat4d(0.0, 0.0, 0.0, 1.0));
}

FDttCanonicalTransform RotationAroundZ(const double AngleRadians)
{
    return FDttCanonicalTransform::FromAxisAngle(
        FVector3d::ZeroVector,
        FVector3d(0.0, 0.0, 1.0),
        AngleRadians);
}

FDttCanonicalVector Axis(const double X, const double Y, const double Z)
{
    FDttCanonicalVector Result;
    Result.X = X;
    Result.Y = Y;
    Result.Z = Z;
    return Result;
}

FDttRobotLinkDescription Link(const TCHAR* Name)
{
    FDttRobotLinkDescription Result;
    Result.Name = FName(Name);
    return Result;
}

FDttNamedJointPosition JointPosition(const TCHAR* Name, const double PositionRadians)
{
    FDttNamedJointPosition Result;
    Result.JointName = FName(Name);
    Result.PositionRadians = PositionRadians;
    return Result;
}

FDttRobotDescription MakeSmallDescription()
{
    FDttRobotDescription Description;
    Description.ModelId = TEXT("kinematic-actor-test");
    Description.ModelRevision = TEXT("test:1");
    Description.RootLinkName = FName(TEXT("base"));
    Description.Links = {
        Link(TEXT("base")),
        Link(TEXT("arm")),
        Link(TEXT("tip")),
    };

    FDttRobotJointDescription BaseToArm;
    BaseToArm.Name = FName(TEXT("base_to_arm"));
    BaseToArm.Type = EDttRobotJointType::Revolute;
    BaseToArm.ParentLink = FName(TEXT("base"));
    BaseToArm.ChildLink = FName(TEXT("arm"));
    BaseToArm.ParentToJoint = Translation(1.0, 0.0, 0.0);
    BaseToArm.AxisJointFrame = Axis(0.0, 0.0, 1.0);
    BaseToArm.bHasPositionLimits = true;
    BaseToArm.LowerPositionRadians = -Pi;
    BaseToArm.UpperPositionRadians = Pi;

    FDttRobotJointDescription ArmToTip;
    ArmToTip.Name = FName(TEXT("arm_to_tip"));
    ArmToTip.Type = EDttRobotJointType::Fixed;
    ArmToTip.ParentLink = FName(TEXT("arm"));
    ArmToTip.ChildLink = FName(TEXT("tip"));
    ArmToTip.ParentToJoint = Translation(0.0, 0.0, 1.0);

    // The order is intentionally unlike the link order to prove that the
    // actor consumes the named FK result rather than array positions.
    Description.Joints = {ArmToTip, BaseToArm};

    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("tip"));
    Tool.LinkToTool = Translation(0.0, 0.0, 0.25);
    Description.ToolFrames = {Tool};
    return Description;
}

TArray<FDttNamedJointPosition> MakeSmallState(const double PositionRadians)
{
    return {JointPosition(TEXT("base_to_arm"), PositionRadians)};
}

bool NearlyEqual(const double Left, const double Right, const double Tolerance = 1.0e-5)
{
    return FMath::Abs(Left - Right) <= Tolerance;
}

bool NearlyEqualTransform(
    const FTransform& Left,
    const FTransform& Right,
    const float Tolerance = 1.0e-3F)
{
    return Left.Equals(Right, Tolerance);
}

TMap<FName, USceneComponent*> ComponentSnapshot(
    ADeferredTeleopKinematicRobotActor* Actor,
    const TCHAR* Prefix)
{
    TMap<FName, USceneComponent*> Result;
    for (UActorComponent* ActorComponent : Actor->GetInstanceComponents())
    {
        USceneComponent* Component = Cast<USceneComponent>(ActorComponent);
        if (Component != nullptr && Component->GetName().StartsWith(Prefix))
        {
            Result.Add(Component->GetFName(), Component);
        }
    }
    return Result;
}

TMap<FName, USceneComponent*> LinkFrameSnapshot(ADeferredTeleopKinematicRobotActor* Actor)
{
    return ComponentSnapshot(Actor, TEXT("DttLinkFrame_"));
}

TMap<FName, USceneComponent*> ToolFrameSnapshot(ADeferredTeleopKinematicRobotActor* Actor)
{
    return ComponentSnapshot(Actor, TEXT("DttToolFrame_"));
}

ADeferredTeleopKinematicRobotActor* NewActor()
{
    return NewObject<ADeferredTeleopKinematicRobotActor>(GetTransientPackage());
}

} // namespace DeferredTeleop::Tests::KinematicRobotActor

namespace DttKinematicRobotActorTest = DeferredTeleop::Tests::KinematicRobotActor;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicRobotActorCorePoseTest,
    "DeferredTeleop.M2.KinematicRobotActor.MatchesCoreByName",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicRobotActorCorePoseTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = DttKinematicRobotActorTest::MakeSmallDescription();
    const FDttCanonicalTransform Root = FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(1.0, 2.0, 3.0),
        DttKinematicRobotActorTest::RotationAroundZ(DttKinematicRobotActorTest::Pi / 4.0).GetRotationQuaternion());
    const TArray<FDttNamedJointPosition> State =
        DttKinematicRobotActorTest::MakeSmallState(-DttKinematicRobotActorTest::Pi / 3.0);

    ADeferredTeleopKinematicRobotActor* Actor = DttKinematicRobotActorTest::NewActor();
    FString Error;
    if (!TestTrue(TEXT("model initializes"), Actor->InitializeModel(Description, Root, Error)))
    {
        AddError(Error);
        return false;
    }
    if (!TestTrue(TEXT("state applies"), Actor->ApplyState(State, Error)))
    {
        AddError(Error);
        return false;
    }

    FDttForwardKinematicsResult CoreResult;
    TestTrue(
        TEXT("shared FK evaluates the fixture"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            Root,
            State,
            CoreResult));
    const TMap<FName, USceneComponent*> LinkFrames =
        DttKinematicRobotActorTest::LinkFrameSnapshot(Actor);
    const TMap<FName, USceneComponent*> ToolFrames =
        DttKinematicRobotActorTest::ToolFrameSnapshot(Actor);
    TestEqual(
        TEXT("one flat link frame exists per model link"),
        LinkFrames.Num(),
        Description.Links.Num());
    TestEqual(
        TEXT("one flat tool frame exists per model tool"),
        ToolFrames.Num(),
        Description.ToolFrames.Num());
    TMap<FName, FTransform> LinkPosesBeforeActorMove;
    TMap<FName, FTransform> ToolPosesBeforeActorMove;
    TMap<FName, FTransform> LinkComponentPosesBeforeActorMove;
    TMap<FName, FTransform> ToolComponentPosesBeforeActorMove;
    for (const TPair<FName, USceneComponent*>& Pair : LinkFrames)
    {
        USceneComponent* const Frame = Pair.Value;
        TestTrue(
            *FString::Printf(TEXT("link frame %s is attached to SceneRoot"), *Pair.Key.ToString()),
            Frame != nullptr && Frame->GetAttachParent() == Actor->GetRootComponent());
        if (Frame != nullptr)
        {
            TestTrue(
                *FString::Printf(TEXT("link frame %s uses absolute location"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteLocation());
            TestTrue(
                *FString::Printf(TEXT("link frame %s uses absolute rotation"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteRotation());
            TestTrue(
                *FString::Printf(TEXT("link frame %s uses absolute scale"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteScale());
            LinkComponentPosesBeforeActorMove.Add(Pair.Key, Frame->GetComponentTransform());
        }
    }
    for (const TPair<FName, USceneComponent*>& Pair : ToolFrames)
    {
        USceneComponent* const Frame = Pair.Value;
        TestTrue(
            *FString::Printf(TEXT("tool frame %s is attached to SceneRoot"), *Pair.Key.ToString()),
            Frame != nullptr && Frame->GetAttachParent() == Actor->GetRootComponent());
        if (Frame != nullptr)
        {
            TestTrue(
                *FString::Printf(TEXT("tool frame %s uses absolute location"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteLocation());
            TestTrue(
                *FString::Printf(TEXT("tool frame %s uses absolute rotation"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteRotation());
            TestTrue(
                *FString::Printf(TEXT("tool frame %s uses absolute scale"), *Pair.Key.ToString()),
                Frame->IsUsingAbsoluteScale());
            ToolComponentPosesBeforeActorMove.Add(Pair.Key, Frame->GetComponentTransform());
        }
    }
    for (const FDttNamedCanonicalTransform& CoreLink : CoreResult.LinkTransforms)
    {
        FTransform Expected;
        FString ConversionError;
        TestTrue(
            *FString::Printf(TEXT("core link %s converts"), *CoreLink.Name.ToString()),
            DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                CoreLink.Transform,
                Expected,
                ConversionError));
        FTransform Actual;
        FString QueryError;
        if (TestTrue(
                *FString::Printf(TEXT("actor returns link %s"), *CoreLink.Name.ToString()),
                Actor->GetLinkTransform(CoreLink.Name, Actual, QueryError)))
        {
            TestTrue(
                *FString::Printf(TEXT("link %s matches core conversion"), *CoreLink.Name.ToString()),
                DttKinematicRobotActorTest::NearlyEqualTransform(Actual, Expected));
            LinkPosesBeforeActorMove.Add(CoreLink.Name, Actual);
        }
        else
        {
            AddError(QueryError);
        }
    }

    for (const FDttNamedCanonicalTransform& CoreTool : CoreResult.ToolTransforms)
    {
        FTransform Expected;
        FString ConversionError;
        TestTrue(
            *FString::Printf(TEXT("core tool %s converts"), *CoreTool.Name.ToString()),
            DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                CoreTool.Transform,
                Expected,
                ConversionError));
        FTransform Actual;
        FString QueryError;
        const bool bToolQuerySucceeded = TestTrue(
            *FString::Printf(TEXT("actor returns tool %s"), *CoreTool.Name.ToString()),
            Actor->GetToolTransform(CoreTool.Name, Actual, QueryError));
        if (bToolQuerySucceeded)
        {
            TestTrue(
                *FString::Printf(TEXT("tool %s matches core conversion"), *CoreTool.Name.ToString()),
                DttKinematicRobotActorTest::NearlyEqualTransform(Actual, Expected));
            ToolPosesBeforeActorMove.Add(CoreTool.Name, Actual);
        }
        else
        {
            AddError(QueryError);
        }
    }

    FTransform RootWorld;
    TestTrue(TEXT("root lookup succeeds"), Actor->GetLinkTransform(FName(TEXT("base")), RootWorld, Error));
    TestTrue(TEXT("root metres and reflection convert exactly once"), DttKinematicRobotActorTest::NearlyEqual(
        RootWorld.GetLocation().X,
        100.0));
    TestTrue(TEXT("root Y basis is reflected once"), DttKinematicRobotActorTest::NearlyEqual(
        RootWorld.GetLocation().Y,
        -200.0));
    TestTrue(TEXT("root Z metres become centimetres"), DttKinematicRobotActorTest::NearlyEqual(
        RootWorld.GetLocation().Z,
        300.0));
    TestEqual(TEXT("actor scale remains unit"), Actor->GetActorScale3D(), FVector::OneVector);

    Actor->SetActorLocation(
        FVector(175.0, -220.0, 85.0),
        false);
    Actor->SetActorRotation(FRotator(12.0, -23.0, 7.0));
    for (const TPair<FName, FTransform>& Pair : LinkPosesBeforeActorMove)
    {
        FTransform AfterMove;
        FString QueryError;
        TestTrue(
            *FString::Printf(TEXT("link %s remains queryable after actor move"), *Pair.Key.ToString()),
            Actor->GetLinkTransform(Pair.Key, AfterMove, QueryError));
        TestTrue(
            *FString::Printf(TEXT("link %s pose is unchanged by actor move"), *Pair.Key.ToString()),
            DttKinematicRobotActorTest::NearlyEqualTransform(AfterMove, Pair.Value));
    }
    for (const TPair<FName, FTransform>& Pair : ToolPosesBeforeActorMove)
    {
        FTransform AfterMove;
        FString QueryError;
        TestTrue(
            *FString::Printf(TEXT("tool %s remains queryable after actor move"), *Pair.Key.ToString()),
            Actor->GetToolTransform(Pair.Key, AfterMove, QueryError));
        TestTrue(
            *FString::Printf(TEXT("tool %s pose is unchanged by actor move"), *Pair.Key.ToString()),
            DttKinematicRobotActorTest::NearlyEqualTransform(AfterMove, Pair.Value));
    }
    for (const TPair<FName, FTransform>& Pair : LinkComponentPosesBeforeActorMove)
    {
        USceneComponent* const* Frame = LinkFrames.Find(Pair.Key);
        TestTrue(
            *FString::Printf(TEXT("link frame %s remains flat after actor move"), *Pair.Key.ToString()),
            Frame != nullptr && DttKinematicRobotActorTest::NearlyEqualTransform(
                (*Frame)->GetComponentTransform(),
                Pair.Value));
    }
    for (const TPair<FName, FTransform>& Pair : ToolComponentPosesBeforeActorMove)
    {
        USceneComponent* const* Frame = ToolFrames.Find(Pair.Key);
        TestTrue(
            *FString::Printf(TEXT("tool frame %s remains flat after actor move"), *Pair.Key.ToString()),
            Frame != nullptr && DttKinematicRobotActorTest::NearlyEqualTransform(
                (*Frame)->GetComponentTransform(),
                Pair.Value));
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicRobotActorInvalidStateTest,
    "DeferredTeleop.M2.KinematicRobotActor.InvalidStatePreservesPose",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicRobotActorInvalidStateTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = DttKinematicRobotActorTest::MakeSmallDescription();
    ADeferredTeleopKinematicRobotActor* Actor = DttKinematicRobotActorTest::NewActor();
    FString Error;
    TestTrue(
        TEXT("model initializes"),
        Actor->InitializeModel(Description, DttKinematicRobotActorTest::Translation(0.0, 0.0, 0.0), Error));
    TestTrue(
        TEXT("valid state applies"),
        Actor->ApplyState(DttKinematicRobotActorTest::MakeSmallState(0.4), Error));

    FTransform Before;
    TestTrue(TEXT("baseline pose is queryable"), Actor->GetLinkTransform(FName(TEXT("tip")), Before, Error));
    const TMap<FName, USceneComponent*> TopologyBeforeInvalidModel =
        DttKinematicRobotActorTest::LinkFrameSnapshot(Actor);
    const TMap<FName, USceneComponent*> ToolTopologyBeforeInvalidModel =
        DttKinematicRobotActorTest::ToolFrameSnapshot(Actor);

    TArray<FDttNamedJointPosition> NonFinite = DttKinematicRobotActorTest::MakeSmallState(0.4);
    NonFinite[0].PositionRadians = std::numeric_limits<double>::quiet_NaN();
    TestFalse(TEXT("NaN state is rejected"), Actor->ApplyState(NonFinite, Error));
    FTransform AfterNaN;
    TestTrue(TEXT("NaN rejection keeps pose"), Actor->GetLinkTransform(FName(TEXT("tip")), AfterNaN, Error));
    TestTrue(
        TEXT("NaN rejection keeps exact transform"),
        DttKinematicRobotActorTest::NearlyEqualTransform(Before, AfterNaN));

    const TArray<FDttNamedJointPosition> MissingState;
    TestFalse(TEXT("missing state is rejected"), Actor->ApplyState(MissingState, Error));
    FTransform AfterMissing;
    TestTrue(TEXT("missing state keeps pose"), Actor->GetLinkTransform(FName(TEXT("tip")), AfterMissing, Error));
    TestTrue(
        TEXT("missing state keeps exact transform"),
        DttKinematicRobotActorTest::NearlyEqualTransform(Before, AfterMissing));

    TArray<FDttNamedJointPosition> UnknownState;
    UnknownState.Add(DttKinematicRobotActorTest::JointPosition(TEXT("unknown"), 0.1));
    TestFalse(
        TEXT("unknown named state is rejected"),
        Actor->ApplyState(UnknownState, Error));
    FTransform AfterUnknown;
    TestTrue(TEXT("unknown state keeps pose"), Actor->GetLinkTransform(FName(TEXT("tip")), AfterUnknown, Error));
    TestTrue(
        TEXT("unknown state keeps exact transform"),
        DttKinematicRobotActorTest::NearlyEqualTransform(Before, AfterUnknown));

    FDttRobotDescription InvalidModel = Description;
    InvalidModel.RootLinkName = FName(TEXT("missing_root"));
    TestFalse(
        TEXT("invalid replacement model is rejected"),
        Actor->InitializeModel(
            InvalidModel,
            DttKinematicRobotActorTest::Translation(5.0, 0.0, 0.0),
            Error));
    FTransform AfterModel;
    TestTrue(TEXT("invalid model keeps old pose"), Actor->GetLinkTransform(FName(TEXT("tip")), AfterModel, Error));
    TestTrue(
        TEXT("invalid model keeps exact transform"),
        DttKinematicRobotActorTest::NearlyEqualTransform(Before, AfterModel));
    TestTrue(TEXT("old model remains initialized"), Actor->bModelInitialized);
    TestTrue(TEXT("old pose remains valid"), Actor->bHasValidPose);
    const TMap<FName, USceneComponent*> TopologyAfterInvalidModel =
        DttKinematicRobotActorTest::LinkFrameSnapshot(Actor);
    const TMap<FName, USceneComponent*> ToolTopologyAfterInvalidModel =
        DttKinematicRobotActorTest::ToolFrameSnapshot(Actor);
    TestEqual(
        TEXT("invalid model keeps topology count"),
        TopologyAfterInvalidModel.Num(),
        TopologyBeforeInvalidModel.Num());
    for (const TPair<FName, USceneComponent*>& Pair : TopologyBeforeInvalidModel)
    {
        USceneComponent* const* Found = TopologyAfterInvalidModel.Find(Pair.Key);
        TestTrue(
            *FString::Printf(TEXT("invalid model keeps component %s"), *Pair.Key.ToString()),
            Found != nullptr && *Found == Pair.Value);
    }
    TestEqual(
        TEXT("invalid model keeps tool topology count"),
        ToolTopologyAfterInvalidModel.Num(),
        ToolTopologyBeforeInvalidModel.Num());
    for (const TPair<FName, USceneComponent*>& Pair : ToolTopologyBeforeInvalidModel)
    {
        USceneComponent* const* Found = ToolTopologyAfterInvalidModel.Find(Pair.Key);
        TestTrue(
            *FString::Printf(TEXT("invalid model keeps tool component %s"), *Pair.Key.ToString()),
            Found != nullptr && *Found == Pair.Value);
    }

    Actor->SetActorScale3D(FVector(2.0));
    TestFalse(
        TEXT("non-unit actor scale is rejected"),
        Actor->ApplyState(DttKinematicRobotActorTest::MakeSmallState(0.5), Error));
    FTransform AfterScale;
    TestTrue(TEXT("scale rejection keeps pose"), Actor->GetLinkTransform(FName(TEXT("tip")), AfterScale, Error));
    TestTrue(
        TEXT("scale rejection keeps exact transform"),
        DttKinematicRobotActorTest::NearlyEqualTransform(Before, AfterScale));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicRobotActorStableTopologyTest,
    "DeferredTeleop.M2.KinematicRobotActor.ReusesTopology",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicRobotActorStableTopologyTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    ADeferredTeleopKinematicRobotActor* Actor = DttKinematicRobotActorTest::NewActor();
    FString Error;
    const FDttRobotDescription Description = DttKinematicRobotActorTest::MakeSmallDescription();
    TestTrue(
        TEXT("model initializes"),
        Actor->InitializeModel(
            Description,
            DttKinematicRobotActorTest::Translation(0.0, 0.0, 0.0),
            Error));
    TestTrue(
        TEXT("first state applies"),
        Actor->ApplyState(DttKinematicRobotActorTest::MakeSmallState(0.1), Error));
    TMap<FName, FTransform> LinkPosesBeforeActorMove;
    TMap<FName, FTransform> ToolPosesBeforeActorMove;
    for (const FDttRobotLinkDescription& Link : Description.Links)
    {
        FTransform Pose;
        TestTrue(
            *FString::Printf(TEXT("link %s is queryable before actor move"), *Link.Name.ToString()),
            Actor->GetLinkTransform(Link.Name, Pose, Error));
        LinkPosesBeforeActorMove.Add(Link.Name, Pose);
    }
    for (const FDttRobotToolFrameDescription& Tool : Description.ToolFrames)
    {
        FTransform Pose;
        TestTrue(
            *FString::Printf(TEXT("tool %s is queryable before actor move"), *Tool.Name.ToString()),
            Actor->GetToolTransform(Tool.Name, Pose, Error));
        ToolPosesBeforeActorMove.Add(Tool.Name, Pose);
    }
    Actor->SetActorLocation(
        FVector(-120.0, 240.0, 45.0),
        false);
    Actor->SetActorRotation(FRotator(-8.0, 31.0, 4.0));
    for (const TPair<FName, FTransform>& Pair : LinkPosesBeforeActorMove)
    {
        FTransform AfterMove;
        TestTrue(
            *FString::Printf(TEXT("link %s remains unchanged after actor move"), *Pair.Key.ToString()),
            Actor->GetLinkTransform(Pair.Key, AfterMove, Error));
        TestTrue(
            *FString::Printf(TEXT("link %s pose remains identical"), *Pair.Key.ToString()),
            DttKinematicRobotActorTest::NearlyEqualTransform(AfterMove, Pair.Value));
    }
    for (const TPair<FName, FTransform>& Pair : ToolPosesBeforeActorMove)
    {
        FTransform AfterMove;
        TestTrue(
            *FString::Printf(TEXT("tool %s remains unchanged after actor move"), *Pair.Key.ToString()),
            Actor->GetToolTransform(Pair.Key, AfterMove, Error));
        TestTrue(
            *FString::Printf(TEXT("tool %s pose remains identical"), *Pair.Key.ToString()),
            DttKinematicRobotActorTest::NearlyEqualTransform(AfterMove, Pair.Value));
    }
    const TMap<FName, USceneComponent*> BeforeLinks =
        DttKinematicRobotActorTest::LinkFrameSnapshot(Actor);
    const TMap<FName, USceneComponent*> BeforeTools =
        DttKinematicRobotActorTest::ToolFrameSnapshot(Actor);
    TestEqual(TEXT("one flat link frame exists per model link"), BeforeLinks.Num(), Description.Links.Num());
    TestEqual(TEXT("one flat tool frame exists per model tool"), BeforeTools.Num(), Description.ToolFrames.Num());

    TestTrue(
        TEXT("second state applies"),
        Actor->ApplyState(DttKinematicRobotActorTest::MakeSmallState(-0.7), Error));
    const TMap<FName, USceneComponent*> AfterLinks =
        DttKinematicRobotActorTest::LinkFrameSnapshot(Actor);
    const TMap<FName, USceneComponent*> AfterTools =
        DttKinematicRobotActorTest::ToolFrameSnapshot(Actor);
    TestEqual(TEXT("link frame count is stable"), AfterLinks.Num(), BeforeLinks.Num());
    TestEqual(TEXT("tool frame count is stable"), AfterTools.Num(), BeforeTools.Num());
    for (const TPair<FName, USceneComponent*>& Pair : BeforeLinks)
    {
        USceneComponent* const* Found = AfterLinks.Find(Pair.Key);
        TestTrue(*FString::Printf(TEXT("component %s is reused"), *Pair.Key.ToString()), Found != nullptr);
        if (Found != nullptr)
        {
            TestTrue(*FString::Printf(TEXT("component pointer %s is unchanged"), *Pair.Key.ToString()), *Found == Pair.Value);
        }
    }
    for (const TPair<FName, USceneComponent*>& Pair : BeforeTools)
    {
        USceneComponent* const* Found = AfterTools.Find(Pair.Key);
        TestTrue(*FString::Printf(TEXT("tool component %s is reused"), *Pair.Key.ToString()), Found != nullptr);
        if (Found != nullptr)
        {
            TestTrue(*FString::Printf(TEXT("tool component pointer %s is unchanged"), *Pair.Key.ToString()), *Found == Pair.Value);
        }
    }

    FDttRobotDescription CaseChanged = Description;
    CaseChanged.ModelId = TEXT("KINEMATIC-ACTOR-TEST");
    TestTrue(
        TEXT("model id case change reinitializes successfully"),
        Actor->InitializeModel(
            CaseChanged,
            DttKinematicRobotActorTest::Translation(0.0, 0.0, 0.0),
            Error));
    TestFalse(
        TEXT("model id case change does not retain the old pose"),
        Actor->bHasValidPose);
    TestTrue(
        TEXT("case-changed model accepts a new state"),
        Actor->ApplyState(DttKinematicRobotActorTest::MakeSmallState(-0.2), Error));

    CaseChanged.ModelRevision = TEXT("TEST:1");
    TestTrue(
        TEXT("model revision case change reinitializes successfully"),
        Actor->InitializeModel(
            CaseChanged,
            DttKinematicRobotActorTest::Translation(0.0, 0.0, 0.0),
            Error));
    TestFalse(
        TEXT("model revision case change does not retain the old pose"),
        Actor->bHasValidPose);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicRobotActorIndependentLayersTest,
    "DeferredTeleop.M2.KinematicRobotActor.IndependentSemanticLayers",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicRobotActorIndependentLayersTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = DttKinematicRobotActorTest::MakeSmallDescription();
    const FDttCanonicalTransform Root = DttKinematicRobotActorTest::Translation(0.0, 0.0, 0.0);
    const TArray<FDttNamedJointPosition> State = DttKinematicRobotActorTest::MakeSmallState(0.2);
    const EDeferredTeleopKinematicSemanticLayer Layers[] = {
        EDeferredTeleopKinematicSemanticLayer::Confirmed,
        EDeferredTeleopKinematicSemanticLayer::Arrival,
        EDeferredTeleopKinematicSemanticLayer::Target,
    };
    ADeferredTeleopKinematicRobotActor* Actors[UE_ARRAY_COUNT(Layers)] = {};
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(Layers); ++Index)
    {
        Actors[Index] = DttKinematicRobotActorTest::NewActor();
        Actors[Index]->SemanticLayer = Layers[Index];
        FString Error;
        TestTrue(
            *FString::Printf(TEXT("layer %d model initializes"), Index),
            Actors[Index]->InitializeModel(Description, Root, Error));
        TestTrue(
            *FString::Printf(TEXT("layer %d state applies"), Index),
            Actors[Index]->ApplyState(State, Error));
        TestEqual(
            *FString::Printf(TEXT("layer %d keeps explicit semantic value"), Index),
            Actors[Index]->SemanticLayer,
            Layers[Index]);
    }

    FTransform ConfirmedTip;
    FTransform ArrivalTip;
    FTransform TargetTip;
    FString Error;
    Actors[0]->GetLinkTransform(FName(TEXT("tip")), ConfirmedTip, Error);
    Actors[1]->GetLinkTransform(FName(TEXT("tip")), ArrivalTip, Error);
    Actors[2]->GetLinkTransform(FName(TEXT("tip")), TargetTip, Error);
    TestTrue(
        TEXT("confirmed and arrival poses initially match"),
        DttKinematicRobotActorTest::NearlyEqualTransform(ConfirmedTip, ArrivalTip));
    TestTrue(
        TEXT("arrival and target poses initially match"),
        DttKinematicRobotActorTest::NearlyEqualTransform(ArrivalTip, TargetTip));

    TestTrue(
        TEXT("target can move independently"),
        Actors[2]->ApplyState(DttKinematicRobotActorTest::MakeSmallState(-0.8), Error));
    FTransform TargetMoved;
    Actors[2]->GetLinkTransform(FName(TEXT("tip")), TargetMoved, Error);
    Actors[0]->GetLinkTransform(FName(TEXT("tip")), ConfirmedTip, Error);
    TestFalse(
        TEXT("target update does not mutate confirmed"),
        DttKinematicRobotActorTest::NearlyEqualTransform(TargetMoved, ConfirmedTip));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicRobotActorSO101Test,
    "DeferredTeleop.M2.KinematicRobotActor.SO101GeneratedDescription",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicRobotActorSO101Test::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FString FixturePath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../robots/so101/generated/so101.kinematics.json"));
    FString Json;
    if (!TestTrue(TEXT("generated SO101 description is available"), FFileHelper::LoadFileToString(Json, *FixturePath)))
    {
        return false;
    }

    FDttRobotDescription Description;
    FString Error;
    if (!TestTrue(
            TEXT("generated SO101 description parses"),
            DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Json, Description, Error)))
    {
        AddError(Error);
        return false;
    }

    TArray<FDttNamedJointPosition> State;
    int32 RevoluteCount = 0;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double Offset = 0.04 * static_cast<double>(RevoluteCount + 1);
        double Position = Offset;
        if (Joint.bHasPositionLimits)
        {
            const double Midpoint = 0.5 * (Joint.LowerPositionRadians + Joint.UpperPositionRadians);
            const double Margin = 0.2 * (Joint.UpperPositionRadians - Joint.LowerPositionRadians);
            Position = FMath::Clamp(Midpoint + Offset, Joint.LowerPositionRadians + Margin, Joint.UpperPositionRadians - Margin);
        }
        State.Add(DttKinematicRobotActorTest::JointPosition(*Joint.Name.ToString(), Position));
        ++RevoluteCount;
    }
    TestTrue(TEXT("SO101 has revolute joints"), RevoluteCount > 0);

    ADeferredTeleopKinematicRobotActor* Actor = DttKinematicRobotActorTest::NewActor();
    const FDttCanonicalTransform Root = FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(0.5, -0.25, 0.2),
        DttKinematicRobotActorTest::RotationAroundZ(0.2).GetRotationQuaternion());
    if (!TestTrue(TEXT("SO101 model initializes"), Actor->InitializeModel(Description, Root, Error)))
    {
        AddError(Error);
        return false;
    }
    if (!TestTrue(TEXT("SO101 non-trivial state applies"), Actor->ApplyState(State, Error)))
    {
        AddError(Error);
        return false;
    }

    FDttForwardKinematicsResult CoreResult;
    TestTrue(
        TEXT("SO101 core pose evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            Root,
            State,
            CoreResult));
    for (const FDttNamedCanonicalTransform& CoreLink : CoreResult.LinkTransforms)
    {
        FTransform Expected;
        FString ConversionError;
        DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            CoreLink.Transform,
            Expected,
            ConversionError);
        FTransform Actual;
        FString QueryError;
        TestTrue(
            *FString::Printf(TEXT("SO101 link %s is queryable"), *CoreLink.Name.ToString()),
            Actor->GetLinkTransform(CoreLink.Name, Actual, QueryError));
        TestTrue(
            *FString::Printf(TEXT("SO101 link %s matches core"), *CoreLink.Name.ToString()),
            DttKinematicRobotActorTest::NearlyEqualTransform(Actual, Expected));
    }
    return true;
}

#endif
