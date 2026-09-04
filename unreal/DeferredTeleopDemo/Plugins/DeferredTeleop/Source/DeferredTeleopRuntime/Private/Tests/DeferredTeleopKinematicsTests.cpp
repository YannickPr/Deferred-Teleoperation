#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

#include <limits>

namespace DeferredTeleop::Tests::Kinematics
{
constexpr double Pi = 3.1415926535897932384626433832795;

FDttCanonicalTransform Translation(double X, double Y, double Z)
{
    return FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(X, Y, Z),
        FQuat4d(0.0, 0.0, 0.0, 1.0));
}

FDttCanonicalVector Vector(double X, double Y, double Z)
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

FDttNamedJointPosition JointPosition(const TCHAR* Name, double PositionRadians)
{
    FDttNamedJointPosition Result;
    Result.JointName = FName(Name);
    Result.PositionRadians = PositionRadians;
    return Result;
}

FDttCanonicalTransform RotationAround(
    const FVector3d& Axis,
    double AngleRadians)
{
    return FDttCanonicalTransform::FromAxisAngle(
        FVector3d(0.0, 0.0, 0.0),
        Axis,
        AngleRadians);
}

FDttRobotDescription MakeBranchedDescription()
{
    FDttRobotDescription Description;
    Description.ModelId = TEXT("test-branched-model");
    Description.ModelRevision = TEXT("test-revision");
    Description.RootLinkName = FName(TEXT("root"));

    Description.Links = {
        Link(TEXT("root")),
        Link(TEXT("left")),
        Link(TEXT("right")),
        Link(TEXT("tip")),
        Link(TEXT("tool_link")),
    };

    FDttRobotJointDescription LeftFixed;
    LeftFixed.Name = FName(TEXT("root_to_left"));
    LeftFixed.Type = EDttRobotJointType::Fixed;
    LeftFixed.ParentLink = FName(TEXT("root"));
    LeftFixed.ChildLink = FName(TEXT("left"));
    LeftFixed.ParentToJoint = Translation(0.0, 1.0, 0.0);

    FDttRobotJointDescription RightRevolute;
    RightRevolute.Name = FName(TEXT("root_to_right"));
    RightRevolute.Type = EDttRobotJointType::Revolute;
    RightRevolute.ParentLink = FName(TEXT("root"));
    RightRevolute.ChildLink = FName(TEXT("right"));
    RightRevolute.ParentToJoint = Translation(1.0, 0.0, 0.0);
    RightRevolute.AxisJointFrame = Vector(0.0, 0.0, 1.0);
    RightRevolute.bHasPositionLimits = true;
    RightRevolute.LowerPositionRadians = -Pi;
    RightRevolute.UpperPositionRadians = Pi;

    FDttRobotJointDescription TipRevolute;
    TipRevolute.Name = FName(TEXT("left_to_tip"));
    TipRevolute.Type = EDttRobotJointType::Revolute;
    TipRevolute.ParentLink = FName(TEXT("left"));
    TipRevolute.ChildLink = FName(TEXT("tip"));
    TipRevolute.ParentToJoint = Translation(1.0, 0.0, 0.0);
    TipRevolute.AxisJointFrame = Vector(0.0, 1.0, 0.0);
    TipRevolute.bHasPositionLimits = true;
    TipRevolute.LowerPositionRadians = -Pi;
    TipRevolute.UpperPositionRadians = Pi;

    FDttRobotJointDescription ToolFixed;
    ToolFixed.Name = FName(TEXT("tip_to_tool"));
    ToolFixed.Type = EDttRobotJointType::Fixed;
    ToolFixed.ParentLink = FName(TEXT("tip"));
    ToolFixed.ChildLink = FName(TEXT("tool_link"));
    ToolFixed.ParentToJoint = Translation(0.0, 0.0, 1.0);

    // The input order is intentionally not a serialised chain order.  FK uses
    // validated parent/child indices, not SO-101 names or array positions.
    Description.Joints = {ToolFixed, RightRevolute, LeftFixed, TipRevolute};

    FDttRobotJointGroupDescription ArmGroup;
    ArmGroup.Name = FName(TEXT("arm"));
    ArmGroup.JointNames = {
        FName(TEXT("root_to_right")),
        FName(TEXT("left_to_tip")),
    };
    Description.JointGroups = {ArmGroup};

    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool_frame"));
    Tool.LinkName = FName(TEXT("tool_link"));
    Tool.LinkToTool = Translation(0.25, 0.0, 0.0);
    Description.ToolFrames = {Tool};
    return Description;
}

