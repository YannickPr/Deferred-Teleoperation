#if WITH_DEV_AUTOMATION_TESTS

#include "Articulated/DeferredTeleopArticulatedSceneActor.h"

#include "Articulated/DeferredTeleopArticulatedSceneValidation.h"
#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "DeferredTeleopMissionClientComponent.h"
#include "HAL/FileManager.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Visualization/DeferredTeleopKinematicRobotActor.h"
#include "UObject/UObjectGlobals.h"
#include "Templates/Function.h"

#include <limits>

struct FDeferredTeleopMissionClientTestAccess
{
    static void ConfigureArticulated(
        UDeferredTeleopMissionClientComponent* Client,
        const uint64 Generation)
    {
        Client->ActiveWireMode = EDeferredTeleopMissionWireMode::ArticulatedView;
        Client->ConnectionGeneration = Generation;
        Client->LastSequenceBySourceId.Reset();
    }

    static void Deliver(
        UDeferredTeleopMissionClientComponent* Client,
        const uint64 Generation,
        const FString& Json)
    {
        Client->HandleMessage(Generation, Json);
    }

    static void StartNewGeneration(
        UDeferredTeleopMissionClientComponent* Client,
        const uint64 Generation)
    {
        Client->ConnectionGeneration = Generation;
        Client->LastSequenceBySourceId.Reset();
    }
};

struct FDeferredTeleopArticulatedSceneTestAccess
{
    static void MarkDisconnected(ADeferredTeleopArticulatedSceneActor* Scene)
    {
        Scene->MarkDisconnected();
    }
};

namespace DeferredTeleop::Tests::ArticulatedScene
{

FString FixturePath(const TCHAR* Name)
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../fixtures/m2/articulated-state/") / Name);
}

FString DescriptionPath()
{
    return FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../robots/so101/generated/so101.kinematics.json"));
}

bool LoadText(const FString& Path, FString& OutText)
{
    return FFileHelper::LoadFileToString(OutText, *Path);
}

bool LoadView(const TCHAR* Name, FDeferredTeleopArticulatedViewState& OutView, FString& OutError)
{
    FString Json;
    if (!LoadText(FixturePath(Name), Json))
    {
        OutError = FString::Printf(TEXT("could not load fixture %s"), Name);
        return false;
    }
    return DeferredTeleop::ArticulatedView::ParseArticulated(Json, OutView, OutError);
}

ADeferredTeleopArticulatedSceneActor* NewConfiguredScene(FString& OutError)
{
    ADeferredTeleopArticulatedSceneActor* Scene =
        NewObject<ADeferredTeleopArticulatedSceneActor>(GetTransientPackage());
    Scene->MissionClient->bAutoConnect = false;
    Scene->ConfirmedActor = NewObject<ADeferredTeleopKinematicRobotActor>(Scene);
    Scene->ArrivalActor = NewObject<ADeferredTeleopKinematicRobotActor>(Scene);
    Scene->TargetActor = NewObject<ADeferredTeleopKinematicRobotActor>(Scene);

    FDeferredTeleopArticulatedModelBinding Binding;
    Binding.RobotId = TEXT("so101-follower-1");
    Binding.DescriptionFilePath = DescriptionPath();
    Binding.ExpectedFrameId = TEXT("field-world");
    Binding.ExpectedCalibrationVersion = TEXT("field-cal-1");
    if (!Scene->ConfigureBinding(Binding, OutError))
    {
        return nullptr;
    }
    return Scene;
}

bool ApplyFixture(
    ADeferredTeleopArticulatedSceneActor* Scene,
    const TCHAR* Name,
    FDeferredTeleopArticulatedViewState& OutView,
    FString& OutError)
{
    FString Json;
    if (!LoadText(FixturePath(Name), Json))
    {
        OutError = FString::Printf(TEXT("could not load fixture %s"), Name);
        return false;
    }
    if (!DeferredTeleop::ArticulatedView::ParseArticulated(Json, OutView, OutError))
    {
        return false;
    }
    return Scene->ApplyArticulatedViewJson(Json, OutError);
}

} // namespace DeferredTeleop::Tests::ArticulatedScene

