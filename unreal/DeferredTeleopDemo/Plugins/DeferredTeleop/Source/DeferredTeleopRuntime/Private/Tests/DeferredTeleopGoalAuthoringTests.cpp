#if WITH_DEV_AUTOMATION_TESTS

#include "Authoring/DeferredTeleopGoalAuthoringComponent.h"
#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "UObject/UObjectGlobals.h"
#include <limits>

struct FDttGoalAuthoringTestAccess
{
    static void Pump(UDeferredTeleopGoalAuthoringComponent* Component, double Now)
    {
        Component->ProcessPendingAt(Now);
    }
};

namespace DeferredTeleop::Tests::GoalAuthoring
{
FDeferredTeleopArticulatedModelBinding Binding()
{
    FDeferredTeleopArticulatedModelBinding Result;
    Result.RobotId = TEXT("so101-follower-1");
    Result.DescriptionFilePath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../robots/so101/generated/so101.kinematics.json"));
    Result.ExpectedFrameId = TEXT("field-world");
    Result.ExpectedCalibrationVersion = TEXT("field-cal-1");
    return Result;
}

bool LoadView(FDeferredTeleopArticulatedViewState& View)
{
    FString Json, Error;
    return FFileHelper::LoadFileToString(Json, *(FPaths::ProjectDir()
        / TEXT("../../fixtures/m2/articulated-state/valid-articulated-view.json")))
        && DeferredTeleop::ArticulatedView::ParseArticulated(Json, View, Error);
}

FDttGoalAuthoringSettings Settings(const FDeferredTeleopArticulatedViewState& View)
{
    FDttGoalAuthoringSettings Result;
    for (const FDeferredTeleopArticulatedJointPosition& Joint : View.ConfirmedRobotState.Joints)
    {
        FDttPreviewJointVelocity Speed;
        Speed.JointName = FName(*Joint.JointName);
        Speed.MaximumRadiansPerSecond = 0.5;
        Result.Preview.JointVelocities.Add(Speed);
    }
    return Result;
}

UDeferredTeleopGoalAuthoringComponent* NewConfigured(FAutomationTestBase& Test)
{
    FDeferredTeleopArticulatedViewState View;
    if (!Test.TestTrue(TEXT("fixture loads through production parser"), LoadView(View)))
    {
        return nullptr;
    }
    auto* Component = NewObject<UDeferredTeleopGoalAuthoringComponent>(GetTransientPackage());
    FString Error;
    if (!Test.TestTrue(TEXT("configure confirmed source"),
            Component->ConfigureFromConfirmedView(Binding(), View, Settings(View), Error)))
    {
        Test.AddError(Error);
        return nullptr;
    }
    return Component;
}

bool GoalAtOffset(UDeferredTeleopGoalAuthoringComponent* Component, double Offset,
    FDttCanonicalTransform& OutGoal)
{
    FDttRobotDescription Description;
    FDttCanonicalTransform Root;
    TArray<FDttNamedJointPosition> Joints;
    if (!Component->GetSourceModelAndState(Description, Root, Joints)) { return false; }
    for (FDttNamedJointPosition& Joint : Joints)
    {
        if (Joint.JointName == FName(TEXT("shoulder_pan"))) { Joint.PositionRadians += Offset; }
    }
    FDttForwardKinematicsResult FK;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(Description, Root, Joints, FK))
    {
        return false;
    }
    for (const FDttNamedCanonicalTransform& Tool : FK.ToolTransforms)
    {
        if (Tool.Name == FName(TEXT("gripper_frame_link"))) { OutGoal = Tool.Transform; return true; }
    }
    return false;
}