const FDttNamedCanonicalTransform* FindNamedTransform(
    const TArray<FDttNamedCanonicalTransform>& Transforms,
    const TCHAR* Name)
{
    for (const FDttNamedCanonicalTransform& Named : Transforms)
    {
        if (Named.Name == FName(Name))
        {
            return &Named;
        }
    }
    return nullptr;
}

bool NearlyEqual(double Left, double Right, double Tolerance = 1.0e-8)
{
    return FMath::Abs(Left - Right) <= Tolerance;
}

bool NearlyEqualVector(
    const FVector3d& Left,
    const FVector3d& Right,
    double Tolerance = 1.0e-8)
{
    return NearlyEqual(Left.X, Right.X, Tolerance)
        && NearlyEqual(Left.Y, Right.Y, Tolerance)
        && NearlyEqual(Left.Z, Right.Z, Tolerance);
}

FString MinimalDescriptionJson()
{
    return TEXT(R"JSON({
      "schema_version":"dtt.robot-description/0",
      "model_id":"minimal",
      "model_revision":"test:0",
      "source":{
        "repository":"local",
        "commit":"test",
        "path":"minimal.urdf",
        "git_blob_sha1":"0000000000000000000000000000000000000000",
        "licence":"Apache-2.0",
        "vendor_modified":false
      },
      "coordinate_convention":{
        "handedness":"RIGHT_HANDED",
        "up_axis":"Z",
        "length_unit":"metre",
        "angle_unit":"radian",
        "rotation_representation":"quaternion_xyzw",
        "transform_notation":"parent_T_child"
      },
      "root_link":"base",
      "links":[
        {"name":"base","visuals":[]},
        {"name":"tip","visuals":[]}
      ],
      "joints":[{
        "name":"joint",
        "type":"revolute",
        "parent_link":"base",
        "child_link":"tip",
        "parent_to_joint":{
          "translation_m":[0,0,0],
          "rotation_xyzw":[0,0,0,1]
        },
        "axis_joint_frame":[0,0,1],
        "position_limits_rad":{"lower":-1,"upper":1}
      }],
      "joint_groups":[{"name":"arm","joints":["joint"]}],
      "tool_frames":[{"name":"tool","link":"tip"}],
      "known_limitations":[]
    })JSON");
}
} // namespace DeferredTeleop::Tests::Kinematics

