#if WITH_DEV_AUTOMATION_TESTS

#include "DeferredTeleopMissionViewParser.h"
#include "DeferredTeleopVisualizationLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace
{
const TCHAR* ValidEmptyView = TEXT(
    "{"
    "\"protocol_version\":\"dtt/0\","
    "\"message_type\":\"mission.view_state\","
    "\"source_id\":\"mission-1\","
    "\"source_sequence\":1,"
    "\"produced_at\":\"2026-09-04T12:00:00Z\","
    "\"connection\":{"
    "\"mission_to_field\":\"CONNECTED\","
    "\"changed_at\":\"2026-09-04T12:00:00Z\","
    "\"detail\":\"delayed-link\"},"
    "\"confirmed_state\":null,"
    "\"arrival_belief\":null,"
    "\"target_branch\":null,"
    "\"trajectory_forecasts\":[],"
    "\"prediction_manifests\":[],"
    "\"status\":{"
    "\"operation_id\":null,"
    "\"correlation_id\":null,"
    "\"terminal_state\":null,"
    "\"terminal_contract_id\":null,"
    "\"received_message_count\":0}}" );

void ExpectCaseRejected(
    FAutomationTestBase& Test,
    const FString& Json,
    const TCHAR* Before,
    const TCHAR* After,
    const TCHAR* Label,
    const TCHAR* DiagnosticPath)
{
    const FString Mutated = Json.Replace(Before, After);
    FDeferredTeleopMissionViewState State;
    FString Error;
    Test.TestFalse(Label, DeferredTeleop::MissionView::Parse(Mutated, State, Error));
    Test.TestTrue(
        *FString::Printf(TEXT("%s reports the rejection"), Label),
        Error.Contains(DiagnosticPath));
}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopMissionViewValidTest,
    "DeferredTeleop.M1.MissionView.ValidStrictFrame",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopMissionViewValidTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDeferredTeleopMissionViewState State;
    FString Error;
    TestTrue(TEXT("valid dtt/0 frame parses"), DeferredTeleop::MissionView::Parse(ValidEmptyView, State, Error));
    TestEqual(TEXT("source is exposed"), State.SourceId, FString(TEXT("mission-1")));
    TestEqual(TEXT("sequence is exposed"), State.SourceSequence, 1);
    TestEqual(
        TEXT("connection metadata is exposed"),
        State.MissionToField,
        EDeferredTeleopConnectionState::Connected);
    ExpectCaseRejected(
        *this,
        FString(ValidEmptyView),
        TEXT("mission.view_state"),
        TEXT("MISSION.VIEW_STATE"),
        TEXT("message-type casing is exact"),
        TEXT("message_type"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopMissionViewRejectTest,
    "DeferredTeleop.M1.MissionView.RejectsUnsupportedAndMalformed",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopMissionViewRejectTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDeferredTeleopMissionViewState State;
    State.SourceId = TEXT("last-valid-state");
    FString Error;
    const FString Unsupported = FString(ValidEmptyView).Replace(TEXT("dtt/0"), TEXT("dtt/1"));
    TestFalse(
        TEXT("unsupported protocol is rejected"),
        DeferredTeleop::MissionView::Parse(Unsupported, State, Error));
    TestTrue(TEXT("error identifies protocol"), Error.Contains(TEXT("protocol_version")));
    TestEqual(
        TEXT("rejection does not overwrite caller state"),
        State.SourceId,
        FString(TEXT("last-valid-state")));

    ExpectCaseRejected(
        *this,
        FString(ValidEmptyView),
        TEXT("dtt/0"),
        TEXT("DTT/0"),
        TEXT("protocol-version casing is exact"),
        TEXT("protocol_version"));
    ExpectCaseRejected(
        *this,
        FString(ValidEmptyView),
        TEXT("CONNECTED"),
        TEXT("connected"),
        TEXT("connection-state casing is exact"),
        TEXT("mission_to_field"));
    ExpectCaseRejected(
        *this,
        FString(ValidEmptyView),
        TEXT("\"terminal_state\":null"),
        TEXT("\"terminal_state\":\"succeeded\""),
        TEXT("terminal-state casing is exact"),
        TEXT("terminal_state"));

    Error.Reset();
    TestFalse(
        TEXT("malformed JSON is rejected"),
        DeferredTeleop::MissionView::Parse(TEXT("{not-json"), State, Error));
    TestTrue(TEXT("malformed JSON has an explicit error"), !Error.IsEmpty());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopGoldenMissionViewTest,
    "DeferredTeleop.M1.MissionView.GoldenFixtureParses",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopGoldenMissionViewTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FString FixturePath = FPaths::ConvertRelativePathToFull(
        FPaths::ProjectDir()
        / TEXT("../../fixtures/m1/golden-session/expected-mission-view.json"));
    FString Json;
    if (!TestTrue(
            *FString::Printf(TEXT("golden Mission view loads from %s"), *FixturePath),
            FFileHelper::LoadFileToString(Json, *FixturePath)))
    {
        return false;
    }

    FDeferredTeleopMissionViewState State;
    FString Error;
    if (!TestTrue(
            TEXT("the Python golden Mission view passes the strict Unreal parser"),
            DeferredTeleop::MissionView::Parse(Json, State, Error)))
    {
        AddError(Error);
        return false;
    }

    TestTrue(TEXT("confirmed state is available"), State.ConfirmedState.bAvailable);
    TestEqual(
        TEXT("confirmed state remains measured"),
        State.ConfirmedState.Evidence.Provenance,
        EDeferredTeleopProvenance::Measured);
    TestTrue(TEXT("arrival belief is available"), State.ArrivalBelief.bAvailable);
    TestEqual(
        TEXT("arrival belief remains predicted"),
        State.ArrivalBelief.Evidence.Provenance,
        EDeferredTeleopProvenance::Predicted);
    TestTrue(TEXT("target branch is available"), State.TargetBranch.bAvailable);
    TestEqual(
        TEXT("target branch remains operator asserted"),
        State.TargetBranch.Evidence.Provenance,
        EDeferredTeleopProvenance::OperatorAsserted);
    TestEqual(
        TEXT("one effect reaches Mission"),
        State.Status.TerminalState,
        FString(TEXT("SUCCEEDED")));
    TestTrue(TEXT("timed trajectory is present"), State.TrajectoryForecasts.Num() >= 2);
    TestTrue(TEXT("prediction manifest is present"), State.PredictionManifests.Num() >= 1);

    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"provenance\": \"MEASURED\""),
        TEXT("\"provenance\": \"measured\""),
        TEXT("provenance casing is exact"),
        TEXT("provenance"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"requested_state\": \"PRESSED\""),
        TEXT("\"requested_state\": \"pressed\""),
        TEXT("requested-state casing is exact"),
        TEXT("unsupported target branch"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"condition\": \"button effect succeeds\""),
        TEXT("\"condition\": \"BUTTON EFFECT SUCCEEDS\""),
        TEXT("target-condition casing is exact"),
        TEXT("unsupported target branch"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"timestamp_basis\": \"WALL_CLOCK_UTC\""),
        TEXT("\"timestamp_basis\": \"wall_clock_utc\""),
        TEXT("timestamp-basis casing is exact"),
        TEXT("unsupported trajectory sample"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"source\": \"CONFIRMED_STATE\""),
        TEXT("\"source\": \"confirmed_state\""),
        TEXT("trajectory-source casing is exact"),
        TEXT("source"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"mission_to_field\": \"CONNECTED\""),
        TEXT("\"mission_to_field\": \"connected\""),
        TEXT("golden connection-state casing is exact"),
        TEXT("mission_to_field"));
    ExpectCaseRejected(
        *this,
        Json,
        TEXT("\"terminal_state\": \"SUCCEEDED\""),
        TEXT("\"terminal_state\": \"succeeded\""),
        TEXT("golden terminal-state casing is exact"),
        TEXT("terminal_state"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopFrameConversionTest,
    "DeferredTeleop.M1.Visualization.ConvertsFrameAndUnits",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopFrameConversionTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDeferredTeleopPose Pose;
    Pose.PositionMetres = FVector(1.0, 2.0, 3.0);
    Pose.Orientation = FQuat::Identity;
    const FTransform Converted =
        UDeferredTeleopVisualizationLibrary::MissionPoseToUnrealTransform(Pose);
    TestEqual(
        TEXT("metres become centimetres and left becomes Unreal right"),
        Converted.GetLocation(),
        FVector(100.0, -200.0, 300.0));
    TestTrue(TEXT("identity orientation remains identity"), Converted.GetRotation().Equals(FQuat::Identity));
    return true;
}

#endif
