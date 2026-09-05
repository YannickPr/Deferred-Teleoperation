#if WITH_DEV_AUTOMATION_TESTS

#include "Authoring/DeferredTeleopAuthoringWorkbench.h"
#include "Components/SceneComponent.h"
#include "Misc/AutomationTest.h"
#include "UObject/UObjectGlobals.h"
#include "Visualization/DeferredTeleopKinematicRobotActor.h"

struct FDttAuthoringWorkbenchTestAccess
{
    static void Pump(ADeferredTeleopAuthoringWorkbench* Workbench, double Now)
    {
        Workbench->Authoring->ProcessPendingAt(Now);
    }

    static ADeferredTeleopAuthoringWorkbench* Make(FAutomationTestBase& Test)
    {
        auto* Workbench = NewObject<ADeferredTeleopAuthoringWorkbench>(GetTransientPackage());
        // As in the existing actor tests, use transient production actors without a viewport.
        Workbench->ReferenceRobot = NewObject<ADeferredTeleopKinematicRobotActor>(Workbench);
        Workbench->CandidateRobot = NewObject<ADeferredTeleopKinematicRobotActor>(Workbench);
        FString Error;
        if (!Test.TestTrue(TEXT("initialize actual committed fixture"), Workbench->InitializeSyntheticFixture(Error)))
        {
            Test.AddError(Error);
            return nullptr;
        }
        return Workbench;
    }
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchDormantTest,
    "DeferredTeleop.M2.Workbench.DormantByDefault",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchDormantTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = NewObject<ADeferredTeleopAuthoringWorkbench>(GetTransientPackage());
    TestFalse(TEXT("no automatic fixture activation"), W->bInitializeSyntheticFixtureOnBeginPlay);
    TestFalse(TEXT("not ready by construction"), W->bReady);
    TestFalse(TEXT("no model configured by construction"), W->Authoring->bConfigured);
    FString Error;
    TestFalse(TEXT("unconfigured edit refused"), W->BeginTargetEdit(Error));
    TestFalse(TEXT("unconfigured reset refused"), W->ResetTargetToSource(Error));
    TestFalse(TEXT("unconfigured freeze refused"), W->EndTargetEdit(true, Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchInitializationTest,
    "DeferredTeleop.M2.Workbench.InitializeAndReload",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchInitializationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    TestTrue(TEXT("ready"), W->bReady);
    TestTrue(TEXT("owned visual copies are distinct"), W->ReferenceRobot != W->CandidateRobot);
    TestTrue(TEXT("reference has a valid FK pose"), W->ReferenceRobot->bHasValidPose);
    TestFalse(TEXT("no candidate invented by initialization"), W->Authoring->HasCurrentPreview());
    FTransform Tool; FString Error;
    TestTrue(TEXT("source tool available"), W->ReferenceRobot->GetToolTransform(TEXT("gripper_frame_link"), Tool, Error));
    TestTrue(TEXT("handle starts at source tool"), Tool.Equals(W->TargetHandle->GetComponentTransform(), 1.0e-6));
    auto* Previous = W->ReferenceRobot.Get();
    TestTrue(TEXT("explicit reload"), W->InitializeSyntheticFixture(Error));
    TestTrue(TEXT("reuses visual actor instead of leaking a new one"), Previous == W->ReferenceRobot.Get());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchReleaseTest,
    "DeferredTeleop.M2.Workbench.ReleaseWaitsForFinalResult",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchReleaseTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    TestTrue(TEXT("edit begins"), W->BeginTargetEdit(Error));
    TestTrue(TEXT("release queued"), W->EndTargetEdit(true, Error));
    TestTrue(TEXT("freeze pending before solve"), W->bFreezePending);
    TestFalse(TEXT("not frozen before solve"), W->bHasFrozenPreview);
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    TestTrue(TEXT("final current result frozen locally"), W->bHasFrozenPreview);
    TestFalse(TEXT("freeze request resolved"), W->bFreezePending);
    TestEqual(TEXT("frozen goal matches exact accepted goal"), W->FrozenPreview.GoalId, W->Authoring->LastValidPreview.GoalId);
    TestTrue(TEXT("candidate pose applied"), W->CandidateRobot->bHasValidPose);
    TestFalse(TEXT("duplicate release has no edit"), W->EndTargetEdit(true, Error));
    TestEqual(TEXT("duplicate release causes no extra solve"), W->Authoring->SolveCount, 1);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchFailureTest,
    "DeferredTeleop.M2.Workbench.FailedFinalGoalNeverFreezesOldResult",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchFailureTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    W->BeginTargetEdit(Error);
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    const FGuid Old = W->Authoring->LastValidPreview.GoalId;
    TestTrue(TEXT("baseline current"), W->Authoring->HasCurrentPreview());
    W->TargetHandle->AddWorldOffset(FVector(10000, 0, 0));
    W->EndTargetEdit(true, Error);
    FDttAuthoringWorkbenchTestAccess::Pump(W, 2.0);
    TestFalse(TEXT("not frozen on failed final solve"), W->bHasFrozenPreview);
    TestFalse(TEXT("pending freeze cleared"), W->bFreezePending);
    TestFalse(TEXT("old solve is not current"), W->Authoring->HasCurrentPreview());
    TestEqual(TEXT("old drawing remains explicitly old"), W->Authoring->LastValidPreview.GoalId, Old);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchCancelTest,
    "DeferredTeleop.M2.Workbench.CancelClearsPendingFreeze",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchCancelTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    W->BeginTargetEdit(Error);
    W->EndTargetEdit(true, Error);
    W->CancelLocalEdit();
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    TestFalse(TEXT("no freeze after tracking loss/cancel"), W->bHasFrozenPreview);
    TestFalse(TEXT("no pending edit"), W->bFreezePending || W->bEditing || W->Authoring->bHasPendingGoal);
    TestEqual(TEXT("cancelled work not solved"), W->Authoring->SolveCount, 0);
    TestTrue(TEXT("source remains available"), W->bReady);
    TestTrue(TEXT("reset to source works"), W->ResetTargetToSource(Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchStageTest,
    "DeferredTeleop.M2.Workbench.StageAndModeGuards",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchStageTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    TestTrue(TEXT("approach mode supported"), W->SetGoalMode(EDttIKMode::PositionPlusApproachAxis, Error));
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    TestTrue(TEXT("source tool approach converges"), W->Authoring->HasCurrentPreview());
    TestFalse(TEXT("invalid mode rejected"), W->SetGoalMode(static_cast<EDttIKMode>(255), Error));
    W->SetActorScale3D(FVector(2.0));
    TestFalse(TEXT("scaled stage cannot initialize"), W->InitializeSyntheticFixture(Error));
    TestFalse(TEXT("failed init disables workbench"), W->bReady);
    W->SetActorTransform(FTransform(FVector(100, 0, 0)));
    TestFalse(TEXT("translated stage explicitly refused"), W->InitializeSyntheticFixture(Error));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchLateHandleTest,
    "DeferredTeleop.M2.Workbench.HandleChangeDuringReleaseInvalidatesCandidate",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchLateHandleTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    W->BeginTargetEdit(Error);
    W->EndTargetEdit(true, Error);
    W->TargetHandle->AddWorldOffset(FVector(1, 0, 0)); // unqueued controller write after release
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    TestFalse(TEXT("older queued pose is not frozen"), W->bHasFrozenPreview);
    TestFalse(TEXT("older queued pose is not current"), W->Authoring->HasCurrentPreview());
    TestFalse(TEXT("pending freeze cleared"), W->bFreezePending);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FDttWorkbenchDisplayFailureTest,
    "DeferredTeleop.M2.Workbench.DisplayFailureBlocksFreeze",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FDttWorkbenchDisplayFailureTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    auto* W = FDttAuthoringWorkbenchTestAccess::Make(*this); if (!W) { return false; }
    FString Error;
    W->BeginTargetEdit(Error);
    W->EndTargetEdit(true, Error);
    W->CandidateRobot = nullptr;
    FDttAuthoringWorkbenchTestAccess::Pump(W, 1.0);
    TestTrue(TEXT("math can succeed independently"), W->Authoring->HasCurrentPreview());
    TestFalse(TEXT("unseen candidate not automatically frozen"), W->bHasFrozenPreview);
    TestTrue(TEXT("presentation failure not overwritten by success diagnostic"), W->StatusText.Contains(TEXT("display failed")));
    return true;
}

#endif