using namespace DeferredTeleop::Tests::Kinematics;

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopCanonicalCompositionTest,
    "DeferredTeleop.M2.Kinematics.CanonicalComposition",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopCanonicalCompositionTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttCanonicalTransform First = FDttCanonicalTransform::FromAxisAngle(
        FVector3d(1.0, 2.0, 3.0),
        FVector3d(0.0, 0.0, 1.0),
        Pi / 2.0);
    const FDttCanonicalTransform Second = Translation(2.0, 0.0, 0.0);
    const FDttCanonicalTransform Composed = First * Second;

    TestTrue(
        TEXT("composition rotates the child translation before adding the parent translation"),
        NearlyEqualVector(Composed.GetTranslationMetres(), FVector3d(1.0, 4.0, 3.0)));
    TestTrue(TEXT("composition preserves a finite rigid transform"), Composed.IsRigid());
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopGenericForwardKinematicsTest,
    "DeferredTeleop.M2.Kinematics.GenericTreeForwardKinematics",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopGenericForwardKinematicsTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeBranchedDescription();
    TArray<FDttNamedJointPosition> State;
    State.Add(JointPosition(TEXT("left_to_tip"), -Pi / 2.0));
    State.Add(JointPosition(TEXT("root_to_right"), Pi / 2.0));

    FDttForwardKinematicsResult Result;
    TestTrue(
        TEXT("generic fixed/revolute tree evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            Translation(10.0, 20.0, 30.0),
            State,
            Result));
    if (!Result.bSuccess)
    {
        AddError(Result.ErrorMessage);
        return false;
    }

    TestEqual(TEXT("model reference is returned"), Result.ModelId, FString(TEXT("test-branched-model")));
    TestEqual(TEXT("all links are returned"), Result.LinkTransforms.Num(), 5);
    const FName ExpectedLinkOrder[] = {
        FName(TEXT("root")),
        FName(TEXT("left")),
        FName(TEXT("tip")),
        FName(TEXT("tool_link")),
        FName(TEXT("right")),
    };
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(ExpectedLinkOrder); ++Index)
    {
        TestEqual(
            *FString::Printf(TEXT("link output %d follows deterministic traversal"), Index),
            Result.LinkTransforms[Index].Name,
            ExpectedLinkOrder[Index]);
    }
    const FDttNamedCanonicalTransform* Root = FindNamedTransform(Result.LinkTransforms, TEXT("root"));
    const FDttNamedCanonicalTransform* Left = FindNamedTransform(Result.LinkTransforms, TEXT("left"));
    const FDttNamedCanonicalTransform* Tip = FindNamedTransform(Result.LinkTransforms, TEXT("tip"));
    const FDttNamedCanonicalTransform* ToolLink = FindNamedTransform(
        Result.LinkTransforms,
        TEXT("tool_link"));
    const FDttNamedCanonicalTransform* Tool = FindNamedTransform(
        Result.ToolTransforms,
        TEXT("tool_frame"));
    TestTrue(TEXT("root lookup is stable by name"), Root != nullptr);
    TestTrue(TEXT("branch lookup is stable by name"), Left != nullptr);
    TestTrue(TEXT("tip lookup is stable by name"), Tip != nullptr);
    TestTrue(TEXT("fixed tool link lookup is stable by name"), ToolLink != nullptr);
    TestTrue(TEXT("tool frame lookup is stable by name"), Tool != nullptr);
    if (Root == nullptr || Left == nullptr || Tip == nullptr || ToolLink == nullptr || Tool == nullptr)
    {
        return false;
    }

    TestTrue(
        TEXT("root receives the supplied world transform"),
        NearlyEqualVector(Root->Transform.GetTranslationMetres(), FVector3d(10.0, 20.0, 30.0)));
    TestTrue(
        TEXT("fixed branch composes parent_to_joint"),
        NearlyEqualVector(Left->Transform.GetTranslationMetres(), FVector3d(10.0, 21.0, 30.0)));
    TestTrue(
        TEXT("revolute branch composes generic motion"),
        NearlyEqualVector(Tip->Transform.GetTranslationMetres(), FVector3d(11.0, 21.0, 30.0)));
    TestTrue(
        TEXT("fixed tool link propagates after revolute motion"),
        NearlyEqualVector(ToolLink->Transform.GetTranslationMetres(), FVector3d(10.0, 21.0, 30.0)));
    TestTrue(
        TEXT("tool frame uses an explicit link-relative transform"),
        NearlyEqualVector(Tool->Transform.GetTranslationMetres(), FVector3d(10.0, 21.0, 30.25)));
    TestTrue(TEXT("valid state has no limit warning"), Result.bWithinJointLimits);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopRobotModelValidationTest,
    "DeferredTeleop.M2.Kinematics.RejectsInvalidDescriptions",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopRobotModelValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    FDttValidatedRobotModel Validated;

    FDttRobotDescription DuplicateLinks = MakeBranchedDescription();
    DuplicateLinks.Links[1].Name = DuplicateLinks.Links[0].Name;
    TestFalse(
        TEXT("duplicate links are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(DuplicateLinks, Validated, Error));
    TestTrue(TEXT("duplicate link error is explicit"), Error.Contains(TEXT("duplicate link")));

    FDttRobotDescription MissingRoot = MakeBranchedDescription();
    MissingRoot.RootLinkName = NAME_None;
    Error.Reset();
    TestFalse(
        TEXT("missing root is rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(MissingRoot, Validated, Error));
    TestTrue(TEXT("missing root error is explicit"), Error.Contains(TEXT("root_link_name")));

    FDttRobotDescription Disconnected = MakeBranchedDescription();
    Disconnected.Links.Add(Link(TEXT("orphan")));
    Error.Reset();
    TestFalse(
        TEXT("multiple roots and disconnected links are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(Disconnected, Validated, Error));
    TestTrue(TEXT("root error identifies the tree invariant"), Error.Contains(TEXT("exactly one root")));

    FDttRobotDescription Cyclic = MakeBranchedDescription();
    Cyclic.Joints[1].ParentLink = FName(TEXT("left"));
    Cyclic.Joints[1].ChildLink = FName(TEXT("root"));
    Error.Reset();
    TestFalse(
        TEXT("cycles are rejected explicitly"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(Cyclic, Validated, Error));
    TestTrue(TEXT("cycle error identifies the cycle"), Error.Contains(TEXT("cycle")));

    FDttRobotDescription InvalidAxis = MakeBranchedDescription();
    InvalidAxis.Joints[1].AxisJointFrame = FDttCanonicalVector();
    Error.Reset();
    TestFalse(
        TEXT("zero revolute axes are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(InvalidAxis, Validated, Error));
    TestTrue(TEXT("axis error is explicit"), Error.Contains(TEXT("axis_joint_frame")));

    FDttRobotDescription NonFinite = MakeBranchedDescription();
    NonFinite.Joints[1].ParentToJoint.TranslationMetres.X =
        std::numeric_limits<double>::quiet_NaN();
    Error.Reset();
    TestFalse(
        TEXT("non-finite transforms are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(NonFinite, Validated, Error));
    TestTrue(TEXT("non-finite error is explicit"), Error.Contains(TEXT("finite")));

    FDttRobotDescription UnknownGroupedJoint = MakeBranchedDescription();
    UnknownGroupedJoint.JointGroups[0].JointNames.Add(FName(TEXT("unknown")));
    Error.Reset();
    TestFalse(
        TEXT("unknown grouped joints are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(
            UnknownGroupedJoint,
            Validated,
            Error));
    TestTrue(TEXT("joint group error is explicit"), Error.Contains(TEXT("joint group")));

    FDttRobotDescription DuplicateGroupedJoint = MakeBranchedDescription();
    DuplicateGroupedJoint.JointGroups[0].JointNames.Add(FName(TEXT("root_to_right")));
    Error.Reset();
    TestFalse(
        TEXT("duplicate joints inside a group are rejected"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(
            DuplicateGroupedJoint,
            Validated,
            Error));
    TestTrue(TEXT("duplicate group entry is explicit"), Error.Contains(TEXT("duplicate joint")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopJointInputValidationTest,
    "DeferredTeleop.M2.Kinematics.RejectsInvalidJointInputs",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopJointInputValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeBranchedDescription();
    FString Error;
    FDttForwardKinematicsResult Result;

    TArray<FDttNamedJointPosition> Unknown;
    Unknown.Add(JointPosition(TEXT("unknown"), 0.0));
    Unknown.Add(JointPosition(TEXT("left_to_tip"), 0.0));
    Unknown.Add(JointPosition(TEXT("root_to_right"), 0.0));
    TestFalse(
        TEXT("unknown joint names are rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Unknown,
            Result));
    TestTrue(TEXT("unknown joint error is explicit"), Result.ErrorMessage.Contains(TEXT("unknown joint")));

    TArray<FDttNamedJointPosition> Duplicate;
    Duplicate.Add(JointPosition(TEXT("left_to_tip"), 0.0));
    Duplicate.Add(JointPosition(TEXT("left_to_tip"), 0.1));
    Duplicate.Add(JointPosition(TEXT("root_to_right"), 0.0));
    TestFalse(
        TEXT("duplicate joint names are rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Duplicate,
            Result));
    TestTrue(TEXT("duplicate joint error is explicit"), Result.ErrorMessage.Contains(TEXT("duplicate joint")));

    TArray<FDttNamedJointPosition> Missing;
    Missing.Add(JointPosition(TEXT("root_to_right"), 0.0));
    TestFalse(
        TEXT("missing revolute joint names are rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            Missing,
            Result));
    TestTrue(TEXT("missing joint error is explicit"), Result.ErrorMessage.Contains(TEXT("missing joint")));

    TArray<FDttNamedJointPosition> NonFinite;
    NonFinite.Add(JointPosition(TEXT("left_to_tip"), std::numeric_limits<double>::quiet_NaN()));
    NonFinite.Add(JointPosition(TEXT("root_to_right"), 0.0));
    TestFalse(
        TEXT("non-finite joint positions are rejected"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            NonFinite,
            Result));
    TestTrue(TEXT("non-finite joint error is explicit"), Result.ErrorMessage.Contains(TEXT("non-finite")));

    TArray<FDttNamedJointPosition> OutOfLimits;
    OutOfLimits.Add(JointPosition(TEXT("left_to_tip"), 2.0 * Pi));
    OutOfLimits.Add(JointPosition(TEXT("root_to_right"), 0.0));
    TestTrue(
        TEXT("limit violations do not silently clamp FK"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            OutOfLimits,
            Result));
    TestFalse(TEXT("limit violation is visible"), Result.bWithinJointLimits);
    TestEqual(TEXT("one limit diagnostic is emitted"), Result.Diagnostics.Num(), 1);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopRobotDescriptionJsonTest,
    "DeferredTeleop.M2.RobotModel.ParsesCanonicalJson",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopRobotDescriptionJsonTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttRobotDescription Description;
    FString Error;
    const bool bParsed = TestTrue(
        TEXT("minimal canonical robot JSON parses"),
        DeferredTeleop::RobotModel::ParseRobotDescriptionJson(
            MinimalDescriptionJson(),
            Description,
            Error));
    if (!bParsed)
    {
        AddError(Error);
        return false;
    }
    TestEqual(TEXT("JSON model id is loaded"), Description.ModelId, FString(TEXT("minimal")));
    TestEqual(TEXT("JSON root is loaded"), Description.RootLinkName, FName(TEXT("base")));
    TestEqual(TEXT("JSON tool frame is loaded"), Description.ToolFrames.Num(), 1);
    TestEqual(TEXT("JSON model links are loaded"), Description.Links.Num(), 2);
    TestEqual(TEXT("JSON model joint is loaded"), Description.Joints.Num(), 1);
    TestEqual(TEXT("JSON joint group is loaded"), Description.JointGroups.Num(), 1);
    if (Description.JointGroups.Num() == 1
        && Description.JointGroups[0].JointNames.Num() == 1)
    {
        TestEqual(
            TEXT("JSON joint group preserves semantic order"),
            Description.JointGroups[0].JointNames[0],
            FName(TEXT("joint")));
    }

    FString Tampered = MinimalDescriptionJson().Replace(
        TEXT("dtt.robot-description/0"),
        TEXT("dtt.robot-description/1"));
    Error.Reset();
    TestFalse(
        TEXT("unsupported JSON schema is rejected"),
        DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Tampered, Description, Error));
    TestTrue(TEXT("schema error is explicit"), Error.Contains(TEXT("schema_version")));

    Tampered = MinimalDescriptionJson().Replace(
        TEXT("\"position_limits_rad\":{\"lower\":-1,\"upper\":1}"),
        TEXT("\"position_limits_rad\":\"invalid\""));
    Error.Reset();
    TestFalse(
        TEXT("non-object joint limits are rejected"),
        DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Tampered, Description, Error));
    TestTrue(
        TEXT("joint-limit type error is explicit"),
        Error.Contains(TEXT("position_limits_rad")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopCanonicalConversionTest,
    "DeferredTeleop.M2.Kinematics.CanonicalUnrealConversion",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopCanonicalConversionTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FString Error;
    FTransform Unreal;
    const FDttCanonicalTransform Identity = Translation(1.0, 2.0, 3.0);
    TestTrue(
        TEXT("canonical metres convert to Unreal centimetres with reflected Y"),
        DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            Identity,
            Unreal,
            Error));
    TestTrue(
        TEXT("metres are scaled exactly once at the boundary"),
        Unreal.GetLocation().Equals(FVector(100.0F, -200.0F, 300.0F), 1.0e-4F));
    TestTrue(TEXT("identity rotation remains identity"), Unreal.GetRotation().Equals(FQuat::Identity));

    struct FQuarterTurnCase
    {
        FVector3d Axis;
        double Angle;
        FVector UnrealInput;
        FVector ExpectedOutput;
    };
    const FQuarterTurnCase Cases[] = {
        {FVector3d(1.0, 0.0, 0.0), Pi / 2.0, FVector::RightVector, FVector(0.0F, 0.0F, -1.0F)},
        {FVector3d(1.0, 0.0, 0.0), -Pi / 2.0, FVector::RightVector, FVector(0.0F, 0.0F, 1.0F)},
        {FVector3d(0.0, 1.0, 0.0), Pi / 2.0, FVector::ForwardVector, FVector(0.0F, 0.0F, -1.0F)},
        {FVector3d(0.0, 1.0, 0.0), -Pi / 2.0, FVector::ForwardVector, FVector(0.0F, 0.0F, 1.0F)},
        {FVector3d(0.0, 0.0, 1.0), Pi / 2.0, FVector::ForwardVector, FVector(0.0F, -1.0F, 0.0F)},
        {FVector3d(0.0, 0.0, 1.0), -Pi / 2.0, FVector::ForwardVector, FVector(0.0F, 1.0F, 0.0F)},
    };
    for (int32 Index = 0; Index < UE_ARRAY_COUNT(Cases); ++Index)
    {
        Error.Reset();
        TestTrue(
            *FString::Printf(TEXT("quarter turn %d converts through S"), Index),
            DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                RotationAround(Cases[Index].Axis, Cases[Index].Angle),
                Unreal,
                Error));
        const FVector ConvertedVector = Unreal.TransformVector(Cases[Index].UnrealInput).GetSafeNormal();
        TestTrue(
            *FString::Printf(TEXT("quarter turn %d preserves basis sign"), Index),
            ConvertedVector.Equals(Cases[Index].ExpectedOutput, 1.0e-4F));
    }

    const FDttCanonicalTransform RoundTripInput = FDttCanonicalTransform::FromAxisAngle(
        FVector3d(1.25, -2.5, 3.75),
        FVector3d(1.0, 2.0, 3.0).GetSafeNormal(),
        -0.73);
    Error.Reset();
    TestTrue(
        TEXT("canonical to Unreal conversion succeeds for a non-trivial rigid transform"),
        DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            RoundTripInput,
            Unreal,
            Error));
    FDttCanonicalTransform RoundTripOutput;
    TestTrue(
        TEXT("Unreal to canonical conversion is the inverse basis boundary"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            Unreal,
            RoundTripOutput,
            Error));
    TestTrue(
        TEXT("canonical translation round-trips"),
        NearlyEqualVector(
            RoundTripInput.GetTranslationMetres(),
            RoundTripOutput.GetTranslationMetres(),
            1.0e-5));
    const FQuat4d InputRotation = RoundTripInput.GetRotationQuaternion();
    const FQuat4d OutputRotation = RoundTripOutput.GetRotationQuaternion();
    const double RotationDot =
        InputRotation.X * OutputRotation.X
        + InputRotation.Y * OutputRotation.Y
        + InputRotation.Z * OutputRotation.Z
        + InputRotation.W * OutputRotation.W;
    TestTrue(TEXT("canonical rotation round-trips up to quaternion sign"), FMath::Abs(RotationDot) >= 1.0 - 1.0e-5);

    FTransform Scaled = FTransform::Identity;
    Scaled.SetScale3D(FVector(1.0F, -1.0F, 1.0F));
    Error.Reset();
    TestFalse(
        TEXT("arbitrary Unreal scale is rejected at the conversion boundary"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            Scaled,
            RoundTripOutput,
            Error));
    TestTrue(TEXT("scale rejection exposes an error"), Error.Contains(TEXT("unit scale")));

    FTransform NonUnitQuaternion = FTransform::Identity;
    NonUnitQuaternion.SetRotation(FQuat(0.0F, 0.0F, 0.0F, 2.0F));
    Error.Reset();
    TestFalse(
        TEXT("non-unit Unreal quaternions are rejected instead of normalized"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            NonUnitQuaternion,
            RoundTripOutput,
            Error));
    TestTrue(
        TEXT("non-unit quaternion rejection exposes an error"),
        Error.Contains(TEXT("normalized quaternion")));

    FTransform ZeroQuaternion = FTransform::Identity;
    ZeroQuaternion.SetRotation(FQuat(0.0F, 0.0F, 0.0F, 0.0F));
    Error.Reset();
    TestFalse(
        TEXT("zero Unreal quaternions are rejected"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            ZeroQuaternion,
            RoundTripOutput,
            Error));
    TestTrue(
        TEXT("zero quaternion rejection exposes an error"),
        Error.Contains(TEXT("non-zero")));

    FTransform NonFiniteQuaternion = FTransform::Identity;
    NonFiniteQuaternion.SetRotation(FQuat(
        std::numeric_limits<float>::quiet_NaN(),
        0.0F,
        0.0F,
        1.0F));
    Error.Reset();
    TestFalse(
        TEXT("non-finite Unreal quaternions are rejected"),
        DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
            NonFiniteQuaternion,
            RoundTripOutput,
            Error));
    TestTrue(
        TEXT("non-finite quaternion rejection exposes an error"),
        Error.Contains(TEXT("non-finite")));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopGeneratedSo101RobotDescriptionTest,
    "DeferredTeleop.M2.RobotModel.ParsesGeneratedSo101Description",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopGeneratedSo101RobotDescriptionTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FString GeneratedPath = FPaths::Combine(
        FPaths::ProjectDir(),
        TEXT("../../robots/so101/generated/so101.kinematics.json"));
    FString Json;
    const bool bLoaded = FFileHelper::LoadFileToString(Json, *GeneratedPath);
    TestTrue(
        *FString::Printf(TEXT("committed SO-101 description is readable: %s"), *GeneratedPath),
        bLoaded);
    if (!bLoaded)
    {
        return false;
    }

    FDttRobotDescription Description;
    FString Error;
    const bool bParsed = TestTrue(
        TEXT("generated SO-101 description parses"),
        DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Json, Description, Error));
    if (!bParsed)
    {
        AddError(Error);
        return false;
    }

    FDttValidatedRobotModel Validated;
    const bool bValidated = TestTrue(
        TEXT("generated SO-101 description validates as one rooted model"),
        DeferredTeleop::Kinematics::ValidateRobotDescription(Description, Validated, Error));
    if (!bValidated)
    {
        AddError(Error);
        return false;
    }

    TestEqual(TEXT("generated model id is SO-101"), Description.ModelId, FString(TEXT("so101_new_calib")));
    TestEqual(TEXT("generated root link is base_link"), Description.RootLinkName, FName(TEXT("base_link")));

    const FName ExpectedLinkNames[] = {
        FName(TEXT("base_link")),
        FName(TEXT("shoulder_link")),
        FName(TEXT("upper_arm_link")),
        FName(TEXT("lower_arm_link")),
        FName(TEXT("wrist_link")),
        FName(TEXT("gripper_link")),
        FName(TEXT("moving_jaw_so101_v1_link")),
        FName(TEXT("gripper_frame_link")),
    };
    TestEqual(
        TEXT("generated description has all expected links"),
        Description.Links.Num(),
        static_cast<int32>(UE_ARRAY_COUNT(ExpectedLinkNames)));
    for (const FName ExpectedName : ExpectedLinkNames)
    {
        bool bFound = false;
        for (const FDttRobotLinkDescription& Link : Description.Links)
        {
            bFound = Link.Name == ExpectedName;
            if (bFound)
            {
                break;
            }
        }
        TestTrue(
            *FString::Printf(TEXT("generated link is present: %s"), *ExpectedName.ToString()),
            bFound);
    }

    const FName ExpectedJointNames[] = {
        FName(TEXT("shoulder_pan")),
        FName(TEXT("shoulder_lift")),
        FName(TEXT("elbow_flex")),
        FName(TEXT("wrist_flex")),
        FName(TEXT("wrist_roll")),
        FName(TEXT("gripper")),
        FName(TEXT("gripper_frame_joint")),
    };
    TestEqual(
        TEXT("generated description has all expected joints"),
        Description.Joints.Num(),
        static_cast<int32>(UE_ARRAY_COUNT(ExpectedJointNames)));
    int32 RevoluteCount = 0;
    int32 FixedCount = 0;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type == EDttRobotJointType::Revolute)
        {
            ++RevoluteCount;
        }
        else if (Joint.Type == EDttRobotJointType::Fixed)
        {
            ++FixedCount;
        }
    }
    TestEqual(TEXT("generated description has six revolute joints"), RevoluteCount, 6);
    TestEqual(TEXT("generated description has one fixed tool joint"), FixedCount, 1);
    for (const FName ExpectedName : ExpectedJointNames)
    {
        bool bFound = false;
        for (const FDttRobotJointDescription& Joint : Description.Joints)
        {
            bFound = Joint.Name == ExpectedName;
            if (bFound)
            {
                break;
            }
        }
        TestTrue(
            *FString::Printf(TEXT("generated joint is present: %s"), *ExpectedName.ToString()),
            bFound);
    }

    TestEqual(TEXT("generated description has arm and gripper groups"), Description.JointGroups.Num(), 2);
    const FDttRobotJointGroupDescription* ArmGroup = nullptr;
    const FDttRobotJointGroupDescription* GripperGroup = nullptr;
    for (const FDttRobotJointGroupDescription& Group : Description.JointGroups)
    {
        if (Group.Name == FName(TEXT("arm")))
        {
            ArmGroup = &Group;
        }
        else if (Group.Name == FName(TEXT("gripper")))
        {
            GripperGroup = &Group;
        }
    }
    TestTrue(TEXT("generated arm group is present"), ArmGroup != nullptr);
    TestTrue(TEXT("generated gripper group is present"), GripperGroup != nullptr);
    if (ArmGroup != nullptr)
    {
        const FName ExpectedArmJointNames[] = {
            FName(TEXT("shoulder_pan")),
            FName(TEXT("shoulder_lift")),
            FName(TEXT("elbow_flex")),
            FName(TEXT("wrist_flex")),
            FName(TEXT("wrist_roll")),
        };
        TestEqual(TEXT("arm group contains five joints"), ArmGroup->JointNames.Num(), 5);
        for (const FName ExpectedName : ExpectedArmJointNames)
        {
            TestTrue(
                *FString::Printf(TEXT("arm group contains %s"), *ExpectedName.ToString()),
                ArmGroup->JointNames.Contains(ExpectedName));
        }
    }
    if (GripperGroup != nullptr)
    {
        TestEqual(TEXT("gripper group contains one joint"), GripperGroup->JointNames.Num(), 1);
        if (GripperGroup->JointNames.Num() == 1)
        {
            TestEqual(
                TEXT("gripper group names its gripper joint"),
                GripperGroup->JointNames[0],
                FName(TEXT("gripper")));
        }
    }

    TestEqual(TEXT("generated description has one tool frame"), Description.ToolFrames.Num(), 1);
    if (Description.ToolFrames.Num() == 1)
    {
        TestEqual(
            TEXT("generated tool frame name is gripper_frame_link"),
            Description.ToolFrames[0].Name,
            FName(TEXT("gripper_frame_link")));
        TestEqual(
            TEXT("generated tool frame is attached to gripper_frame_link"),
            Description.ToolFrames[0].LinkName,
            FName(TEXT("gripper_frame_link")));
    }

    // This integration check deliberately asserts model identity and structure
    // only. It is not a numerical SO-101 FK golden oracle; that belongs to M2.4.
    return true;
}

#endif
