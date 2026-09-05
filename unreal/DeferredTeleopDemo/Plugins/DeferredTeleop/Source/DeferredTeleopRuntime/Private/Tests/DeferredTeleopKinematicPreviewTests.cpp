#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopKinematicPreviewLibrary.h"
#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Misc/AutomationTest.h"

#include <limits>

namespace DeferredTeleop::Tests::KinematicPreview
{
constexpr double Pi = 3.1415926535897932384626433832795;

FDttCanonicalTransform Translation(double X, double Y, double Z)
{
    return FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(X, Y, Z),
        FQuat4d(0.0, 0.0, 0.0, 1.0));
}

FDttCanonicalTransform RootTransform()
{
    return FDttCanonicalTransform::FromAxisAngle(
        FVector3d(0.4, -0.2, 0.7),
        FVector3d(0.0, 0.0, 1.0),
        0.17);
}

FDttCanonicalVector AxisZ()
{
    FDttCanonicalVector Result;
    Result.Z = 1.0;
    return Result;
}

FDttRobotLinkDescription Link(const TCHAR* Name)
{
    FDttRobotLinkDescription Result;
    Result.Name = FName(Name);
    return Result;
}

FDttRobotJointDescription FixedJoint(
    const TCHAR* Name,
    const TCHAR* Parent,
    const TCHAR* Child)
{
    FDttRobotJointDescription Result;
    Result.Name = FName(Name);
    Result.Type = EDttRobotJointType::Fixed;
    Result.ParentLink = FName(Parent);
    Result.ChildLink = FName(Child);
    Result.ParentToJoint = Translation(0.0, 0.0, 0.0);
    return Result;
}

FDttRobotJointDescription RevoluteJoint(
    const TCHAR* Name,
    const TCHAR* Parent,
    const TCHAR* Child,
    double Lower,
    double Upper)
{
    FDttRobotJointDescription Result;
    Result.Name = FName(Name);
    Result.Type = EDttRobotJointType::Revolute;
    Result.ParentLink = FName(Parent);
    Result.ChildLink = FName(Child);
    Result.ParentToJoint = Translation(1.0, 0.0, 0.0);
    Result.AxisJointFrame = AxisZ();
    Result.bHasPositionLimits = true;
    Result.LowerPositionRadians = Lower;
    Result.UpperPositionRadians = Upper;
    return Result;
}

FDttRobotDescription MakeDescription()
{
    FDttRobotDescription Description;
    Description.ModelId = TEXT("preview-test-model");
    Description.ModelRevision = TEXT("preview:test:1");
    Description.RootLinkName = FName(TEXT("root"));
    Description.Links = {
        Link(TEXT("root")),
        Link(TEXT("mount")),
        Link(TEXT("link_a")),
        Link(TEXT("link_b")),
        Link(TEXT("tool_link")),
    };
    Description.Joints = {
        FixedJoint(TEXT("root_to_mount"), TEXT("root"), TEXT("mount")),
        RevoluteJoint(TEXT("joint_a"), TEXT("mount"), TEXT("link_a"), -Pi, Pi),
        RevoluteJoint(TEXT("joint_b"), TEXT("link_a"), TEXT("link_b"), -Pi, Pi),
        RevoluteJoint(TEXT("gripper"), TEXT("link_b"), TEXT("tool_link"), 0.0, 1.0),
    };

    FDttRobotJointGroupDescription Group;
    Group.Name = FName(TEXT("arm"));
    Group.JointNames = {FName(TEXT("joint_a")), FName(TEXT("joint_b"))};
    Description.JointGroups = {Group};

    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("tool_link"));
    Tool.LinkToTool = Translation(0.25, 0.0, 0.0);
    Description.ToolFrames = {Tool};
    return Description;
}

FDeferredTeleopArticulatedJointPosition ArticulatedJoint(
    const TCHAR* Name,
    double PositionRadians)
{
    FDeferredTeleopArticulatedJointPosition Result;
    Result.JointName = Name;
    Result.PositionRadians = PositionRadians;
    return Result;
}

FDttNamedJointPosition NamedJoint(const TCHAR* Name, double PositionRadians)
{
    FDttNamedJointPosition Result;
    Result.JointName = FName(Name);
    Result.PositionRadians = PositionRadians;
    return Result;
}

FDttPreviewJointVelocity PreviewVelocity(const TCHAR* Name, double MaximumRadiansPerSecond)
{
    FDttPreviewJointVelocity Result;
    Result.JointName = FName(Name);
    Result.MaximumRadiansPerSecond = MaximumRadiansPerSecond;
    return Result;
}