namespace DttArticulatedSceneTest = DeferredTeleop::Tests::ArticulatedScene;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneSha256Test,
    "DeferredTeleop.M2.ArticulatedScene.Sha256RawBytes",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneSha256Test::RunTest(const FString& Parameters)
{
    (void)Parameters;
    TArray<uint8> Empty;
    FString Hash;
    FString Error;
    TestTrue(
        TEXT("OpenSSL hashes empty bytes"),
        DeferredTeleop::ArticulatedScene::ComputeDescriptionHash(Empty, Hash, Error));
    TestEqual(
        TEXT("empty SHA-256 is exact"),
        Hash,
        FString(TEXT("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")));

    const uint8 AbcBytes[] = {'a', 'b', 'c'};
    TArray<uint8> Abc;
    Abc.Append(AbcBytes, UE_ARRAY_COUNT(AbcBytes));
    Error.Reset();
    TestTrue(
        TEXT("OpenSSL hashes abc bytes"),
        DeferredTeleop::ArticulatedScene::ComputeDescriptionHash(Abc, Hash, Error));
    TestEqual(
        TEXT("abc SHA-256 is exact"),
        Hash,
        FString(TEXT("sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")));

    TArray<uint8> FixtureBytes;
    TestTrue(
        TEXT("the real SO101 fixture loads as bytes"),
        FFileHelper::LoadFileToArray(FixtureBytes, *DttArticulatedSceneTest::DescriptionPath()));
    Error.Reset();
    TestTrue(
        TEXT("the real fixture hashes"),
        DeferredTeleop::ArticulatedScene::ComputeDescriptionHash(FixtureBytes, Hash, Error));
    TestEqual(
        TEXT("fixture hash covers exact committed bytes"),
        Hash,
        FString(TEXT("sha256:36ce321332248351f5304630a9ccc4887d6665666e17b6933e3302874735e5f2")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneReferenceAndCaseTest,
    "DeferredTeleop.M2.ArticulatedScene.ReferenceAndCaseValidation",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneReferenceAndCaseTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    ADeferredTeleopArticulatedSceneActor* Scene = DttArticulatedSceneTest::NewConfiguredScene(Error);
    if (!TestTrue(TEXT("scene configures from local catalogue"), Scene != nullptr))
    {
        AddError(Error);
        return false;
    }

    FDeferredTeleopArticulatedViewState View;
    if (!TestTrue(TEXT("three-layer fixture parses"), DttArticulatedSceneTest::LoadView(
                      TEXT("valid-articulated-view.json"), View, Error)))
    {
        AddError(Error);
        return false;
    }

    DeferredTeleop::ArticulatedScene::FPreparedLayerState Prepared;
    TestTrue(
        TEXT("production validation accepts the reference"),
        DeferredTeleop::ArticulatedScene::PrepareLayerState(
            Scene->ModelBinding,
            View.ConfirmedRobotState,
            Prepared,
            Error));
    int32 OrderedRevoluteCount = 0;
    for (const FDttRobotJointDescription& Joint : Scene->ModelBinding.Description.Joints)
    {
        if (Joint.Type == EDttRobotJointType::Revolute)
        {
            TestTrue(
                *FString::Printf(TEXT("joint %s follows description order"), *Joint.Name.ToString()),
                Prepared.OrderedJointPositions.IsValidIndex(OrderedRevoluteCount)
                    && Prepared.OrderedJointPositions[OrderedRevoluteCount].JointName == Joint.Name);
            ++OrderedRevoluteCount;
        }
    }

    FDeferredTeleopArticulatedRobotState WrongCase = View.ConfirmedRobotState;
    WrongCase.Joints[0].JointName = TEXT("SHOULDER_PAN");
    Prepared = DeferredTeleop::ArticulatedScene::FPreparedLayerState();
    Error.Reset();
    TestFalse(
        TEXT("joint name casing is rejected before FName conversion"),
        DeferredTeleop::ArticulatedScene::PrepareLayerState(
            Scene->ModelBinding,
            WrongCase,
            Prepared,
            Error));
    TestTrue(TEXT("casing diagnostic is explicit"), Error.Contains(TEXT("casing mismatch")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneInvalidModelTest,
    "DeferredTeleop.M2.ArticulatedScene.InvalidModelPreservesLastGood",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneInvalidModelTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    ADeferredTeleopArticulatedSceneActor* Scene = DttArticulatedSceneTest::NewConfiguredScene(Error);
    if (!TestTrue(TEXT("scene configures"), Scene != nullptr))
    {
        AddError(Error);
        return false;
    }
    FDeferredTeleopArticulatedViewState Valid;
    if (!TestTrue(TEXT("baseline fixture applies"), DttArticulatedSceneTest::ApplyFixture(
                      Scene, TEXT("valid-articulated-view.json"), Valid, Error)))
    {
        AddError(Error);
        return false;
    }

    FTransform Before;
    TestTrue(
        TEXT("baseline confirmed root is queryable"),
        Scene->ConfirmedActor->GetLinkTransform(FName(TEXT("base_link")), Before, Error));

    const auto CheckMissingEvidence = [this, Scene, &Valid, &Before](
                                           const TCHAR* Label,
                                           TFunction<void(FDeferredTeleopEvidence&)>&& Mutate)
    {
        FDeferredTeleopArticulatedViewState Candidate = Valid;
        Mutate(Candidate.ConfirmedRobotState.Evidence);
        FString CandidateError;
        TestFalse(Label, Scene->ApplyArticulatedViewState(Candidate, CandidateError));
        TestEqual(
            *FString::Printf(TEXT("%s has InvalidLayer reason"), Label),
            Scene->ConfirmedStatus.Reason,
            FString(TEXT("InvalidLayer")));
        FTransform After;
        TestTrue(
            *FString::Printf(TEXT("%s preserves last pose"), Label),
            Scene->ConfirmedActor->GetLinkTransform(FName(TEXT("base_link")), After, CandidateError));
        TestTrue(
            *FString::Printf(TEXT("%s preserves exact root"), Label),
            Before.Equals(After, 1.0e-4F));
    };

    CheckMissingEvidence(
        TEXT("unknown provenance is rejected"),
        [](FDeferredTeleopEvidence& Evidence)
        {
            Evidence.Provenance = EDeferredTeleopProvenance::Unknown;
        });
    CheckMissingEvidence(
        TEXT("empty evidence sources are rejected"),
        [](FDeferredTeleopEvidence& Evidence)
        {
            Evidence.SourceIds.Reset();
        });
    CheckMissingEvidence(
        TEXT("zero world revision is rejected"),
        [](FDeferredTeleopEvidence& Evidence)
        {
            Evidence.WorldRevision = 0;
        });
    CheckMissingEvidence(
        TEXT("default observed timestamp is rejected"),
        [](FDeferredTeleopEvidence& Evidence)
        {
            Evidence.ObservedAt = FDateTime();
        });
    CheckMissingEvidence(
        TEXT("default produced timestamp is rejected"),
        [](FDeferredTeleopEvidence& Evidence)
        {
            Evidence.ProducedAt = FDateTime();
        });

    const auto CheckInvalidModel = [this, Scene, &Valid, &Before](
                                       const TCHAR* Label,
                                       TFunction<void(FDeferredTeleopArticulatedRobotState&)>&& Mutate)
    {
        FDeferredTeleopArticulatedViewState Candidate = Valid;
        Mutate(Candidate.ConfirmedRobotState);
        FString CandidateError;
        TestFalse(Label, Scene->ApplyArticulatedViewState(Candidate, CandidateError));
        TestEqual(
            *FString::Printf(TEXT("%s has InvalidModel reason"), Label),
            Scene->ConfirmedStatus.Reason,
            FString(TEXT("InvalidModel")));
        FTransform After;
        TestTrue(
            *FString::Printf(TEXT("%s preserves last pose"), Label),
            Scene->ConfirmedActor->GetLinkTransform(FName(TEXT("base_link")), After, CandidateError));
        TestTrue(
            *FString::Printf(TEXT("%s preserves exact root"), Label),
            Before.Equals(After, 1.0e-4F));
    };

    CheckInvalidModel(
        TEXT("model id mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.ModelReference.ModelId = TEXT("other-model");
        });
    CheckInvalidModel(
        TEXT("model revision mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.ModelReference.ModelRevision = TEXT("other-revision");
        });
    CheckInvalidModel(
        TEXT("description hash mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.ModelReference.DescriptionHash = TEXT("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        });
    CheckInvalidModel(
        TEXT("robot id mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RobotId = TEXT("other-robot");
        });
    CheckInvalidModel(
        TEXT("robot id casing mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RobotId = TEXT("SO101-FOLLOWER-1");
        });
    CheckInvalidModel(
        TEXT("frame id mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RootPose.FrameId = TEXT("other-world");
        });
    CheckInvalidModel(
        TEXT("frame id casing mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RootPose.FrameId = TEXT("FIELD-WORLD");
        });
    CheckInvalidModel(
        TEXT("calibration mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RootPose.CalibrationVersion = TEXT("other-calibration");
        });
    CheckInvalidModel(
        TEXT("calibration casing mismatch"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RootPose.CalibrationVersion = TEXT("FIELD-CAL-1");
        });
    CheckInvalidModel(
        TEXT("non-canonical root quaternion"),
        [](FDeferredTeleopArticulatedRobotState& State)
        {
            State.RootPose.Orientation = FQuat(0.0F, 0.0F, 0.0F, 0.0F);
        });

    const FString OriginalRobotId = Scene->ModelBinding.RobotId;
    const FString OriginalDescriptionPath = Scene->ModelBinding.DescriptionFilePath;
    const FString OriginalDescriptionHash = Scene->ModelBinding.CachedDescriptionHash;
    const FString OriginalModelKey = Scene->ModelBinding.CachedModelKey;
    const FString OriginalModelId = Scene->ModelBinding.Description.ModelId;
    const FString MissingDescriptionPath =
        FPaths::ProjectSavedDir() / TEXT("DeferredTeleop.ArticulatedScene.missing.json");
    IFileManager::Get().Delete(*MissingDescriptionPath, false, true, true);
    Scene->ModelBinding.RobotId = TEXT("candidate-robot");
    Scene->ModelBinding.DescriptionFilePath = MissingDescriptionPath;
    Error.Reset();
    TestFalse(
        TEXT("missing reload is rejected"),
        Scene->ReloadLocalDescription(Error));
    TestEqual(
        TEXT("missing reload reports InvalidModel"),
        Scene->ConfirmedStatus.Reason,
        FString(TEXT("InvalidModel")));
    TestEqual(
        TEXT("failed reload restores the committed robot id"),
        Scene->ModelBinding.RobotId,
        OriginalRobotId);
    TestEqual(
        TEXT("failed reload restores the committed path"),
        Scene->ModelBinding.DescriptionFilePath,
        OriginalDescriptionPath);
    TestEqual(
        TEXT("failed reload preserves the committed hash"),
        Scene->ModelBinding.CachedDescriptionHash,
        OriginalDescriptionHash);
    TestEqual(
        TEXT("failed reload preserves the committed model key"),
        Scene->ModelBinding.CachedModelKey,
        OriginalModelKey);
    TestEqual(
        TEXT("failed reload preserves the parsed catalogue"),
        Scene->ModelBinding.Description.ModelId,
        OriginalModelId);
    FTransform AfterMissing;
    TestTrue(
        TEXT("missing reload preserves last-good pose"),
        Scene->ConfirmedActor->GetLinkTransform(FName(TEXT("base_link")), AfterMissing, Error));
    TestTrue(
        TEXT("missing reload does not replace the pose"),
        Before.Equals(AfterMissing, 1.0e-4F));

    const FString InvalidUtf8Path =
        FPaths::ProjectSavedDir() / TEXT("DeferredTeleop.ArticulatedScene.invalid-utf8.bin");
    TArray<uint8> InvalidUtf8;
    InvalidUtf8.Add(0xC3);
    InvalidUtf8.Add(0x28);
    TestTrue(
        TEXT("invalid UTF-8 test file is written"),
        FFileHelper::SaveArrayToFile(InvalidUtf8, *InvalidUtf8Path));
    Scene->ModelBinding.RobotId = TEXT("candidate-robot");
    Scene->ModelBinding.DescriptionFilePath = InvalidUtf8Path;
    Error.Reset();
    TestFalse(
        TEXT("invalid UTF-8 reload is rejected"),
        Scene->ReloadLocalDescription(Error));
    TestTrue(
        TEXT("invalid UTF-8 reports a model diagnostic"),
        Scene->ConfirmedStatus.Reason == TEXT("InvalidModel")
            && Error.Contains(TEXT("UTF-8")));
    TestEqual(
        TEXT("invalid UTF-8 restores the committed robot id"),
        Scene->ModelBinding.RobotId,
        OriginalRobotId);
    TestEqual(
        TEXT("invalid UTF-8 preserves the committed hash"),
        Scene->ModelBinding.CachedDescriptionHash,
        OriginalDescriptionHash);
    TestEqual(
        TEXT("invalid UTF-8 preserves the committed model key"),
        Scene->ModelBinding.CachedModelKey,
        OriginalModelKey);
    TestEqual(
        TEXT("invalid UTF-8 preserves the parsed catalogue"),
        Scene->ModelBinding.Description.ModelId,
        OriginalModelId);
    FTransform AfterInvalidUtf8;
    TestTrue(
        TEXT("invalid UTF-8 preserves last-good pose"),
        Scene->ConfirmedActor->GetLinkTransform(FName(TEXT("base_link")), AfterInvalidUtf8, Error));
    TestTrue(
        TEXT("invalid UTF-8 does not replace the pose"),
        Before.Equals(AfterInvalidUtf8, 1.0e-4F));
    IFileManager::Get().Delete(*InvalidUtf8Path, false, true, true);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneRollbackTest,
    "DeferredTeleop.M2.ArticulatedScene.RootRollbackRestoresLastGood",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneRollbackTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    ADeferredTeleopArticulatedSceneActor* Scene = DttArticulatedSceneTest::NewConfiguredScene(Error);
    if (!TestTrue(TEXT("scene configures"), Scene != nullptr))
    {
        AddError(Error);
        return false;
    }
    FDeferredTeleopArticulatedViewState Valid;
    if (!TestTrue(TEXT("baseline fixture applies"), DttArticulatedSceneTest::ApplyFixture(
                      Scene, TEXT("valid-articulated-view.json"), Valid, Error)))
    {
        AddError(Error);
        return false;
    }

    FTransform BeforeRoot;
    FTransform BeforeTip;
    ADeferredTeleopKinematicRobotActor* ConfirmedActorBefore = Scene->ConfirmedActor;
    TestTrue(TEXT("old root query succeeds"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("base_link")), BeforeRoot, Error));
    TestTrue(TEXT("old tip query succeeds"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("gripper_link")), BeforeTip, Error));

    FDeferredTeleopArticulatedViewState Candidate = Valid;
    Candidate.ConfirmedRobotState.RootPose.PositionMetres.X = 0.45F;
    Scene->SetTestFailNextApply(true);
    Error.Reset();
    TestFalse(
        TEXT("forced candidate ApplyState failure is reported"),
        Scene->ApplyArticulatedViewState(Candidate, Error));
    TestTrue(TEXT("rollback is degraded but visible"), Scene->ConfirmedStatus.bDegraded);
    TestFalse(TEXT("rollback is not a model identity error"), Scene->ConfirmedStatus.bCritical);

    FTransform AfterRoot;
    FTransform AfterTip;
    TestTrue(TEXT("restored root query succeeds"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("base_link")), AfterRoot, Error));
    TestTrue(TEXT("restored tip query succeeds"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("gripper_link")), AfterTip, Error));
    TestTrue(TEXT("root is restored exactly"), BeforeRoot.Equals(AfterRoot, 1.0e-4F));
    TestTrue(TEXT("joints and pose are restored exactly"), BeforeTip.Equals(AfterTip, 1.0e-4F));
    TestTrue(
        TEXT("rollback keeps the persistent actor instance"),
        Scene->ConfirmedActor == ConfirmedActorBefore);
    TestTrue(TEXT("last-good pose remains available"), Scene->ConfirmedStatus.bHasLastGoodPose);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneThreeLayersTest,
    "DeferredTeleop.M2.ArticulatedScene.ThreeLayersNullAndOutlier",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneThreeLayersTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    ADeferredTeleopArticulatedSceneActor* Scene = DttArticulatedSceneTest::NewConfiguredScene(Error);
    if (!TestTrue(TEXT("scene configures"), Scene != nullptr))
    {
        AddError(Error);
        return false;
    }
    FDeferredTeleopArticulatedViewState Valid;
    if (!TestTrue(TEXT("three-layer fixture applies"), DttArticulatedSceneTest::ApplyFixture(
                      Scene, TEXT("valid-articulated-view.json"), Valid, Error)))
    {
        AddError(Error);
        return false;
    }
    TestTrue(TEXT("confirmed actor is available"), Scene->ConfirmedStatus.bAvailable);
    TestTrue(TEXT("arrival actor is available"), Scene->ArrivalStatus.bAvailable);
    TestTrue(TEXT("target actor is available"), Scene->TargetStatus.bAvailable);
    TestTrue(TEXT("confirmed layer is visible"), Scene->ConfirmedStatus.bVisible);
    TestTrue(TEXT("arrival layer is visible"), Scene->ArrivalStatus.bVisible);
    TestTrue(TEXT("target layer is visible"), Scene->TargetStatus.bVisible);

    FDeferredTeleopArticulatedViewState Live;
    if (!TestTrue(TEXT("live null-layer fixture parses"), DttArticulatedSceneTest::LoadView(
                      TEXT("live-articulated-view.json"), Live, Error)))
    {
        AddError(Error);
        return false;
    }
    // The checked-in disconnected envelope currently carries all three
    // layers as null.  Keep its connection and null-layer fields, while
    // exercising the documented live shape in which Confirmed remains the
    // last selected state and Arrival/Target are explicitly absent.
    Live.bHasConfirmedRobotState = true;
    Live.ConfirmedRobotState = Valid.ConfirmedRobotState;
    Error.Reset();
    TestTrue(
        TEXT("live null-layer view applies"),
        Scene->ApplyArticulatedViewState(Live, Error));
    TestTrue(TEXT("confirmed remains visible in live fixture"), Scene->ConfirmedStatus.bVisible);
    TestFalse(TEXT("null arrival is unavailable"), Scene->ArrivalStatus.bAvailable);
    TestFalse(TEXT("null arrival is hidden"), Scene->ArrivalStatus.bVisible);
    TestFalse(TEXT("null target is unavailable"), Scene->TargetStatus.bAvailable);
    TestFalse(TEXT("null target is hidden"), Scene->TargetStatus.bVisible);

    // Restore the complete fixture so the outlier test has a last-good pose.
    TestTrue(TEXT("three-layer fixture restores"), Scene->ApplyArticulatedViewState(Valid, Error));
    FTransform BeforeOutlier;
    TestTrue(TEXT("last-good confirmed pose is queryable"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("gripper_link")), BeforeOutlier, Error));
    FDeferredTeleopArticulatedViewState Outlier = Valid;
    Outlier.ConfirmedRobotState.Joints.Last().PositionRadians = 100.0;
    Error.Reset();
    TestFalse(TEXT("measured outlier is rejected before actor mutation"),
              Scene->ApplyArticulatedViewState(Outlier, Error));
    TestEqual(
        TEXT("confirmed outlier has its own reason"),
        Scene->ConfirmedStatus.Reason,
        FString(TEXT("MeasuredOutlier")));
    TestTrue(TEXT("outlier value and limit are visible"),
             Scene->ConfirmedStatus.Diagnostics.Num() > 0
                 && Scene->ConfirmedStatus.Diagnostics[0].Contains(TEXT("limits")));
    FTransform AfterOutlier;
    TestTrue(TEXT("outlier keeps last-good pose"), Scene->ConfirmedActor->GetLinkTransform(
                   FName(TEXT("gripper_link")), AfterOutlier, Error));
    TestTrue(TEXT("outlier does not clamp or mutate pose"), BeforeOutlier.Equals(AfterOutlier, 1.0e-4F));
    TestFalse(TEXT("outlier is distinct from InvalidModel"),
              Scene->ConfirmedStatus.Reason == TEXT("InvalidModel"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneSequenceGenerationTest,
    "DeferredTeleop.M2.ArticulatedScene.SourceSequenceAndConnectionGeneration",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneSequenceGenerationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    UDeferredTeleopMissionClientComponent* Client =
        NewObject<UDeferredTeleopMissionClientComponent>(GetTransientPackage());
    FString Json;
    TestTrue(TEXT("sequence fixture loads"), DttArticulatedSceneTest::LoadText(
                   DttArticulatedSceneTest::FixturePath(TEXT("valid-articulated-view.json")), Json));
    FDeferredTeleopMissionClientTestAccess::ConfigureArticulated(Client, 11);
    FDeferredTeleopMissionClientTestAccess::Deliver(Client, 11, Json);
    TestTrue(TEXT("first articulated sequence is accepted"), Client->bHasValidArticulatedState);
    TestEqual(TEXT("first sequence is retained"), Client->LastValidArticulatedState.SourceSequence, 4);

    FDeferredTeleopMissionClientTestAccess::Deliver(Client, 11, Json);
    TestEqual(TEXT("duplicate sequence does not replace state"), Client->LastValidArticulatedState.SourceSequence, 4);

    FString ReconnectedJson = Json.Replace(
        TEXT("\"source_sequence\": 4"),
        TEXT("\"source_sequence\": 1"));
    FDeferredTeleopMissionClientTestAccess::StartNewGeneration(Client, 12);
    FDeferredTeleopMissionClientTestAccess::Deliver(Client, 12, ReconnectedJson);
    TestEqual(TEXT("new connection resets source sequence order"), Client->LastValidArticulatedState.SourceSequence, 1);

    FDeferredTeleopMissionClientTestAccess::Deliver(Client, 11, Json);
    TestEqual(TEXT("old generation cannot restore a pose"), Client->LastValidArticulatedState.SourceSequence, 1);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedSceneStaleEvidenceTest,
    "DeferredTeleop.M2.ArticulatedScene.InvalidAndDisconnectedKeepEvidence",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedSceneStaleEvidenceTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    ADeferredTeleopArticulatedSceneActor* Scene = DttArticulatedSceneTest::NewConfiguredScene(Error);
    if (!TestTrue(TEXT("scene configures"), Scene != nullptr))
    {
        AddError(Error);
        return false;
    }
    FDeferredTeleopArticulatedViewState Valid;
    if (!TestTrue(TEXT("baseline fixture applies"), DttArticulatedSceneTest::ApplyFixture(
                      Scene, TEXT("valid-articulated-view.json"), Valid, Error)))
    {
        AddError(Error);
        return false;
    }
    const FDateTime ObservedAt = Scene->ConfirmedStatus.ObservedAt;
    const FDateTime ProducedAt = Scene->ConfirmedStatus.ProducedAt;
    const EDeferredTeleopProvenance Provenance = Scene->ConfirmedStatus.Provenance;
    const FDateTime ArrivalObservedAt = Scene->ArrivalStatus.ObservedAt;
    const FDateTime ArrivalProducedAt = Scene->ArrivalStatus.ProducedAt;
    const FDateTime ArrivalPredictedFor = Scene->ArrivalStatus.PredictedFor;
    const FString ArrivalForecastModelVersion = Scene->ArrivalStatus.EvidenceModelVersion;
    const FDateTime ArrivalFreshUntil = Scene->ArrivalStatus.FreshUntil;
    const FDateTime ArrivalEstimatedIntentArrivalAt =
        Scene->ArrivalStatus.EstimatedIntentArrivalAt;
    const double ArrivalLinkOneWayDelaySeconds =
        Scene->ArrivalStatus.LinkOneWayDelaySeconds;
    TestTrue(TEXT("arrival retains predicted_for presence"), Scene->ArrivalStatus.bHasPredictedFor);
    TestTrue(TEXT("arrival retains fresh_until presence"), Scene->ArrivalStatus.bHasFreshUntil);
    TestTrue(
        TEXT("arrival retains estimated intent arrival presence"),
        Scene->ArrivalStatus.bHasEstimatedIntentArrival);
    TestTrue(
        TEXT("arrival retains forecast model version"),
        Scene->ArrivalStatus.bHasEvidenceModelVersion
            && Scene->ArrivalStatus.EvidenceModelVersion == TEXT("articulated-predictor-fixture-1"));

    const auto CheckArrivalDirectInvariant = [this, Scene, &Valid, &ArrivalPredictedFor](
                                                 const TCHAR* Label,
                                                 TFunction<void(FDeferredTeleopArticulatedArrivalRobotState&)>&& Mutate)
    {
        FDeferredTeleopArticulatedViewState Candidate = Valid;
        Mutate(Candidate.ArrivalRobotState);
        FString CandidateError;
        TestFalse(Label, Scene->ApplyArticulatedViewState(Candidate, CandidateError));
        TestEqual(
            *FString::Printf(TEXT("%s has InvalidLayer reason"), Label),
            Scene->ArrivalStatus.Reason,
            FString(TEXT("InvalidLayer")));
        TestTrue(
            *FString::Printf(TEXT("%s preserves arrival pose"), Label),
            Scene->ArrivalStatus.bHasLastGoodPose);
        TestTrue(
            *FString::Printf(TEXT("%s preserves predicted_for"), Label),
            Scene->ArrivalStatus.PredictedFor == ArrivalPredictedFor);
    };

    CheckArrivalDirectInvariant(
        TEXT("direct arrival provenance is checked"),
        [](FDeferredTeleopArticulatedArrivalRobotState& Arrival)
        {
            Arrival.RobotState.Evidence.Provenance = EDeferredTeleopProvenance::OperatorAsserted;
        });
    CheckArrivalDirectInvariant(
        TEXT("direct arrival predicted_for ordering is checked"),
        [](FDeferredTeleopArticulatedArrivalRobotState& Arrival)
        {
            Arrival.PredictedFor = Arrival.RobotState.Evidence.ProducedAt;
        });
    CheckArrivalDirectInvariant(
        TEXT("direct arrival negative delay is checked"),
        [](FDeferredTeleopArticulatedArrivalRobotState& Arrival)
        {
            Arrival.LinkOneWayDelaySeconds = -1.0;
        });
    CheckArrivalDirectInvariant(
        TEXT("direct arrival non-finite delay is checked"),
        [](FDeferredTeleopArticulatedArrivalRobotState& Arrival)
        {
            Arrival.LinkOneWayDelaySeconds = std::numeric_limits<double>::quiet_NaN();
        });
    CheckArrivalDirectInvariant(
        TEXT("direct arrival estimated timestamp is checked"),
        [](FDeferredTeleopArticulatedArrivalRobotState& Arrival)
        {
            Arrival.bHasEstimatedIntentArrival = true;
            Arrival.EstimatedIntentArrivalAt = FDateTime();
        });

    FDeferredTeleopArticulatedViewState Invalid = Valid;
    Invalid.ConfirmedRobotState.Joints[0].JointName = TEXT("unknown_joint");
    Error.Reset();
    TestFalse(TEXT("invalid layer keeps last-good pose"),
              Scene->ApplyArticulatedViewState(Invalid, Error));
    TestTrue(TEXT("invalid layer is degraded"), Scene->ConfirmedStatus.bDegraded);
    TestTrue(TEXT("invalid layer remains visible from cache"), Scene->ConfirmedStatus.bVisible);
    TestTrue(
        TEXT("invalid layer keeps observed_at"),
        Scene->ConfirmedStatus.ObservedAt == ObservedAt);
    TestTrue(
        TEXT("invalid layer keeps produced_at"),
        Scene->ConfirmedStatus.ProducedAt == ProducedAt);
    TestTrue(
        TEXT("invalid layer keeps declared provenance"),
        Scene->ConfirmedStatus.Provenance == Provenance);
    TestTrue(
        TEXT("receipt age is separate from observation time"),
        Scene->ConfirmedStatus.ReceiptAgeSeconds >= 0.0
            && Scene->ConfirmedStatus.ObservedAt != FDateTime());
    TestTrue(
        TEXT("invalid message keeps arrival predicted_for"),
        Scene->ArrivalStatus.PredictedFor == ArrivalPredictedFor);
    TestTrue(
        TEXT("invalid message keeps arrival observed_at"),
        Scene->ArrivalStatus.ObservedAt == ArrivalObservedAt);
    TestTrue(
        TEXT("invalid message keeps arrival produced_at"),
        Scene->ArrivalStatus.ProducedAt == ArrivalProducedAt);
    TestEqual(
        TEXT("invalid message keeps arrival forecast model version"),
        Scene->ArrivalStatus.EvidenceModelVersion,
        ArrivalForecastModelVersion);
    TestTrue(
        TEXT("invalid message keeps arrival fresh_until"),
        Scene->ArrivalStatus.FreshUntil == ArrivalFreshUntil);
    TestTrue(
        TEXT("invalid message keeps estimated intent arrival"),
        Scene->ArrivalStatus.EstimatedIntentArrivalAt == ArrivalEstimatedIntentArrivalAt);
    TestTrue(
        TEXT("invalid message keeps one-way delay"),
        FMath::IsNearlyEqual(
            Scene->ArrivalStatus.LinkOneWayDelaySeconds,
            ArrivalLinkOneWayDelaySeconds));

    FDeferredTeleopArticulatedSceneTestAccess::MarkDisconnected(Scene);
    TestTrue(TEXT("disconnect is visibly stale"), Scene->ConfirmedStatus.bDegraded);
    TestTrue(
        TEXT("disconnect keeps evidence observed_at"),
        Scene->ConfirmedStatus.ObservedAt == ObservedAt);
    TestTrue(
        TEXT("disconnect keeps evidence produced_at"),
        Scene->ConfirmedStatus.ProducedAt == ProducedAt);
    TestTrue(TEXT("disconnect keeps cached pose visible"), Scene->ConfirmedStatus.bVisible);
    TestTrue(
        TEXT("disconnect keeps arrival predicted_for"),
        Scene->ArrivalStatus.PredictedFor == ArrivalPredictedFor);
    TestTrue(
        TEXT("disconnect keeps arrival observed_at"),
        Scene->ArrivalStatus.ObservedAt == ArrivalObservedAt);
    TestTrue(
        TEXT("disconnect keeps arrival produced_at"),
        Scene->ArrivalStatus.ProducedAt == ArrivalProducedAt);
    TestEqual(
        TEXT("disconnect keeps arrival forecast model version"),
        Scene->ArrivalStatus.EvidenceModelVersion,
        ArrivalForecastModelVersion);
    TestTrue(
        TEXT("disconnect keeps arrival fresh_until"),
        Scene->ArrivalStatus.FreshUntil == ArrivalFreshUntil);
    TestTrue(
        TEXT("disconnect keeps estimated intent arrival"),
        Scene->ArrivalStatus.EstimatedIntentArrivalAt == ArrivalEstimatedIntentArrivalAt);
    TestTrue(
        TEXT("disconnect keeps one-way delay"),
        FMath::IsNearlyEqual(
            Scene->ArrivalStatus.LinkOneWayDelaySeconds,
            ArrivalLinkOneWayDelaySeconds));

    Error.Reset();
    TestFalse(
        TEXT("malformed articulated JSON is rejected by the production wrapper"),
        Scene->ApplyArticulatedViewJson(TEXT("{}"), Error));
    TestTrue(TEXT("malformed articulated JSON reports a parser error"), !Error.IsEmpty());
    TestEqual(
        TEXT("message rejection reports stale/degraded"),
        Scene->ConfirmedStatus.Reason,
        FString(TEXT("STALE/DEGRADED")));
    TestTrue(
        TEXT("message rejection keeps source evidence"),
        Scene->ConfirmedStatus.Provenance == Provenance);
    TestTrue(
        TEXT("message rejection keeps arrival predicted_for"),
        Scene->ArrivalStatus.PredictedFor == ArrivalPredictedFor);
    TestTrue(
        TEXT("message rejection keeps arrival observed_at"),
        Scene->ArrivalStatus.ObservedAt == ArrivalObservedAt);
    TestTrue(
        TEXT("message rejection keeps arrival produced_at"),
        Scene->ArrivalStatus.ProducedAt == ArrivalProducedAt);
    TestEqual(
        TEXT("message rejection keeps arrival forecast model version"),
        Scene->ArrivalStatus.EvidenceModelVersion,
        ArrivalForecastModelVersion);
    TestTrue(
        TEXT("message rejection keeps arrival fresh_until"),
        Scene->ArrivalStatus.FreshUntil == ArrivalFreshUntil);
    TestTrue(
        TEXT("message rejection keeps estimated intent arrival"),
        Scene->ArrivalStatus.EstimatedIntentArrivalAt == ArrivalEstimatedIntentArrivalAt);
    TestTrue(
        TEXT("message rejection keeps one-way delay"),
        FMath::IsNearlyEqual(
            Scene->ArrivalStatus.LinkOneWayDelaySeconds,
            ArrivalLinkOneWayDelaySeconds));
    return true;
}

#endif
