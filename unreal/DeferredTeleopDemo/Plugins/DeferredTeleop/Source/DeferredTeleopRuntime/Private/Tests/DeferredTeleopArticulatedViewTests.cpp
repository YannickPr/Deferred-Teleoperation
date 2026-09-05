#if WITH_DEV_AUTOMATION_TESTS

#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace DeferredTeleop::ArticulatedTests
{
bool LoadFixture(const TCHAR* Name, FString& OutJson)
{
    const FString FixturePath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir() / TEXT("../../fixtures/m2/articulated-state/") / Name);
    return FFileHelper::LoadFileToString(OutJson, *FixturePath);
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedViewValidTest,
    "DeferredTeleop.M2.ArticulatedView.ValidThreeLayers",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedViewValidTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Json;
    if (!TestTrue(
            TEXT("valid M2 fixture loads"),
            DeferredTeleop::ArticulatedTests::LoadFixture(TEXT("valid-articulated-view.json"), Json)))
    {
        return false;
    }

    FDeferredTeleopArticulatedViewState State;
    FString Error;
    if (!TestTrue(
            TEXT("strict articulated view parses"),
            DeferredTeleop::ArticulatedView::ParseArticulated(Json, State, Error)))
    {
        AddError(Error);
        return false;
    }
    TestEqual(TEXT("message type is M2-specific"), State.MessageType, FString(TEXT("mission.articulated_view_state")));
    TestTrue(TEXT("confirmed articulated state is available"), State.bHasConfirmedRobotState);
    TestEqual(TEXT("model id is exposed to Blueprint"), State.ConfirmedRobotState.ModelReference.ModelId, FString(TEXT("so101_new_calib")));
    TestEqual(TEXT("six named joints are preserved"), State.ConfirmedRobotState.Joints.Num(), 6);
    TestTrue(TEXT("predicted arrival layer is available in the three-layer fixture"), State.ArrivalRobotState.bAvailable);
    TestTrue(TEXT("operator target layer is available in the three-layer fixture"), State.bHasTargetRobotState);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedViewRejectTest,
    "DeferredTeleop.M2.ArticulatedView.RejectsInvalidAndPreservesLastValid",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedViewRejectTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Json;
    if (!TestTrue(
            TEXT("invalid M2 fixture loads"),
            DeferredTeleop::ArticulatedTests::LoadFixture(
                TEXT("invalid-articulated-view-duplicate-joint.json"),
                Json)))
    {
        return false;
    }

    FDeferredTeleopArticulatedViewState State;
    State.SourceId = TEXT("last-valid-state");
    FString Error;
    TestFalse(
        TEXT("duplicate named joints are rejected"),
        DeferredTeleop::ArticulatedView::ParseArticulated(Json, State, Error));
    TestTrue(TEXT("duplicate diagnostic is visible"), Error.Contains(TEXT("duplicate joint name")));
    TestEqual(TEXT("rejection keeps the last valid state"), State.SourceId, FString(TEXT("last-valid-state")));

    const FString CaseDistinctJson = Json.Replace(
        TEXT("\"joint_name\": \"shoulder_pan\",\n        \"position_radians\": -0.2"),
        TEXT("\"joint_name\": \"SHOULDER_PAN\",\n        \"position_radians\": -0.2"));
    Error.Reset();
    TestTrue(
        TEXT("joint names differing only by case are distinct on the wire"),
        DeferredTeleop::ArticulatedView::ParseArticulated(
            CaseDistinctJson,
            State,
            Error));
    TestEqual(TEXT("case-distinct fixture keeps six joints"), State.ConfirmedRobotState.Joints.Num(), 6);
    if (State.ConfirmedRobotState.Joints.Num() >= 2)
    {
        TestTrue(
            TEXT("case-distinct joint names are preserved"),
            !State.ConfirmedRobotState.Joints[0].JointName.Equals(
                State.ConfirmedRobotState.Joints[1].JointName,
                ESearchCase::CaseSensitive));
    }

    if (!TestTrue(
            TEXT("near-boundary quaternion fixture loads"),
            DeferredTeleop::ArticulatedTests::LoadFixture(
                TEXT("invalid-articulated-view-nonunit-quaternion.json"),
                Json)))
    {
        return false;
    }
    Error.Reset();
    TestFalse(
        TEXT("quaternion norm outside the canonical tolerance is rejected"),
        DeferredTeleop::ArticulatedView::ParseArticulated(Json, State, Error));
    TestTrue(TEXT("non-unit quaternion diagnostic is visible"), Error.Contains(TEXT("unit length")));

    if (!TestTrue(
            TEXT("valid fixture loads for wire-case mutations"),
            DeferredTeleop::ArticulatedTests::LoadFixture(
                TEXT("valid-articulated-view.json"),
                Json)))
    {
        return false;
    }

    auto ExpectWireCaseRejected = [this, &Json](
                                       const TCHAR* Label,
                                       const TCHAR* Before,
                                       const TCHAR* After,
                                       const TCHAR* DiagnosticPath)
    {
        const FString Mutated = Json.Replace(Before, After);
        FDeferredTeleopArticulatedViewState Candidate;
        FString MutationError;
        TestFalse(
            Label,
            DeferredTeleop::ArticulatedView::ParseArticulated(
                Mutated,
                Candidate,
                MutationError));
        TestTrue(
            *FString::Printf(TEXT("%s reports its field"), Label),
            MutationError.Contains(DiagnosticPath));
    };
    ExpectWireCaseRejected(
        TEXT("wire protocol version casing is exact"),
        TEXT("dtt/0"),
        TEXT("DTT/0"),
        TEXT("protocol_version"));
    ExpectWireCaseRejected(
        TEXT("wire description hash prefix casing is exact"),
        TEXT("sha256:"),
        TEXT("SHA256:"),
        TEXT("description_hash"));
    ExpectWireCaseRejected(
        TEXT("wire provenance casing is exact"),
        TEXT("\"provenance\": \"MEASURED\""),
        TEXT("\"provenance\": \"measured\""),
        TEXT("provenance"));
    ExpectWireCaseRejected(
        TEXT("wire connection casing is exact"),
        TEXT("\"mission_to_field\": \"CONNECTED\""),
        TEXT("\"mission_to_field\": \"connected\""),
        TEXT("mission_to_field"));
    ExpectWireCaseRejected(
        TEXT("wire terminal-state casing is exact"),
        TEXT("\"terminal_state\": null"),
        TEXT("\"terminal_state\": \"succeeded\""),
        TEXT("terminal_state"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopArticulatedModelReferenceTest,
    "DeferredTeleop.M2.ArticulatedView.ModelReferenceDiagnostic",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopArticulatedModelReferenceTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDeferredTeleopRobotModelReference Actual;
    Actual.ModelId = TEXT("so101_new_calib");
    Actual.ModelRevision = TEXT("git:actual");
    Actual.DescriptionHash = TEXT("sha256:0000000000000000000000000000000000000000000000000000000000000000");
    FDeferredTeleopRobotModelReference Expected = Actual;
    Expected.ModelRevision = TEXT("git:expected");

    FString Diagnostic;
    TestFalse(
        TEXT("model revision mismatch is a visible comparison failure"),
        DeferredTeleop::ArticulatedView::CompareModelReference(Actual, Expected, Diagnostic));
    TestTrue(TEXT("comparison diagnostic names the mismatched field"), Diagnostic.Contains(TEXT("model_revision")));

    Expected = Actual;
    Expected.DescriptionHash = TEXT("sha256:1111111111111111111111111111111111111111111111111111111111111111");
    Diagnostic.Reset();
    TestFalse(
        TEXT("model hash mismatch is a visible comparison failure"),
        DeferredTeleop::ArticulatedView::CompareModelReference(Actual, Expected, Diagnostic));
    TestTrue(TEXT("hash diagnostic names the mismatched field"), Diagnostic.Contains(TEXT("description_hash")));

    Expected = Actual;
    Expected.ModelId = TEXT("SO101_NEW_CALIB");
    Diagnostic.Reset();
    TestFalse(
        TEXT("model id casing is a comparison mismatch"),
        DeferredTeleop::ArticulatedView::CompareModelReference(Actual, Expected, Diagnostic));
    TestTrue(TEXT("model id case diagnostic names the field"), Diagnostic.Contains(TEXT("model_id")));

    Expected = Actual;
    Expected.ModelRevision = TEXT("GIT:ACTUAL");
    Diagnostic.Reset();
    TestFalse(
        TEXT("model revision casing is a comparison mismatch"),
        DeferredTeleop::ArticulatedView::CompareModelReference(Actual, Expected, Diagnostic));
    TestTrue(
        TEXT("model revision case diagnostic names the field"),
        Diagnostic.Contains(TEXT("model_revision")));

    Expected = Actual;
    Expected.DescriptionHash = TEXT("SHA256:0000000000000000000000000000000000000000000000000000000000000000");
    Diagnostic.Reset();
    TestFalse(
        TEXT("description hash prefix casing is a comparison mismatch"),
        DeferredTeleop::ArticulatedView::CompareModelReference(Actual, Expected, Diagnostic));
    TestTrue(
        TEXT("description hash case diagnostic names the field"),
        Diagnostic.Contains(TEXT("description_hash")));
    return true;
}

#endif