void SetIKJointPositions(
    FDttIKResult& IKResult,
    double JointA,
    double JointB,
    double Gripper)
{
    IKResult.JointPositions = {
        NamedJoint(TEXT("joint_a"), JointA),
        NamedJoint(TEXT("joint_b"), JointB),
        NamedJoint(TEXT("gripper"), Gripper),
    };
}

FDttKinematicPreviewRequest MakeRequest()
{
    FDttKinematicPreviewRequest Request;
    Request.PreviewId = FGuid(0x11111111, 0x22222222, 0x33333333, 0x44444444);
    Request.GoalId = FGuid(0xaaaaaaaa, 0xbbbbbbbb, 0xcccccccc, 0xdddddddd);
    Request.ModelReference.ModelId = TEXT("preview-test-model");
    Request.ModelReference.ModelRevision = TEXT("preview:test:1");
    Request.ModelReference.DescriptionHash =
        TEXT("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    Request.WorldTransformOfRoot = RootTransform();
    Request.StartJointPositions = {
        ArticulatedJoint(TEXT("joint_a"), 0.0),
        ArticulatedJoint(TEXT("joint_b"), 0.2),
        ArticulatedJoint(TEXT("gripper"), 0.3),
    };

    Request.IKResult.bSuccess = true;
    Request.IKResult.Status = EDttIKStatus::Converged;
    Request.IKResult.ModelId = TEXT("preview-test-model");
    Request.IKResult.ModelRevision = TEXT("preview:test:1");
    Request.IKResult.ToolFrameName = FName(TEXT("tool"));
    Request.IKResult.ActiveJointNames = {
        FName(TEXT("joint_a")),
        FName(TEXT("joint_b")),
    };
    Request.IKResult.PositionResidualMetres = 1.0e-10;
    Request.IKResult.ApproachResidualRadians = 2.0e-10;
    Request.IKResult.Diagnostic = TEXT("fixture IK");
    Request.IKResult.Diagnostics = {TEXT("fixture diagnostic")};
    SetIKJointPositions(Request.IKResult, 1.0, 0.6, 0.3);

    Request.SourceReference.SourceMessageId = TEXT("source-message-1");
    Request.SourceReference.CorrelationId = TEXT("correlation-1");
    Request.SourceReference.SourceKind = EDttPreviewSourceKind::Measured;
    Request.SourceReference.FrameId = TEXT("field-world");
    Request.SourceReference.CalibrationVersion = TEXT("calibration-1");
    Request.SourceReference.Evidence.SourceIds = {TEXT("camera-1")};
    Request.SourceReference.Evidence.ObservedAt = FDateTime(2026, 9, 4, 12, 0, 0);
    Request.SourceReference.Evidence.ProducedAt = FDateTime(2026, 9, 4, 12, 0, 1);
    Request.SourceReference.Evidence.Provenance = EDeferredTeleopProvenance::Measured;
    Request.SourceReference.Evidence.WorldRevision = 7;

    Request.Settings.JointVelocities = {
        PreviewVelocity(TEXT("joint_a"), 1.0),
        PreviewVelocity(TEXT("joint_b"), 1.0),
        PreviewVelocity(TEXT("gripper"), 1.0),
    };
    Request.Settings.SampleRateHz = 10.0;
    Request.Settings.MaximumDurationSeconds = 30.0;
    Request.Settings.MaximumSamples = 128;
    Request.Settings.bAcceptPartial = false;
    return Request;
}

bool FindToolTransform(
    const TArray<FDttNamedCanonicalTransform>& ToolTransforms,
    FName ToolName,
    FDttCanonicalTransform& OutTransform)
{
    for (const FDttNamedCanonicalTransform& NamedTransform : ToolTransforms)
    {
        if (NamedTransform.Name == ToolName)
        {
            OutTransform = NamedTransform.Transform;
            return true;
        }
    }
    return false;
}

TArray<FDttNamedJointPosition> PreviewGoalState(
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request)
{
    TMap<FName, double> IKValues;
    for (const FDttNamedJointPosition& Position : Request.IKResult.JointPositions)
    {
        IKValues.Add(Position.JointName, Position.PositionRadians);
    }
    TMap<FName, double> StartValues;
    for (const FDeferredTeleopArticulatedJointPosition& Position : Request.StartJointPositions)
    {
        StartValues.Add(FName(*Position.JointName), Position.PositionRadians);
    }
    TSet<FName> ActiveNames;
    for (const FName ActiveName : Request.IKResult.ActiveJointNames)
    {
        ActiveNames.Add(ActiveName);
    }

    TArray<FDttNamedJointPosition> Result;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double* IKValue = IKValues.Find(Joint.Name);
        const double* StartValue = StartValues.Find(Joint.Name);
        if (IKValue == nullptr || StartValue == nullptr)
        {
            continue;
        }
        Result.Add(NamedJoint(
            *Joint.Name.ToString(),
            ActiveNames.Contains(Joint.Name) ? *IKValue : *StartValue));
    }
    return Result;
}