bool SolveAtStart(UDeferredTeleopGoalAuthoringComponent* Component, double Now)
{
    FString Error;
    if (!Component->QueueCanonicalGoal(Component->StartToolTransform, EDttIKMode::PositionOnly, Error))
    {
        return false;
    }
    FDttGoalAuthoringTestAccess::Pump(Component, Now);
    return Component->HasCurrentPreview();
}
} // namespace DeferredTeleop::Tests::GoalAuthoring
namespace DttAuthoringTest = DeferredTeleop::Tests::GoalAuthoring;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringConfigureTest,
    "DeferredTeleop.M2.GoalAuthoring.ConfigureAndPreview",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringConfigureTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    TestFalse(TEXT("configuration does not fabricate a candidate"), C->HasCurrentPreview());
    TestTrue(TEXT("current tool is a valid goal"), DttAuthoringTest::SolveAtStart(C, 100.0));
    TestEqual(TEXT("zero motion has one sample"), C->LastValidPreview.Samples.Num(), 1);
    TestEqual(TEXT("input snapshot revision retained"), C->LastValidPreview.SourceReference.Evidence.WorldRevision, 7);
    TestTrue(TEXT("duration measured"), FMath::IsFinite(C->LastSolveMilliseconds));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringLatestWinsTest,
    "DeferredTeleop.M2.GoalAuthoring.LatestWinsAndRateLimit",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringLatestWinsTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    FString Error;
    FDttCanonicalTransform Far = C->StartToolTransform; Far.TranslationMetres.X += 100.0;
    C->QueueCanonicalGoal(Far, EDttIKMode::PositionOnly, Error);
    C->QueueCanonicalGoal(C->StartToolTransform, EDttIKMode::PositionOnly, Error);
    FDttGoalAuthoringTestAccess::Pump(C, 100.0);
    TestTrue(TEXT("only latest reachable goal was solved"), C->HasCurrentPreview());
    TestEqual(TEXT("one solve"), C->SolveCount, 1);
    C->QueueCanonicalGoal(C->StartToolTransform, EDttIKMode::PositionOnly, Error);
    TestFalse(TEXT("new input immediately invalidates current candidate"), C->HasCurrentPreview());
    FDttGoalAuthoringTestAccess::Pump(C, 100.01);
    TestEqual(TEXT("20Hz cap respected"), C->SolveCount, 1);
    FDttGoalAuthoringTestAccess::Pump(C, 100.06);
    TestEqual(TEXT("one more solve, not a queued burst"), C->SolveCount, 2);
    FDttGoalAuthoringTestAccess::Pump(C, 900.0);
    TestEqual(TEXT("no catch-up without new input"), C->SolveCount, 2);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringRejectTest,
    "DeferredTeleop.M2.GoalAuthoring.InvalidAndFailedGoalRetainsDrawing",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringRejectTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    if (!TestTrue(TEXT("baseline"), DttAuthoringTest::SolveAtStart(C, 1.0))) { return false; }
    const FGuid Old = C->LastValidPreview.PreviewId;
    FString Error;
    FDttCanonicalTransform Bad = C->StartToolTransform;
    Bad.TranslationMetres.X = std::numeric_limits<double>::quiet_NaN();
    TestFalse(TEXT("NaN rejected"), C->QueueCanonicalGoal(Bad, EDttIKMode::PositionOnly, Error));
    TestEqual(TEXT("old drawing retained"), C->LastValidPreview.PreviewId, Old);
    TestFalse(TEXT("old drawing not current"), C->HasCurrentPreview());
    Bad = C->StartToolTransform; Bad.TranslationMetres.X += 100.0;
    TestTrue(TEXT("finite far goal accepted for solving"), C->QueueCanonicalGoal(Bad, EDttIKMode::PositionOnly, Error));
    FDttGoalAuthoringTestAccess::Pump(C, 2.0);
    TestFalse(TEXT("nonconverged goal not accepted by default"), C->HasCurrentPreview());
    TestEqual(TEXT("failed solve retains baseline"), C->LastValidPreview.PreviewId, Old);
    FDttKinematicPreview Copy;
    TestFalse(TEXT("stale freeze refused"), C->CopyCurrentPreview(Copy, Error));
    TestFalse(TEXT("failure output cleared"), Copy.bValid);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringRebaseTest,
    "DeferredTeleop.M2.GoalAuthoring.SourceRebaseAndIdentityGuards",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringRebaseTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    TestTrue(TEXT("baseline"), DttAuthoringTest::SolveAtStart(C, 1.0));
    FDeferredTeleopArticulatedViewState View; if (!DttAuthoringTest::LoadView(View)) { return false; }
    const auto Original = View;
    View.ConfirmedRobotState.ModelReference.ModelId = TEXT("SO101_NEW_CALIB");
    FString Error;
    TestFalse(TEXT("case-sensitive model mismatch rejected"),
        C->ConfigureFromConfirmedView(DttAuthoringTest::Binding(), View, DttAuthoringTest::Settings(View), Error));
    TestFalse(TEXT("failed rebase disables authoring"), C->bConfigured);
    TestTrue(TEXT("last drawing remains available"), C->LastValidPreview.bValid);
    TestFalse(TEXT("old source cannot be used"), C->QueueCanonicalGoal(C->StartToolTransform, EDttIKMode::PositionOnly, Error));
    TestTrue(TEXT("explicit valid rebase recovers"),
        C->ConfigureFromConfirmedView(DttAuthoringTest::Binding(), Original, DttAuthoringTest::Settings(Original), Error));
    TestFalse(TEXT("successful rebase clears candidate"), C->LastValidPreview.bValid);
    TestTrue(TEXT("recovered baseline"), DttAuthoringTest::SolveAtStart(C, 2.0));
    TestFalse(TEXT("malformed fixture is not a source"),
        C->ConfigureFromConfirmedJson(DttAuthoringTest::Binding(), TEXT("{}"),
            DttAuthoringTest::Settings(Original), Error));
    TestFalse(TEXT("malformed rebase disables source"), C->bConfigured);
    TestFalse(TEXT("malformed rebase cannot freeze old drawing"), C->HasCurrentPreview());
    FString Json;
    TestTrue(TEXT("read the known fixture bytes"), FFileHelper::LoadFileToString(Json,
        *(FPaths::ProjectDir() / TEXT("../../fixtures/m2/articulated-state/valid-articulated-view.json"))));
    TestTrue(TEXT("explicit fixture rebase uses production parser"),
        C->ConfigureFromConfirmedJson(DttAuthoringTest::Binding(), Json,
            DttAuthoringTest::Settings(Original), Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringFrameTest,
    "DeferredTeleop.M2.GoalAuthoring.UnrealFrameAndScaleBoundary",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringFrameTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    FTransform Local; FString Error;
    TestTrue(TEXT("canonical -> Unreal"), DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(C->StartToolTransform, Local, Error));
    const FTransform Anchor(FQuat(FVector::UpVector, 0.3), FVector(50.0, -20.0, 80.0));
    const FTransform Target = Local * Anchor;
    TestTrue(TEXT("non-identity presentation anchor"), C->QueueUnrealGoal(Target, Anchor, EDttIKMode::PositionPlusApproachAxis, Error));
    FDttGoalAuthoringTestAccess::Pump(C, 1.0);
    TestTrue(TEXT("round trip goal converges"), C->HasCurrentPreview());
    TestTrue(TEXT("metres not scaled twice"), C->LastIKResult.PositionResidualMetres < 1.0e-8);
    FTransform Scaled = Target; Scaled.SetScale3D(FVector(2.0));
    TestFalse(TEXT("scaled target rejected"), C->QueueUnrealGoal(Scaled, Anchor, EDttIKMode::PositionOnly, Error));
    TestFalse(TEXT("scaled anchor rejected"), C->QueueUnrealGoal(Target, Scaled, EDttIKMode::PositionOnly, Error));
    TestFalse(TEXT("bad input invalidates prior candidate"), C->HasCurrentPreview());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringWarmStartTest,
    "DeferredTeleop.M2.GoalAuthoring.WarmStartKeepsOriginalSource",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringWarmStartTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    FString Error;
    for (int32 Index = 1; Index <= 2; ++Index)
    {
        FDttCanonicalTransform Goal;
        if (!DttAuthoringTest::GoalAtOffset(C, 0.05 * Index, Goal)) { return false; }
        C->QueueCanonicalGoal(Goal, EDttIKMode::PositionOnly, Error);
        FDttGoalAuthoringTestAccess::Pump(C, static_cast<double>(Index));
        TestTrue(TEXT("nearby reachable goal converged"), C->HasCurrentPreview());
        for (const auto& Joint : C->LastValidPreview.StartJointPositions)
        {
            if (Joint.JointName == TEXT("shoulder_pan")) { TestEqual(TEXT("preview starts at snapshot not prior candidate"), Joint.PositionRadians, 0.1); }
        }
        for (const auto& Joint : C->LastValidPreview.GoalJointPositions)
        {
            if (Joint.JointName == TEXT("gripper")) { TestEqual(TEXT("inactive gripper retained exactly"), Joint.PositionRadians, 0.6); }
        }
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringCopyTest,
    "DeferredTeleop.M2.GoalAuthoring.CopyClearAndSettings",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringCopyTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    TestTrue(TEXT("baseline"), DttAuthoringTest::SolveAtStart(C, 1.0));
    FString Error; FDttKinematicPreview Frozen;
    TestTrue(TEXT("copy candidate"), C->CopyCurrentPreview(Frozen, Error));
    C->ClearCandidate();
    TestFalse(TEXT("clear removes current preview"), C->HasCurrentPreview());
    TestTrue(TEXT("frozen copy is independent"), Frozen.bValid);
    TestTrue(TEXT("clear keeps configured source"), C->bConfigured);
    FDeferredTeleopArticulatedViewState View; if (!DttAuthoringTest::LoadView(View)) { return false; }
    FDttGoalAuthoringSettings Bad = DttAuthoringTest::Settings(View); Bad.MaximumSolveRateHz = 0.0;
    TestFalse(TEXT("invalid rate rejected"), C->ConfigureFromConfirmedView(DttAuthoringTest::Binding(), View, Bad, Error));
    Bad = DttAuthoringTest::Settings(View); Bad.Preview.JointVelocities.Reset();
    TestFalse(TEXT("missing preview speeds rejected before editing"), C->ConfigureFromConfirmedView(DttAuthoringTest::Binding(), View, Bad, Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttAuthoringClockTest,
    "DeferredTeleop.M2.GoalAuthoring.ClockRollbackAndMissingSource",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttAuthoringClockTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* C = NewObject<UDeferredTeleopGoalAuthoringComponent>(GetTransientPackage());
    FString Error;
    TestFalse(TEXT("unconfigured queue rejected"), C->QueueCanonicalGoal(FDttCanonicalTransform::Identity(), EDttIKMode::PositionOnly, Error));
    C = DttAuthoringTest::NewConfigured(*this); if (!C) { return false; }
    TestTrue(TEXT("baseline"), DttAuthoringTest::SolveAtStart(C, 100.0));
    C->QueueCanonicalGoal(C->StartToolTransform, EDttIKMode::PositionOnly, Error);
    FDttGoalAuthoringTestAccess::Pump(C, 10.0);
    TestEqual(TEXT("clock rollback does not solve immediately"), C->SolveCount, 1);
    FDttGoalAuthoringTestAccess::Pump(C, 10.06);
    TestEqual(TEXT("one solve after new interval"), C->SolveCount, 2);
    FDttGoalAuthoringTestAccess::Pump(C, std::numeric_limits<double>::infinity());
    TestFalse(TEXT("invalid clock invalidates candidate"), C->HasCurrentPreview());
    return true;
}

#endif