bool RefreshAchievedTransform(
    const FDttRobotDescription& Description,
    FDttKinematicPreviewRequest& Request)
{
    FDttForwardKinematicsResult FKResult;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            Request.WorldTransformOfRoot,
            PreviewGoalState(Description, Request),
            FKResult))
    {
        return false;
    }
    return FindToolTransform(
        FKResult.ToolTransforms,
        Request.IKResult.ToolFrameName,
        Request.IKResult.AchievedToolTransform);
}

bool Build(
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request,
    FDttKinematicPreview& OutPreview,
    FString& OutError)
{
    return DeferredTeleop::Kinematics::BuildPreview(
        Description,
        Request,
        OutPreview,
        OutError);
}

bool NearlyEqualTransform(
    const FDttCanonicalTransform& Left,
    const FDttCanonicalTransform& Right,
    double Tolerance = 1.0e-12)
{
    const FVector3d LeftTranslation = Left.GetTranslationMetres();
    const FVector3d RightTranslation = Right.GetTranslationMetres();
    const FQuat4d LeftRotation = Left.GetRotationQuaternion();
    const FQuat4d RightRotation = Right.GetRotationQuaternion();
    return FMath::Abs(LeftTranslation.X - RightTranslation.X) <= Tolerance
        && FMath::Abs(LeftTranslation.Y - RightTranslation.Y) <= Tolerance
        && FMath::Abs(LeftTranslation.Z - RightTranslation.Z) <= Tolerance
        && FMath::Abs(LeftRotation.X - RightRotation.X) <= Tolerance
        && FMath::Abs(LeftRotation.Y - RightRotation.Y) <= Tolerance
        && FMath::Abs(LeftRotation.Z - RightRotation.Z) <= Tolerance
        && FMath::Abs(LeftRotation.W - RightRotation.W) <= Tolerance;
}

void ExpectRejected(
    FAutomationTestBase& Test,
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request,
    const TCHAR* Label)
{
    FDttKinematicPreview Preview;
    Preview.bValid = true;
    FString Error;
    Test.TestFalse(Label, Build(Description, Request, Preview, Error));
    Test.TestFalse(TEXT("rejected preview is reset to invalid"), Preview.bValid);
    Test.TestTrue(TEXT("rejected preview has no samples"), Preview.Samples.Num() == 0);
    Test.TestTrue(
        TEXT("rejected preview has no snapshot identifiers"),
        !Preview.PreviewId.IsValid() && !Preview.GoalId.IsValid());
    Test.TestTrue(TEXT("rejection provides a diagnostic"), !Error.IsEmpty());
}

} // namespace DeferredTeleop::Tests::KinematicPreview

namespace DeferredTeleop::Tests::KinematicPreview
{
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewConvergedTest,
    "DeferredTeleop.M2.KinematicPreview.ConvergedSnapshotAndFK",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewConvergedTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest Request = MakeRequest();
    TestTrue(TEXT("fixture achieved transform is FK-derived"), RefreshAchievedTransform(Description, Request));

    FDttKinematicPreview Preview;
    FString Error;
    TestTrue(TEXT("converged IK builds a preview"), Build(Description, Request, Preview, Error));
    if (!Preview.bValid)
    {
        AddError(Error);
        return false;
    }

    TestEqual(TEXT("preview id is copied"), Preview.PreviewId, Request.PreviewId);
    TestEqual(TEXT("goal id is copied"), Preview.GoalId, Request.GoalId);
    TestTrue(TEXT("preview and goal IDs are distinct fixture identities"), Preview.PreviewId != Preview.GoalId);
    TestEqual(TEXT("model id is copied"), Preview.ModelReference.ModelId, Request.ModelReference.ModelId);
    TestEqual(
        TEXT("model revision is copied"),
        Preview.ModelReference.ModelRevision,
        Request.ModelReference.ModelRevision);
    TestEqual(
        TEXT("source message is copied"),
        Preview.SourceReference.SourceMessageId,
        Request.SourceReference.SourceMessageId);
    TestEqual(
        TEXT("source evidence is copied by value"),
        Preview.SourceReference.Evidence.SourceIds[0],
        Request.SourceReference.Evidence.SourceIds[0]);
    TestTrue(
        TEXT("root transform is copied"),
        NearlyEqualTransform(Preview.WorldTransformOfRoot, Request.WorldTransformOfRoot));
    TestEqual(TEXT("IK status is exposed"), Preview.IKStatus, EDttIKStatus::Converged);
    TestFalse(TEXT("converged result is not marked partial"), Preview.bAcceptedPartial);
    TestEqual(TEXT("tool frame is copied"), Preview.ToolFrameName, FName(TEXT("tool")));
    TestEqual(TEXT("start state is description ordered"), Preview.StartJointPositions.Num(), 3);
    TestEqual(TEXT("goal state is description ordered"), Preview.GoalJointPositions.Num(), 3);
    TestTrue(TEXT("preview has multiple samples"), Preview.Samples.Num() >= 2);

    if (Preview.Samples.Num() >= 2)
    {
        const FDttKinematicPreviewSample& First = Preview.Samples[0];
        const FDttKinematicPreviewSample& Last = Preview.Samples.Last();
        TestTrue(TEXT("first sample time is exactly zero"), First.TimeSeconds == 0.0);
        TestTrue(
            TEXT("last sample time is exactly the preview duration"),
            Last.TimeSeconds == Preview.DurationSeconds);
        for (int32 JointIndex = 0; JointIndex < 3; ++JointIndex)
        {
            TestTrue(
                *FString::Printf(TEXT("first sample preserves start joint %d exactly"), JointIndex),
                First.JointPositions[JointIndex].PositionRadians
                    == Preview.StartJointPositions[JointIndex].PositionRadians);
            TestTrue(
                *FString::Printf(TEXT("last sample preserves goal joint %d exactly"), JointIndex),
                Last.JointPositions[JointIndex].PositionRadians
                    == Preview.GoalJointPositions[JointIndex].PositionRadians);
        }
    }

    for (const FDttKinematicPreviewSample& Sample : Preview.Samples)
    {
        TArray<FDttNamedJointPosition> JointState;
        for (const FDeferredTeleopArticulatedJointPosition& Position : Sample.JointPositions)
        {
            JointState.Add(NamedJoint(*Position.JointName, Position.PositionRadians));
        }
        FDttForwardKinematicsResult FKResult;
        TestTrue(
            TEXT("each sample has an independently FK-evaluable joint state"),
            DeferredTeleop::Kinematics::EvaluateForwardKinematics(
                Description,
                Request.WorldTransformOfRoot,
                JointState,
                FKResult));
        FDttCanonicalTransform ExpectedTool;
        TestTrue(TEXT("each sample contains the requested tool"), FindToolTransform(FKResult.ToolTransforms, FName(TEXT("tool")), ExpectedTool));
        TestTrue(TEXT("sample tool pose is the FK pose"), NearlyEqualTransform(Sample.ToolTransform, ExpectedTool));
        TestTrue(TEXT("every sample tool pose remains rigid"), Sample.ToolTransform.IsRigid());
    }

    Request.SourceReference.SourceMessageId = TEXT("mutated-after-build");
    Request.StartJointPositions[0].PositionRadians = 1.5;
    TestEqual(
        TEXT("source snapshot is independent of request mutation"),
        Preview.SourceReference.SourceMessageId,
        FString(TEXT("source-message-1")));
    TestTrue(
        TEXT("joint snapshot is independent of request mutation"),
        Preview.StartJointPositions[0].PositionRadians == 0.0);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewDurationTest,
    "DeferredTeleop.M2.KinematicPreview.DurationUsesMaxJointTime",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewDurationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest Request = MakeRequest();
    SetIKJointPositions(Request.IKResult, 1.0, 1.2, 0.3);
    Request.Settings.JointVelocities[0].MaximumRadiansPerSecond = 2.0;
    Request.Settings.JointVelocities[1].MaximumRadiansPerSecond = 0.5;
    Request.Settings.SampleRateHz = 2.0;
    TestTrue(TEXT("fixture achieved transform is refreshed"), RefreshAchievedTransform(Description, Request));

    FDttKinematicPreview Preview;
    FString Error;
    TestTrue(TEXT("multi-joint preview builds"), Build(Description, Request, Preview, Error));
    TestTrue(TEXT("duration is the maximum joint travel time"), FMath::IsNearlyEqual(Preview.DurationSeconds, 2.0, 1.0e-12));
    TestEqual(TEXT("ceil duration*rate plus endpoint determines samples"), Preview.Samples.Num(), 5);
    if (Preview.Samples.Num() == 5)
    {
        const FDttKinematicPreviewSample& Middle = Preview.Samples[2];
        TestTrue(TEXT("sample time uses seconds"), FMath::IsNearlyEqual(Middle.TimeSeconds, 1.0, 1.0e-12));
        TestTrue(
            TEXT("joint_a is linearly interpolated in radians"),
            FMath::IsNearlyEqual(Middle.JointPositions[0].PositionRadians, 0.5, 1.0e-12));
        TestTrue(
            TEXT("joint_b is linearly interpolated in radians"),
            FMath::IsNearlyEqual(Middle.JointPositions[1].PositionRadians, 0.7, 1.0e-12));
        TestTrue(
            TEXT("inactive gripper remains at its start value"),
            Middle.JointPositions[2].PositionRadians == 0.3);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewZeroDurationTest,
    "DeferredTeleop.M2.KinematicPreview.ZeroDuration",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewZeroDurationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest Request = MakeRequest();
    SetIKJointPositions(Request.IKResult, 0.0, 0.2, 0.3);
    TestTrue(TEXT("zero-duration achieved transform is refreshed"), RefreshAchievedTransform(Description, Request));

    FDttKinematicPreview Preview;
    FString Error;
    TestTrue(TEXT("zero-duration preview builds"), Build(Description, Request, Preview, Error));
    TestTrue(TEXT("duration is exactly zero"), Preview.DurationSeconds == 0.0);
    TestEqual(TEXT("zero duration emits one sample"), Preview.Samples.Num(), 1);
    if (Preview.Samples.Num() == 1)
    {
        TestTrue(TEXT("zero-duration sample time is exactly zero"), Preview.Samples[0].TimeSeconds == 0.0);
        TestTrue(
            TEXT("zero-duration sample preserves the start state"),
            Preview.Samples[0].JointPositions[1].PositionRadians == 0.2);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewCapTest,
    "DeferredTeleop.M2.KinematicPreview.CapsSamplesAndPreservesEndpoints",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewCapTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest Request = MakeRequest();
    SetIKJointPositions(Request.IKResult, 2.0, 0.6, 0.3);
    Request.Settings.JointVelocities[0].MaximumRadiansPerSecond = 0.1;
    Request.Settings.SampleRateHz = 1000.0;
    Request.Settings.MaximumSamples = 128;
    TestTrue(TEXT("high-frequency fixture achieved transform is refreshed"), RefreshAchievedTransform(Description, Request));

    FDttKinematicPreview Preview;
    FString Error;
    TestTrue(TEXT("high-frequency preview builds"), Build(Description, Request, Preview, Error));
    TestTrue(TEXT("duration remains within the configured bound"), Preview.DurationSeconds <= 30.0);
    TestEqual(TEXT("intermediate samples are capped at 128"), Preview.Samples.Num(), 128);
    if (Preview.Samples.Num() == 128)
    {
        TestTrue(TEXT("cap keeps exact zero endpoint"), Preview.Samples[0].TimeSeconds == 0.0);
        TestTrue(
            TEXT("cap keeps exact duration endpoint"),
            Preview.Samples.Last().TimeSeconds == Preview.DurationSeconds);
        TestTrue(
            TEXT("cap keeps exact first joint start"),
            Preview.Samples[0].JointPositions[0].PositionRadians
                == Preview.StartJointPositions[0].PositionRadians);
        TestTrue(
            TEXT("cap keeps exact first joint goal"),
            Preview.Samples.Last().JointPositions[0].PositionRadians
                == Preview.GoalJointPositions[0].PositionRadians);
        for (int32 Index = 1; Index < Preview.Samples.Num(); ++Index)
        {
            TestTrue(
                *FString::Printf(TEXT("capped sample %d has increasing time"), Index),
                Preview.Samples[Index].TimeSeconds > Preview.Samples[Index - 1].TimeSeconds);
        }
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewSettingsValidationTest,
    "DeferredTeleop.M2.KinematicPreview.RejectsInvalidSettings",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewSettingsValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    const FDttKinematicPreviewRequest BaseRequest = MakeRequest();
    struct FInvalidCase
    {
        const TCHAR* Label;
        void (*Mutate)(FDttKinematicPreviewRequest&);
    };
    const FInvalidCase Cases[] = {
        {TEXT("missing velocity"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.JointVelocities.RemoveAt(2);
         }},
        {TEXT("duplicate velocity"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.JointVelocities.Add(PreviewVelocity(TEXT("joint_a"), 1.0));
         }},
        {TEXT("zero velocity"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.JointVelocities[0].MaximumRadiansPerSecond = 0.0;
         }},
        {TEXT("NaN velocity"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.JointVelocities[0].MaximumRadiansPerSecond =
                 std::numeric_limits<double>::quiet_NaN();
         }},
        {TEXT("zero sample rate"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.SampleRateHz = 0.0;
         }},
        {TEXT("sample rate over bound"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.SampleRateHz = 1000.0001;
         }},
        {TEXT("zero duration bound"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.MaximumDurationSeconds = 0.0;
         }},
        {TEXT("duration over bound"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.MaximumDurationSeconds = 30.0001;
         }},
        {TEXT("sample cap below bound"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.MaximumSamples = 1;
         }},
        {TEXT("sample cap above bound"), [](FDttKinematicPreviewRequest& Request)
         {
             Request.Settings.MaximumSamples = 129;
         }},
    };
    for (const FInvalidCase& InvalidCase : Cases)
    {
        FDttKinematicPreviewRequest Request = BaseRequest;
        InvalidCase.Mutate(Request);
        ExpectRejected(*this, Description, Request, InvalidCase.Label);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewForgedResultTest,
    "DeferredTeleop.M2.KinematicPreview.RejectsForgedOrMismatchedIK",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewForgedResultTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest BaseRequest = MakeRequest();
    TestTrue(TEXT("base achieved transform is FK-derived"), RefreshAchievedTransform(Description, BaseRequest));

    FDttKinematicPreviewRequest Forged = BaseRequest;
    Forged.IKResult.AchievedToolTransform.TranslationMetres.X += 1.0e-6;
    ExpectRejected(*this, Description, Forged, TEXT("forged achieved transform"));

    Forged = BaseRequest;
    Forged.WorldTransformOfRoot.TranslationMetres.X += 0.01;
    ExpectRejected(*this, Description, Forged, TEXT("wrong root transform"));

    Forged = BaseRequest;
    Forged.IKResult.ToolFrameName = FName(TEXT("unknown-tool"));
    ExpectRejected(*this, Description, Forged, TEXT("wrong tool frame"));

    Forged = BaseRequest;
    Forged.ModelReference.DescriptionHash =
        TEXT("SHA256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    ExpectRejected(*this, Description, Forged, TEXT("uppercase description hash prefix"));

    Forged = BaseRequest;
    Forged.ModelReference.DescriptionHash =
        TEXT("sha256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
    ExpectRejected(*this, Description, Forged, TEXT("uppercase description hash digest"));

    Forged = BaseRequest;
    Forged.ModelReference.ModelId = TEXT("other-model");
    ExpectRejected(*this, Description, Forged, TEXT("model id mismatch"));

    Forged = BaseRequest;
    Forged.ModelReference.ModelId = TEXT("PREVIEW-TEST-MODEL");
    ExpectRejected(*this, Description, Forged, TEXT("model id casing mismatch"));

    Forged = BaseRequest;
    Forged.GoalId = FGuid();
    ExpectRejected(*this, Description, Forged, TEXT("zero goal id"));

    Forged = BaseRequest;
    Forged.ModelReference.ModelRevision = TEXT("other-revision");
    ExpectRejected(*this, Description, Forged, TEXT("model revision mismatch"));

    Forged = BaseRequest;
    Forged.ModelReference.ModelRevision = TEXT("PREVIEW:TEST:1");
    ExpectRejected(*this, Description, Forged, TEXT("model revision casing mismatch"));

    Forged = BaseRequest;
    Forged.IKResult.ModelId = TEXT("other-model");
    ExpectRejected(*this, Description, Forged, TEXT("IK model id mismatch"));

    Forged = BaseRequest;
    Forged.IKResult.ModelId = TEXT("PREVIEW-TEST-MODEL");
    ExpectRejected(*this, Description, Forged, TEXT("IK model id casing mismatch"));

    Forged = BaseRequest;
    Forged.IKResult.ModelRevision = TEXT("other-revision");
    ExpectRejected(*this, Description, Forged, TEXT("IK model revision mismatch"));

    Forged = BaseRequest;
    Forged.IKResult.ModelRevision = TEXT("PREVIEW:TEST:1");
    ExpectRejected(*this, Description, Forged, TEXT("IK model revision casing mismatch"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewSourceValidationTest,
    "DeferredTeleop.M2.KinematicPreview.ValidatesSourceEvidence",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewSourceValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest BaseRequest = MakeRequest();
    TestTrue(TEXT("source fixture achieved transform is FK-derived"), RefreshAchievedTransform(Description, BaseRequest));
    const EDttPreviewSourceKind SourceKinds[] = {
        EDttPreviewSourceKind::Measured,
        EDttPreviewSourceKind::Fused,
        EDttPreviewSourceKind::Synthetic,
        EDttPreviewSourceKind::OperatorAsserted,
    };
    const EDeferredTeleopProvenance Provenances[] = {
        EDeferredTeleopProvenance::Measured,
        EDeferredTeleopProvenance::Fused,
        EDeferredTeleopProvenance::Simulated,
        EDeferredTeleopProvenance::OperatorAsserted,
    };
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(SourceKinds); ++Index)
    {
        FDttKinematicPreviewRequest Request = BaseRequest;
        Request.SourceReference.SourceKind = SourceKinds[Index];
        Request.SourceReference.Evidence.Provenance = Provenances[Index];
        FDttKinematicPreview Preview;
        FString Error;
        TestTrue(
            *FString::Printf(TEXT("source kind %d with matching provenance passes"), Index),
            Build(Description, Request, Preview, Error));
        TestTrue(
            *FString::Printf(TEXT("source kind %d remains visible in the snapshot"), Index),
            Preview.SourceReference.SourceKind == SourceKinds[Index]);
    }

    FDttKinematicPreviewRequest Invalid = BaseRequest;
    Invalid.SourceReference.SourceKind = EDttPreviewSourceKind::Synthetic;
    Invalid.SourceReference.Evidence.Provenance = EDeferredTeleopProvenance::Measured;
    ExpectRejected(*this, Description, Invalid, TEXT("inconsistent source mapping"));

    Invalid = BaseRequest;
    Invalid.SourceReference.Evidence.ProducedAt = FDateTime(2026, 9, 4, 11, 59, 59);
    ExpectRejected(*this, Description, Invalid, TEXT("inverted evidence dates"));

    Invalid = BaseRequest;
    Invalid.SourceReference.Evidence.SourceIds.Reset();
    ExpectRejected(*this, Description, Invalid, TEXT("missing evidence source"));

    Invalid = BaseRequest;
    Invalid.SourceReference.SourceMessageId.Reset();
    ExpectRejected(*this, Description, Invalid, TEXT("missing source message id"));

    Invalid = BaseRequest;
    Invalid.SourceReference.CorrelationId.Reset();
    ExpectRejected(*this, Description, Invalid, TEXT("missing correlation id"));

    Invalid = BaseRequest;
    Invalid.SourceReference.Evidence.WorldRevision = 0;
    ExpectRejected(*this, Description, Invalid, TEXT("zero world revision"));

    Invalid = BaseRequest;
    Invalid.SourceReference.FrameId.Reset();
    ExpectRejected(*this, Description, Invalid, TEXT("missing frame id"));

    Invalid = BaseRequest;
    Invalid.SourceReference.CalibrationVersion.Reset();
    ExpectRejected(*this, Description, Invalid, TEXT("missing calibration version"));

    Invalid = BaseRequest;
    Invalid.PreviewId = FGuid();
    ExpectRejected(*this, Description, Invalid, TEXT("zero preview id"));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopKinematicPreviewJointAndStatusValidationTest,
    "DeferredTeleop.M2.KinematicPreview.ValidatesJointsAndStatuses",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopKinematicPreviewJointAndStatusValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeDescription();
    FDttKinematicPreviewRequest BaseRequest = MakeRequest();
    TestTrue(TEXT("base achieved transform is FK-derived"), RefreshAchievedTransform(Description, BaseRequest));

    FDttKinematicPreviewRequest Invalid = BaseRequest;
    Invalid.StartJointPositions[0].JointName = TEXT("unknown");
    ExpectRejected(*this, Description, Invalid, TEXT("unknown start joint"));

    Invalid = BaseRequest;
    Invalid.StartJointPositions[0].JointName = TEXT("root_to_mount");
    ExpectRejected(*this, Description, Invalid, TEXT("fixed start joint"));

    Invalid = BaseRequest;
    Invalid.StartJointPositions.Add(ArticulatedJoint(TEXT("joint_a"), 0.0));
    ExpectRejected(*this, Description, Invalid, TEXT("duplicate start joint"));

    Invalid = BaseRequest;
    Invalid.StartJointPositions[0].PositionRadians = std::numeric_limits<double>::quiet_NaN();
    ExpectRejected(*this, Description, Invalid, TEXT("non-finite start joint"));

    Invalid = BaseRequest;
    Invalid.StartJointPositions[0].PositionRadians = 4.0;
    ExpectRejected(*this, Description, Invalid, TEXT("out-of-limit start joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.JointPositions[0].JointName = FName(TEXT("unknown"));
    ExpectRejected(*this, Description, Invalid, TEXT("unknown IK joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.JointPositions.Add(NamedJoint(TEXT("joint_a"), 0.0));
    ExpectRejected(*this, Description, Invalid, TEXT("duplicate IK joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.JointPositions[0].PositionRadians = std::numeric_limits<double>::quiet_NaN();
    ExpectRejected(*this, Description, Invalid, TEXT("non-finite IK joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.JointPositions[0].PositionRadians = 4.0;
    ExpectRejected(*this, Description, Invalid, TEXT("out-of-limit IK joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.ActiveJointNames.Add(FName(TEXT("root_to_mount")));
    ExpectRejected(*this, Description, Invalid, TEXT("fixed active joint"));

    Invalid = BaseRequest;
    Invalid.IKResult.ActiveJointNames.Add(FName(TEXT("joint_a")));
    ExpectRejected(*this, Description, Invalid, TEXT("duplicate active joint"));

    FDttKinematicPreviewRequest InactiveGripper = BaseRequest;
    FDttKinematicPreview EqualInactivePreview;
    FString EqualInactiveError;
    TestTrue(
        TEXT("inactive gripper equal to start is accepted"),
        Build(Description, BaseRequest, EqualInactivePreview, EqualInactiveError));
    if (EqualInactivePreview.bValid)
    {
        TestTrue(
            TEXT("accepted inactive gripper goal equals start exactly"),
            EqualInactivePreview.GoalJointPositions[2].PositionRadians
                == EqualInactivePreview.StartJointPositions[2].PositionRadians);
    }

    InactiveGripper.IKResult.JointPositions[2].PositionRadians = 0.8;
    TestTrue(
        TEXT("inactive gripper fixture retains a valid FK achieved goal"),
        RefreshAchievedTransform(Description, InactiveGripper));
    ExpectRejected(*this, Description, InactiveGripper, TEXT("inactive gripper goal differs from start"));

    FDttKinematicPreview Preview;
    FString Error;

    FDttKinematicPreviewRequest Partial = BaseRequest;
    Partial.IKResult.Status = EDttIKStatus::Partial;
    Partial.IKResult.bSuccess = false;
    ExpectRejected(*this, Description, Partial, TEXT("partial without opt-in"));

    Partial.Settings.bAcceptPartial = true;
    TestTrue(TEXT("partial result passes with explicit opt-in"), Build(Description, Partial, Preview, Error));
    TestTrue(TEXT("partial opt-in is exposed"), Preview.bAcceptedPartial);

    FDttKinematicPreviewRequest PartialSuccess = BaseRequest;
    PartialSuccess.IKResult.Status = EDttIKStatus::Partial;
    PartialSuccess.IKResult.bSuccess = true;
    PartialSuccess.Settings.bAcceptPartial = true;
    ExpectRejected(*this, Description, PartialSuccess, TEXT("partial result with bSuccess=true"));

    FDttKinematicPreviewRequest IterationLimit = BaseRequest;
    IterationLimit.IKResult.Status = EDttIKStatus::IterationLimit;
    IterationLimit.IKResult.bSuccess = false;
    IterationLimit.Settings.bAcceptPartial = true;
    TestTrue(TEXT("iteration-limit result passes with explicit opt-in"), Build(Description, IterationLimit, Preview, Error));
    TestTrue(TEXT("iteration-limit opt-in is exposed"), Preview.bAcceptedPartial);

    const EDttIKStatus RejectedStatuses[] = {
        EDttIKStatus::InvalidInput,
        EDttIKStatus::NumericalFailure,
        EDttIKStatus::Unreachable,
    };
    for (const EDttIKStatus Status : RejectedStatuses)
    {
        FDttKinematicPreviewRequest Rejected = BaseRequest;
        Rejected.IKResult.Status = Status;
        Rejected.IKResult.bSuccess = false;
        ExpectRejected(*this, Description, Rejected, TEXT("unsupported IK status"));
    }

    FDttKinematicPreviewRequest Incoherent = BaseRequest;
    Incoherent.IKResult.Status = EDttIKStatus::Converged;
    Incoherent.IKResult.bSuccess = false;
    ExpectRejected(*this, Description, Incoherent, TEXT("incoherent converged status"));
    return true;
}
} // namespace DeferredTeleop::Tests::KinematicPreview

#endif // WITH_DEV_AUTOMATION_TESTS
