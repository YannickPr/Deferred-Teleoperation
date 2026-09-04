#if WITH_DEV_AUTOMATION_TESTS

#include "Kinematics/DeferredTeleopIKLibrary.h"
#include "Kinematics/DeferredTeleopIKTestBridge.h"
#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

#include <limits>

namespace DeferredTeleop::Tests::IK
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

FDttRobotJointDescription Revolute(
    const TCHAR* Name,
    const TCHAR* Parent,
    const TCHAR* Child,
    const FDttCanonicalTransform& ParentToJoint,
    const FDttCanonicalVector& Axis,
    double Lower = -Pi,
    double Upper = Pi)
{
    FDttRobotJointDescription Result;
    Result.Name = FName(Name);
    Result.Type = EDttRobotJointType::Revolute;
    Result.ParentLink = FName(Parent);
    Result.ChildLink = FName(Child);
    Result.ParentToJoint = ParentToJoint;
    Result.AxisJointFrame = Axis;
    Result.bHasPositionLimits = true;
    Result.LowerPositionRadians = Lower;
    Result.UpperPositionRadians = Upper;
    return Result;
}

FDttRobotDescription MakeIKDescription()
{
    FDttRobotDescription Description;
    Description.ModelId = TEXT("ik-test-model");
    Description.ModelRevision = TEXT("ik-test:1");
    Description.RootLinkName = FName(TEXT("root"));
    Description.Links = {
        Link(TEXT("root")),
        Link(TEXT("y_link")),
        Link(TEXT("z_link")),
        Link(TEXT("x_link")),
        Link(TEXT("tool_link")),
        Link(TEXT("gripper_link")),
    };
    Description.Joints = {
        Revolute(
            TEXT("joint_y"),
            TEXT("root"),
            TEXT("y_link"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 1.0, 0.0),
            -1.4,
            1.4),
        Revolute(
            TEXT("joint_z"),
            TEXT("y_link"),
            TEXT("z_link"),
            Translation(0.35, 0.05, 0.10),
            Vector(0.0, 0.0, 1.0),
            -1.5,
            1.5),
        Revolute(
            TEXT("joint_x"),
            TEXT("z_link"),
            TEXT("x_link"),
            Translation(0.28, -0.06, 0.04),
            Vector(1.0, 0.0, 0.0),
            -1.5,
            1.5),
        Revolute(
            TEXT("wrist_roll"),
            TEXT("x_link"),
            TEXT("tool_link"),
            Translation(0.22, 0.07, -0.03),
            Vector(0.0, 0.0, 1.0),
            -1.5,
            1.5),
        Revolute(
            TEXT("gripper"),
            TEXT("tool_link"),
            TEXT("gripper_link"),
            Translation(0.0, 0.0, 0.05),
            Vector(1.0, 0.0, 0.0),
            -0.7,
            0.7),
    };
    FDttRobotJointGroupDescription Arm;
    Arm.Name = FName(TEXT("arm"));
    Arm.JointNames = {
        FName(TEXT("joint_y")),
        FName(TEXT("joint_z")),
        FName(TEXT("joint_x")),
        FName(TEXT("wrist_roll")),
    };
    FDttRobotJointGroupDescription Gripper;
    Gripper.Name = FName(TEXT("gripper"));
    Gripper.JointNames = {FName(TEXT("gripper"))};
    Description.JointGroups = {Arm, Gripper};

    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("tool_link"));
    Tool.LinkToTool = Translation(0.13, -0.025, 0.035);
    Description.ToolFrames = {Tool};
    return Description;
}

bool LoadSO101Description(
    FDttRobotDescription& OutDescription,
    FString& OutJson,
    FString& OutError)
{
    const FString GeneratedPath = FPaths::Combine(
        FPaths::ProjectDir(),
        TEXT("../../robots/so101/generated/so101.kinematics.json"));
    if (!FFileHelper::LoadFileToString(OutJson, *GeneratedPath))
    {
        OutError = FString::Printf(
            TEXT("could not read generated SO-101 description: %s"),
            *GeneratedPath);
        return false;
    }
    return DeferredTeleop::RobotModel::ParseRobotDescriptionJson(
        OutJson,
        OutDescription,
        OutError);
}

FDttRobotDescription MakeFreeRollDescription()
{
    FDttRobotDescription Description = MakeIKDescription();
    // A pure local-Z tool offset is invariant under the wrist's local-Z roll.
    Description.ToolFrames[0].LinkToTool = Translation(0.0, 0.0, 0.035);
    Description.ModelId = TEXT("ik-free-roll-test-model");
    Description.ModelRevision = TEXT("ik-free-roll-test:1");
    return Description;
}

FDttRobotDescription MakeSixDofGenericDescription()
{
    // The first five joints all rotate about Z at the same origin.  They can
    // change the tool's XY position, but cannot produce a Z displacement.  A
    // sixth Y joint is therefore required for the target below.
    FDttRobotDescription Description;
    Description.ModelId = TEXT("six-dof-generic");
    Description.ModelRevision = TEXT("six-dof-generic:1");
    Description.RootLinkName = FName(TEXT("root"));
    Description.Links = {
        Link(TEXT("root")),
        Link(TEXT("link1")),
        Link(TEXT("link2")),
        Link(TEXT("link3")),
        Link(TEXT("link4")),
        Link(TEXT("link5")),
        Link(TEXT("link6")),
    };
    Description.Joints = {
        Revolute(
            TEXT("joint_z0"),
            TEXT("root"),
            TEXT("link1"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
        Revolute(
            TEXT("joint_z1"),
            TEXT("link1"),
            TEXT("link2"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
        Revolute(
            TEXT("joint_z2"),
            TEXT("link2"),
            TEXT("link3"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
        Revolute(
            TEXT("joint_z3"),
            TEXT("link3"),
            TEXT("link4"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
        Revolute(
            TEXT("joint_z4"),
            TEXT("link4"),
            TEXT("link5"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
        Revolute(
            TEXT("joint_y5"),
            TEXT("link5"),
            TEXT("link6"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 1.0, 0.0)),
    };
    FDttRobotJointGroupDescription Group;
    Group.Name = FName(TEXT("six-dof"));
    Group.JointNames = {
        FName(TEXT("joint_z0")),
        FName(TEXT("joint_z1")),
        FName(TEXT("joint_z2")),
        FName(TEXT("joint_z3")),
        FName(TEXT("joint_z4")),
        FName(TEXT("joint_y5")),
    };
    Description.JointGroups = {Group};
    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("link6"));
    Tool.LinkToTool = Translation(1.0, 0.0, 0.0);
    Description.ToolFrames = {Tool};
    return Description;
}

FDttRobotDescription MakeSingularRotationCenterDescription()
{
    FDttRobotDescription Description;
    Description.ModelId = TEXT("singular-rotation-center");
    Description.ModelRevision = TEXT("singular-rotation-center:1");
    Description.RootLinkName = FName(TEXT("root"));
    Description.Links = {Link(TEXT("root")), Link(TEXT("tool_link"))};
    Description.Joints = {
        Revolute(
            TEXT("joint_z"),
            TEXT("root"),
            TEXT("tool_link"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 0.0, 1.0)),
    };
    FDttRobotJointGroupDescription Group;
    Group.Name = FName(TEXT("singular"));
    Group.JointNames = {FName(TEXT("joint_z"))};
    Description.JointGroups = {Group};
    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("tool_link"));
    // The tool is at the joint origin, so every value of joint_z has the same
    // position and the position Jacobian is exactly zero.
    Tool.LinkToTool = Translation(0.0, 0.0, 0.0);
    Description.ToolFrames = {Tool};
    return Description;
}

TArray<FDttNamedJointPosition> Seed(
    double JointY,
    double JointZ,
    double JointX,
    double WristRoll,
    double Gripper)
{
    return {
        JointPosition(TEXT("joint_y"), JointY),
        JointPosition(TEXT("joint_z"), JointZ),
        JointPosition(TEXT("joint_x"), JointX),
        JointPosition(TEXT("wrist_roll"), WristRoll),
        JointPosition(TEXT("gripper"), Gripper),
    };
}

const FDttNamedCanonicalTransform* FindTool(
    const FDttForwardKinematicsResult& Result,
    const TCHAR* Name)
{
    const FName ToolName(Name);
    for (const FDttNamedCanonicalTransform& Tool : Result.ToolTransforms)
    {
        if (Tool.Name == ToolName)
        {
            return &Tool;
        }
    }
    return nullptr;
}

const FDttNamedCanonicalTransform* FindTool(
    const FDttForwardKinematicsResult& Result,
    FName Name)
{
    for (const FDttNamedCanonicalTransform& Tool : Result.ToolTransforms)
    {
        if (Tool.Name == Name)
        {
            return &Tool;
        }
    }
    return nullptr;
}

double PositionError(
    const FDttCanonicalTransform& Left,
    const FDttCanonicalTransform& Right)
{
    return (Left.GetTranslationMetres() - Right.GetTranslationMetres()).Size();
}

bool NearlyEqual(double Left, double Right, double Tolerance = 1.0e-8)
{
    return FMath::Abs(Left - Right) <= Tolerance;
}

bool NormalizeOracleDirection(const FVector3d& Input, FVector3d& OutDirection)
{
    if (!FMath::IsFinite(Input.X)
        || !FMath::IsFinite(Input.Y)
        || !FMath::IsFinite(Input.Z))
    {
        return false;
    }
    const double NormSquared = Input.SizeSquared();
    if (!FMath::IsFinite(NormSquared) || NormSquared <= 1.0e-24)
    {
        return false;
    }
    OutDirection = Input * (1.0 / FMath::Sqrt(NormSquared));
    return FMath::IsFinite(OutDirection.X)
        && FMath::IsFinite(OutDirection.Y)
        && FMath::IsFinite(OutDirection.Z);
}

bool EvaluateToolTransform(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    const TArray<FDttNamedJointPosition>& State,
    FDttCanonicalTransform& OutToolTransform)
{
    FDttForwardKinematicsResult FK;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            WorldTransformOfRoot,
            State,
            FK))
    {
        return false;
    }
    const FDttNamedCanonicalTransform* Tool = FindTool(FK, TEXT("tool"));
    if (Tool == nullptr)
    {
        return false;
    }
    OutToolTransform = Tool->Transform;
    return OutToolTransform.IsRigid();
}

TArray<FDttNamedJointPosition> StateWithJointOffset(
    const TArray<FDttNamedJointPosition>& State,
    FName JointName,
    double OffsetRadians)
{
    TArray<FDttNamedJointPosition> Result = State;
    for (FDttNamedJointPosition& Position : Result)
    {
        if (Position.JointName == JointName)
        {
            Position.PositionRadians += OffsetRadians;
            break;
        }
    }
    return Result;
}

bool BuildOracleApproachBasis(
    const FVector3d& CurrentAxisInput,
    const FVector3d& TargetAxisInput,
    FVector3d& OutBasisU,
    FVector3d& OutBasisV,
    FVector3d& OutError3,
    double& OutAngle)
{
    FVector3d CurrentAxis;
    FVector3d TargetAxis;
    if (!NormalizeOracleDirection(CurrentAxisInput, CurrentAxis)
        || !NormalizeOracleDirection(TargetAxisInput, TargetAxis))
    {
        return false;
    }

    const FVector3d CanonicalAxes[] = {
        FVector3d(1.0, 0.0, 0.0),
        FVector3d(0.0, 1.0, 0.0),
        FVector3d(0.0, 0.0, 1.0),
    };
    int32 LeastAlignedIndex = 0;
    double LeastAligned = FMath::Abs(
        FVector3d::DotProduct(CurrentAxis, CanonicalAxes[0]));
    for (int32 Index = 1; Index < UE_ARRAY_COUNT(CanonicalAxes); ++Index)
    {
        const double Alignment = FMath::Abs(
            FVector3d::DotProduct(CurrentAxis, CanonicalAxes[Index]));
        if (Alignment < LeastAligned)
        {
            LeastAligned = Alignment;
            LeastAlignedIndex = Index;
        }
    }

    OutBasisU = CanonicalAxes[LeastAlignedIndex]
        - FVector3d::DotProduct(
              CanonicalAxes[LeastAlignedIndex],
              CurrentAxis)
            * CurrentAxis;
    if (!NormalizeOracleDirection(OutBasisU, OutBasisU))
    {
        return false;
    }
    OutBasisV = FVector3d::CrossProduct(CurrentAxis, OutBasisU);
    if (!NormalizeOracleDirection(OutBasisV, OutBasisV))
    {
        return false;
    }

    const FVector3d Cross = FVector3d::CrossProduct(CurrentAxis, TargetAxis);
    const double SineMagnitude = FMath::Sqrt(FMath::Max(0.0, Cross.SizeSquared()));
    const double Dot = FMath::Clamp(
        FVector3d::DotProduct(CurrentAxis, TargetAxis),
        -1.0,
        1.0);
    OutAngle = FMath::Atan2(SineMagnitude, Dot);
    if (!FMath::IsFinite(OutAngle))
    {
        return false;
    }
    if (OutAngle <= 1.0e-12)
    {
        OutError3 = FVector3d::ZeroVector;
        OutAngle = 0.0;
    }
    else if (SineMagnitude > 1.0e-12)
    {
        OutError3 = (OutAngle / SineMagnitude)
            * (TargetAxis - Dot * CurrentAxis);
    }
    else if (Dot < 0.0)
    {
        OutError3 = Pi * OutBasisU;
        OutAngle = Pi;
    }
    else
    {
        OutError3 = FVector3d::ZeroVector;
        OutAngle = 0.0;
    }
    return FMath::IsFinite(OutError3.X)
        && FMath::IsFinite(OutError3.Y)
        && FMath::IsFinite(OutError3.Z);
}

bool ToolApproachAxis(
    const FDttCanonicalTransform& ToolTransform,
    const FDttCanonicalVector& LocalToolApproachAxis,
    FVector3d& OutAxis)
{
    FQuat4d Rotation = ToolTransform.GetRotationQuaternion();
    Rotation.Normalize();
    return NormalizeOracleDirection(
        Rotation.RotateVector(LocalToolApproachAxis.ToVector3d()),
        OutAxis);
}

bool SolveTarget(
    const FDttRobotDescription& Description,
    const TArray<FDttNamedJointPosition>& TargetState,
    const TArray<FDttNamedJointPosition>& StartState,
    EDttIKMode Mode,
    FDttIKResult& OutResult,
    FDttForwardKinematicsResult& OutTargetFK,
    FName JointGroupName = FName(TEXT("arm")),
    FName ToolFrameName = FName(TEXT("tool")))
{
    const bool bTargetFK = DeferredTeleop::Kinematics::EvaluateForwardKinematics(
        Description,
        FDttCanonicalTransform::Identity(),
        TargetState,
        OutTargetFK);
    if (!bTargetFK)
    {
        return false;
    }
    const FDttNamedCanonicalTransform* TargetTool = FindTool(OutTargetFK, ToolFrameName);
    if (TargetTool == nullptr)
    {
        return false;
    }

    FDttIKRequest Request;
    Request.JointGroupName = JointGroupName;
    Request.ToolFrameName = ToolFrameName;
    Request.Mode = Mode;
    Request.TargetPositionMetres = TargetTool->Transform.TranslationMetres;
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = StartState;
    if (Mode == EDttIKMode::PositionPlusApproachAxis)
    {
        FQuat4d TargetRotation = TargetTool->Transform.GetRotationQuaternion();
        TargetRotation.Normalize();
        Request.TargetApproachDirectionCanonical = FDttCanonicalVector::FromVector3d(
            TargetRotation.RotateVector(FVector3d(0.0, 0.0, 1.0)));
        Request.LocalToolApproachAxis = Vector(0.0, 0.0, 1.0);
    }
    return DeferredTeleop::Kinematics::SolveInverseKinematics(
        Description,
        Request,
        FDttIKSettings(),
        OutResult);
}
} // namespace DeferredTeleop::Tests::IK

namespace DeferredTeleop::Tests::IK
{
IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKSO101KnownTargetTest,
    "DeferredTeleop.M2.IK.SO101KnownNonsymmetricTarget",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKSO101KnownTargetTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttRobotDescription Description;
    FString Json;
    FString Error;
    const bool bLoaded = LoadSO101Description(Description, Json, Error);
    TestTrue(TEXT("generated SO101 description file is readable and parses"), bLoaded);
    if (!bLoaded)
    {
        AddError(Error);
        return false;
    }

    TestEqual(TEXT("SO101 model id comes from generated JSON"), Description.ModelId,
        FString(TEXT("so101_new_calib")));
    TestTrue(
        TEXT("SO101 model revision contains the pinned source commit and blob"),
        Description.ModelRevision.Contains(TEXT("385e8d7c68e24945df6c60d9bd68837a4b7411ae"))
            && Description.ModelRevision.Contains(TEXT("9552a231d8b23bed68ec15779eba620c5d875ec4")));
    TestTrue(
        TEXT("SO101 JSON retains the pinned source metadata"),
        Json.Contains(TEXT("https://github.com/TheRobotStudio/SO-ARM100"))
            && Json.Contains(TEXT("Simulation/SO101/so101_new_calib.urdf"))
            && Json.Contains(TEXT("\"commit\": \"385e8d7c68e24945df6c60d9bd68837a4b7411ae\""))
            && Json.Contains(TEXT("\"git_blob_sha1\": \"9552a231d8b23bed68ec15779eba620c5d875ec4\"")));
    TestEqual(TEXT("SO101 has arm and gripper groups"), Description.JointGroups.Num(), 2);
    if (Description.JointGroups.Num() < 2)
    {
        return false;
    }
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
    TestTrue(TEXT("SO101 contains named arm and gripper groups"), ArmGroup != nullptr && GripperGroup != nullptr);
    if (ArmGroup == nullptr || GripperGroup == nullptr)
    {
        return false;
    }
    TestEqual(TEXT("SO101 has five arm joints"), ArmGroup->JointNames.Num(), 5);
    TestEqual(TEXT("SO101 has a separate gripper group"), GripperGroup->JointNames.Num(), 1);
    TestTrue(
        TEXT("SO101 gripper actuator is outside the five-joint arm group"),
        ArmGroup->JointNames.Num() == 5
            && GripperGroup->JointNames.Num() == 1
            && GripperGroup->JointNames[0] == FName(TEXT("gripper"))
            && !ArmGroup->JointNames.Contains(FName(TEXT("gripper"))));
    int32 RevoluteJointCount = 0;
    bool bFixedJointOutsideGroups = false;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type == EDttRobotJointType::Revolute)
        {
            ++RevoluteJointCount;
        }
        if (Joint.Name == FName(TEXT("gripper_frame_joint")))
        {
            bFixedJointOutsideGroups = Joint.Type == EDttRobotJointType::Fixed
                && !ArmGroup->JointNames.Contains(Joint.Name)
                && !GripperGroup->JointNames.Contains(Joint.Name);
        }
    }
    TestEqual(TEXT("SO101 has five arm revolutes plus one gripper revolute"), RevoluteJointCount, 6);
    TestTrue(TEXT("SO101 fixed gripper frame joint is outside actuator groups"), bFixedJointOutsideGroups);

    const TArray<FDttNamedJointPosition> State = {
        JointPosition(TEXT("shoulder_pan"), 0.23),
        JointPosition(TEXT("shoulder_lift"), -0.41),
        JointPosition(TEXT("elbow_flex"), 0.37),
        JointPosition(TEXT("wrist_flex"), -0.28),
        JointPosition(TEXT("wrist_roll"), 0.19),
        JointPosition(TEXT("gripper"), 0.31),
    };
    const TArray<FDttNamedJointPosition> WarmSeed = {
        JointPosition(TEXT("shoulder_pan"), 0.20),
        JointPosition(TEXT("shoulder_lift"), -0.38),
        JointPosition(TEXT("elbow_flex"), 0.34),
        JointPosition(TEXT("wrist_flex"), -0.25),
        JointPosition(TEXT("wrist_roll"), 0.16),
        JointPosition(TEXT("gripper"), 0.31),
    };
    FDttForwardKinematicsResult Result;
    TestTrue(
        TEXT("SO101 nonsymmetric named target FK evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            State,
            Result));
    const FDttNamedCanonicalTransform* Tool = FindTool(Result, FName(TEXT("gripper_frame_link")));
    TestTrue(TEXT("SO101 tool frame is explicit"), Tool != nullptr);
    if (Tool == nullptr)
    {
        return false;
    }
    TestTrue(
        TEXT("SO101 target tool position is nonzero"),
        Tool->Transform.GetTranslationMetres().Size() > 1.0e-6);

    auto AssertSO101Result = [this](
        const TCHAR* Label,
        const FDttIKResult& IKResult,
        double ExpectedGripper) {
        TestTrue(
            *FString::Printf(TEXT("%s solve succeeds"), Label),
            IKResult.bSuccess && IKResult.Status == EDttIKStatus::Converged);
        TestTrue(
            *FString::Printf(TEXT("%s position residual is within tolerance"), Label),
            IKResult.PositionResidualMetres <= 1.0e-3);
        TestEqual(
            *FString::Printf(TEXT("%s returns all revolute joints"), Label),
            IKResult.JointPositions.Num(),
            6);
        TestTrue(
            *FString::Printf(TEXT("%s identifies the generated tool frame"), Label),
            IKResult.ToolFrameName == FName(TEXT("gripper_frame_link")));
        const FDttNamedJointPosition* Gripper = nullptr;
        for (const FDttNamedJointPosition& Position : IKResult.JointPositions)
        {
            if (Position.JointName == FName(TEXT("gripper")))
            {
                Gripper = &Position;
                break;
            }
        }
        TestTrue(
            *FString::Printf(TEXT("%s returns the inactive gripper"), Label),
            Gripper != nullptr);
        if (Gripper != nullptr)
        {
            TestTrue(
                *FString::Printf(TEXT("%s leaves gripper unchanged"), Label),
                NearlyEqual(Gripper->PositionRadians, ExpectedGripper, 1.0e-12));
        }
    };

    FDttIKResult PositionResult;
    FDttForwardKinematicsResult PositionTargetFK;
    TestTrue(
        TEXT("SO101 position-only target is solvable by the named arm group"),
        SolveTarget(
            Description,
            State,
            WarmSeed,
            EDttIKMode::PositionOnly,
            PositionResult,
            PositionTargetFK,
            FName(TEXT("arm")),
            FName(TEXT("gripper_frame_link"))));
    AssertSO101Result(TEXT("SO101 position-only"), PositionResult, 0.31);

    FDttIKResult ApproachResult;
    FDttForwardKinematicsResult ApproachTargetFK;
    TestTrue(
        TEXT("SO101 position-plus-approach target is solvable from a warm seed"),
        SolveTarget(
            Description,
            State,
            WarmSeed,
            EDttIKMode::PositionPlusApproachAxis,
            ApproachResult,
            ApproachTargetFK,
            FName(TEXT("arm")),
            FName(TEXT("gripper_frame_link"))));
    AssertSO101Result(TEXT("SO101 position-plus-approach"), ApproachResult, 0.31);
    TestTrue(
        TEXT("SO101 approach residual is within the axis tolerance"),
        ApproachResult.ApproachResidualRadians <= 2.0 * Pi / 180.0);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKPositionOnlyTest,
    "DeferredTeleop.M2.IK.PositionOnly",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKPositionOnlyTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeIKDescription();
    const TArray<FDttNamedJointPosition> TargetState = Seed(0.27, -0.31, 0.41, -0.23, 0.44);
    const TArray<FDttNamedJointPosition> StartState = Seed(-0.08, 0.12, -0.17, 0.19, 0.44);
    FDttIKRequest Request;
    FDttForwardKinematicsResult TargetFK;
    FDttIKResult Result;
    TestTrue(
        TEXT("position-only target solves on a nonsymmetric FK fixture"),
        SolveTarget(
            Description,
            TargetState,
            StartState,
            EDttIKMode::PositionOnly,
            Result,
            TargetFK));
    TestTrue(TEXT("position-only status is converged"), Result.Status == EDttIKStatus::Converged);
    TestTrue(TEXT("position-only result identifies the requested tool frame"),
        Result.ToolFrameName == FName(TEXT("tool")));
    TestTrue(TEXT("position-only residual is within tolerance"), Result.PositionResidualMetres <= 1.0e-3);
    TestEqual(TEXT("all revolute positions are returned"), Result.JointPositions.Num(), 5);
    const FDttNamedJointPosition* GripperResult = nullptr;
    for (const FDttNamedJointPosition& Position : Result.JointPositions)
    {
        if (Position.JointName == FName(TEXT("gripper")))
        {
            GripperResult = &Position;
        }
    }
    TestTrue(TEXT("inactive gripper position is returned"), GripperResult != nullptr);
    if (GripperResult != nullptr)
    {
        TestTrue(TEXT("inactive gripper is unchanged"), NearlyEqual(GripperResult->PositionRadians, 0.44));
    }

    // Position-only mode deliberately ignores malformed approach fields.
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionOnly;
    const FDttNamedCanonicalTransform* TargetTool = FindTool(TargetFK, TEXT("tool"));
    if (TargetTool == nullptr)
    {
        return false;
    }
    Request.TargetPositionMetres = TargetTool->Transform.TranslationMetres;
    Request.TargetApproachDirectionCanonical = Vector(
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity(),
        0.0);
    Request.LocalToolApproachAxis = Request.TargetApproachDirectionCanonical;
    Request.SeedJointPositions = StartState;
    TestTrue(
        TEXT("position-only ignores non-finite approach fields"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKApproachAxisTest,
    "DeferredTeleop.M2.IK.PositionPlusApproachAxis",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKApproachAxisTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeIKDescription();
    const TArray<FDttNamedJointPosition> TargetState = Seed(0.20, -0.28, 0.36, 0.71, -0.31);
    const TArray<FDttNamedJointPosition> StartState = Seed(-0.09, 0.11, -0.22, -0.66, -0.31);
    FDttIKResult Result;
    FDttForwardKinematicsResult TargetFK;
    TestTrue(
        TEXT("position-plus-approach-axis hard case returns an inspectable result"),
        SolveTarget(
            Description,
            TargetState,
            StartState,
            EDttIKMode::PositionPlusApproachAxis,
            Result,
            TargetFK));
    AddInfo(FString::Printf(
        TEXT("Approach result: status=%d position_m=%.12g approach_rad=%.12g iterations=%d fk=%d diagnostic=%s"),
        static_cast<int32>(Result.Status), Result.PositionResidualMetres,
        Result.ApproachResidualRadians, Result.Iterations, Result.FKEvaluations,
        *Result.Diagnostic));
    TestTrue(
        TEXT("hard approach case reports local partial progress honestly"),
        Result.Status == EDttIKStatus::Partial
            || Result.Status == EDttIKStatus::IterationLimit);
    TestTrue(TEXT("hard approach case does not claim convergence"), Result.Status != EDttIKStatus::Converged);
    TestTrue(TEXT("hard approach case does not claim workspace unreachability"), Result.Status != EDttIKStatus::Unreachable);
    TestTrue(
        TEXT("hard approach case reports finite residuals"),
        FMath::IsFinite(Result.PositionResidualMetres)
            && FMath::IsFinite(Result.ApproachResidualRadians));
    TestTrue(
        TEXT("hard approach case visibly misses at least one task tolerance"),
        Result.PositionResidualMetres > 1.0e-3
            || Result.ApproachResidualRadians > 2.0 * Pi / 180.0);
    TestTrue(TEXT("hard approach case respects the default FK budget"), Result.FKEvaluations <= 1024);
    TestTrue(TEXT("hard approach case respects the default iteration budget"), Result.Iterations <= 64);
    TestEqual(TEXT("approach task exposes two active rows through residual"), Result.ActiveJointNames.Num(), 4);

    // The same target is reached from a nearby warm seed under the strict
    // default tolerances.  This distinguishes the local failure above from
    // an invalid task definition without claiming a global workspace proof.
    const TArray<FDttNamedJointPosition> NearStartState =
        Seed(0.19, -0.27, 0.35, 0.70, -0.31);
    FDttIKResult NearResult;
    FDttForwardKinematicsResult NearFK;
    TestTrue(
        TEXT("nearby approach-axis seed reaches the same target"),
        SolveTarget(
            Description,
            TargetState,
            NearStartState,
            EDttIKMode::PositionPlusApproachAxis,
            NearResult,
            NearFK));
    TestTrue(TEXT("nearby approach-axis seed converges"), NearResult.Status == EDttIKStatus::Converged);
    TestTrue(TEXT("nearby approach-axis position residual is bounded"), NearResult.PositionResidualMetres <= 1.0e-3);
    TestTrue(TEXT("nearby approach-axis angular residual is bounded"), NearResult.ApproachResidualRadians <= 2.0 * Pi / 180.0);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKConvergedCandidatePolicyTest,
    "DeferredTeleop.M2.IK.ConvergedCandidateOverridesCost",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKConvergedCandidatePolicyTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttIKRequest Request;
    Request.Mode = EDttIKMode::PositionPlusApproachAxis;
    const FDttIKSettings Settings;

    // The existing candidate is cheaper but misses position by 0.1 mm.  The
    // selected candidate satisfies both tolerances while carrying a larger
    // weighted cost because its approach residual is 0.01 rad.
    DeferredTeleop::Kinematics::IKTestBridge::FDeferredTeleopIKTestCandidate ExistingCandidate;
    ExistingCandidate.JointValues = {0.1, 0.2, 0.3};
    ExistingCandidate.PositionResidualMetres = 1.1e-3;
    ExistingCandidate.ApproachResidualRadians = 0.0;
    ExistingCandidate.WeightedCost = 1.21e-6;

    DeferredTeleop::Kinematics::IKTestBridge::FDeferredTeleopIKTestCandidate Candidate;
    Candidate.JointValues = {0.4, 0.5, 0.6};
    Candidate.PositionResidualMetres = 0.5e-3;
    Candidate.ApproachResidualRadians = 0.01;
    Candidate.WeightedCost = 1.025e-5;

    TestTrue(
        TEXT("the policy fixture makes the feasible candidate more expensive"),
        ExistingCandidate.WeightedCost < Candidate.WeightedCost);
    DeferredTeleop::Kinematics::IKTestBridge::FDeferredTeleopIKTestCandidateSelection Selection;
    FString BridgeError;
    TestTrue(
        TEXT("candidate policy test invokes the production selection helper"),
        DeferredTeleop::Kinematics::IKTestBridge::EvaluateCandidateSelectionPolicyForTest(
            Request,
            Settings,
            ExistingCandidate,
            Candidate,
            Selection,
            BridgeError));
    if (!BridgeError.IsEmpty())
    {
        AddError(BridgeError);
    }
    TestTrue(TEXT("a candidate is selected"), Selection.bHasCandidate);
    TestTrue(
        TEXT("convergence takes priority over weighted-cost ordering"),
        Selection.bConvergedCandidate);
    TestTrue(
        TEXT("the converged candidate is accepted despite the cost gate"),
        Selection.bAcceptedCandidate);
    TestEqual(TEXT("the selected candidate values are copied"), Selection.SelectedJointValues.Num(), 3);
    for (int32 Index = 0; Index < Candidate.JointValues.Num(); ++Index)
    {
        TestTrue(
            *FString::Printf(TEXT("selected candidate joint %d is the feasible candidate"), Index),
            NearlyEqual(Selection.SelectedJointValues[Index], Candidate.JointValues[Index], 1.0e-15));
    }
    TestTrue(
        TEXT("the selected converged candidate keeps its position residual"),
        NearlyEqual(Selection.SelectedPositionResidualMetres, Candidate.PositionResidualMetres, 1.0e-15));
    TestTrue(
        TEXT("the selected converged candidate keeps its approach residual"),
        NearlyEqual(Selection.SelectedApproachResidualRadians, Candidate.ApproachResidualRadians, 1.0e-15));
    TestTrue(
        TEXT("the selected converged candidate keeps its higher cost"),
        NearlyEqual(Selection.SelectedWeightedCost, Candidate.WeightedCost, 1.0e-15));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKFreeRollAxisTest,
    "DeferredTeleop.M2.IK.ApproachAxis.FreeRoll",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKFreeRollAxisTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeFreeRollDescription();
    const TArray<FDttNamedJointPosition> PlusRollState =
        Seed(0.20, -0.28, 0.36, 0.71, -0.31);
    const TArray<FDttNamedJointPosition> MinusRollState =
        Seed(0.20, -0.28, 0.36, -0.71, -0.31);
    FDttForwardKinematicsResult PlusFK;
    FDttForwardKinematicsResult MinusFK;
    TestTrue(
        TEXT("pure-Z-offset free-roll positive target FK evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            PlusRollState,
            PlusFK));
    TestTrue(
        TEXT("pure-Z-offset free-roll negative target FK evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            MinusRollState,
            MinusFK));
    const FDttNamedCanonicalTransform* PlusTool =
        FindTool(PlusFK, FName(TEXT("tool")));
    const FDttNamedCanonicalTransform* MinusTool =
        FindTool(MinusFK, FName(TEXT("tool")));
    TestTrue(TEXT("free-roll target tools are present"), PlusTool != nullptr && MinusTool != nullptr);
    if (PlusTool == nullptr || MinusTool == nullptr)
    {
        return false;
    }
    constexpr double ScientificTolerance = 1.0e-12;
    TestTrue(
        TEXT("pure-Z-offset roll leaves target tool position unchanged"),
        (PlusTool->Transform.GetTranslationMetres()
            - MinusTool->Transform.GetTranslationMetres()).Size()
            <= ScientificTolerance);

    const FDttCanonicalVector LocalApproachAxis = Vector(0.0, 0.0, 1.0);
    FVector3d PlusAxis;
    FVector3d MinusAxis;
    TestTrue(
        TEXT("positive free-roll target approach axis is finite"),
        ToolApproachAxis(PlusTool->Transform, LocalApproachAxis, PlusAxis));
    TestTrue(
        TEXT("negative free-roll target approach axis is finite"),
        ToolApproachAxis(MinusTool->Transform, LocalApproachAxis, MinusAxis));
    TestTrue(
        TEXT("pure-Z-offset roll leaves target approach axis unchanged"),
        (PlusAxis - MinusAxis).Size() <= ScientificTolerance);

    const TArray<FDttNamedJointPosition> WarmStartState =
        Seed(0.19, -0.27, 0.35, 0.70, -0.31);
    FDttIKResult PlusResult;
    FDttIKResult MinusResult;
    FDttForwardKinematicsResult PlusTargetFK;
    FDttForwardKinematicsResult MinusTargetFK;
    TestTrue(
        TEXT("positive free-roll target converges by position and approach axis"),
        SolveTarget(
            Description,
            PlusRollState,
            WarmStartState,
            EDttIKMode::PositionPlusApproachAxis,
            PlusResult,
            PlusTargetFK));
    TestTrue(
        TEXT("negative free-roll target converges by position and approach axis"),
        SolveTarget(
            Description,
            MinusRollState,
            WarmStartState,
            EDttIKMode::PositionPlusApproachAxis,
            MinusResult,
            MinusTargetFK));
    TestTrue(TEXT("positive free-roll result is converged"), PlusResult.Status == EDttIKStatus::Converged);
    TestTrue(TEXT("negative free-roll result is converged"), MinusResult.Status == EDttIKStatus::Converged);
    TestTrue(
        TEXT("positive free-roll position residual is bounded"),
        PlusResult.PositionResidualMetres <= 1.0e-3);
    TestTrue(
        TEXT("negative free-roll position residual is bounded"),
        MinusResult.PositionResidualMetres <= 1.0e-3);
    TestTrue(
        TEXT("positive free-roll approach residual is bounded"),
        PlusResult.ApproachResidualRadians <= 2.0 * Pi / 180.0);
    TestTrue(
        TEXT("negative free-roll approach residual is bounded"),
        MinusResult.ApproachResidualRadians <= 2.0 * Pi / 180.0);
    TestEqual(
        TEXT("free-roll solves return the same complete joint state"),
        PlusResult.JointPositions.Num(),
        MinusResult.JointPositions.Num());
    if (PlusResult.JointPositions.Num() == MinusResult.JointPositions.Num())
    {
        for (int32 Index = 0; Index < PlusResult.JointPositions.Num(); ++Index)
        {
            TestTrue(
                *FString::Printf(TEXT("free-roll solution joint %d keeps named order"), Index),
                PlusResult.JointPositions[Index].JointName
                    == MinusResult.JointPositions[Index].JointName);
            TestTrue(
                *FString::Printf(TEXT("free-roll solution joint %d is deterministic"), Index),
                FMath::Abs(
                    PlusResult.JointPositions[Index].PositionRadians
                    - MinusResult.JointPositions[Index].PositionRadians)
                    <= ScientificTolerance);
        }
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKAntiparallelAxisTest,
    "DeferredTeleop.M2.IK.ApproachAxis.AntiparallelIsNotConverged",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKAntiparallelAxisTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttRobotDescription Description = MakeIKDescription();
    const TArray<FDttNamedJointPosition> SeedState = Seed(0.0, 0.0, 0.0, 0.0, 0.0);
    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionPlusApproachAxis;
    FDttForwardKinematicsResult FK;
    TestTrue(
        TEXT("fixture FK evaluates"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            SeedState,
            FK));
    const FDttNamedCanonicalTransform* Tool = FindTool(FK, TEXT("tool"));
    if (Tool == nullptr)
    {
        return false;
    }
    Request.TargetPositionMetres = Tool->Transform.TranslationMetres;
    Request.TargetApproachDirectionCanonical = Vector(0.0, 0.0, -1.0);
    Request.LocalToolApproachAxis = Vector(0.0, 0.0, 1.0);
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = SeedState;
    FDttIKSettings Settings;
    Settings.MaxIterations = 1;
    FDttIKResult Result;
    TestTrue(
        TEXT("antiparallel local solve returns a result"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            Settings,
            Result));
    TestTrue(TEXT("180 degree approach is not falsely converged"), Result.Status != EDttIKStatus::Converged);
    TestTrue(TEXT("antiparallel residual is near pi"), Result.ApproachResidualRadians > 3.0);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKInputValidationTest,
    "DeferredTeleop.M2.IK.RejectsInvalidInputs",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKInputValidationTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeIKDescription();
    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.TargetPositionMetres = Vector(0.4, 0.2, 0.3);
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = Seed(0.0, 0.0, 0.0, 0.0, 0.0);
    FDttIKResult Result;

    Request.JointGroupName = FName(TEXT("missing"));
    TestFalse(
        TEXT("unknown group is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    TestTrue(TEXT("unknown group status is invalid"), Result.Status == EDttIKStatus::InvalidInput);

    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("missing"));
    TestFalse(
        TEXT("unknown tool is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    TestTrue(TEXT("unknown tool status is invalid"), Result.Status == EDttIKStatus::InvalidInput);

    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionPlusApproachAxis;
    Request.TargetApproachDirectionCanonical = Vector(0.0, 0.0, 0.0);
    Request.LocalToolApproachAxis = Vector(0.0, 0.0, 1.0);
    TestFalse(
        TEXT("zero target axis is rejected in approach mode"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    TestTrue(TEXT("zero target axis status is invalid"), Result.Status == EDttIKStatus::InvalidInput);

    Request.Mode = EDttIKMode::PositionOnly;
    Request.SeedJointPositions[0].PositionRadians =
        std::numeric_limits<double>::quiet_NaN();
    TestFalse(
        TEXT("non-finite seed is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    TestTrue(TEXT("non-finite seed status is invalid"), Result.Status == EDttIKStatus::InvalidInput);

    Request.SeedJointPositions = Seed(0.0, 0.0, 0.0, 0.0, 0.0);
    Request.SeedJointPositions.Add(JointPosition(TEXT("fixed_not_a_joint"), 0.0));
    TestFalse(
        TEXT("unknown seed name is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));

    FDttRobotDescription FixedDescription;
    FString FixedJson;
    FString FixedError;
    const bool bFixedLoaded = TestTrue(
        TEXT("generated SO101 description is available for fixed-joint validation"),
        LoadSO101Description(FixedDescription, FixedJson, FixedError));
    if (!bFixedLoaded)
    {
        AddError(FixedError);
        return false;
    }
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("gripper_frame_link"));
    Request.SeedJointPositions = {
        JointPosition(TEXT("shoulder_pan"), 0.0),
        JointPosition(TEXT("shoulder_lift"), 0.0),
        JointPosition(TEXT("elbow_flex"), 0.0),
        JointPosition(TEXT("wrist_flex"), 0.0),
        JointPosition(TEXT("wrist_roll"), 0.0),
        JointPosition(TEXT("gripper"), 0.0),
        JointPosition(TEXT("gripper_frame_joint"), 0.0),
    };
    TestFalse(
        TEXT("fixed joint in a named seed is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            FixedDescription,
            Request,
            FDttIKSettings(),
            Result));

    Request.SeedJointPositions = {
        JointPosition(TEXT("joint_y"), 0.0),
        JointPosition(TEXT("joint_z"), 0.0),
        JointPosition(TEXT("joint_x"), 0.0),
        JointPosition(TEXT("wrist_roll"), 0.0),
    };
    TestFalse(
        TEXT("incomplete named seed is rejected"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKLimitsAndBudgetTest,
    "DeferredTeleop.M2.IK.LimitsAndDeterministicBudget",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKLimitsAndBudgetTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeIKDescription();
    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionOnly;
    Request.TargetPositionMetres = Vector(1.3, -0.9, 1.1);
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = Seed(1.4, 1.5, 1.5, 1.5, 0.27);
    FDttIKSettings Settings;
    Settings.MaxFKEvaluations = 1;
    Settings.MaxIterations = 64;
    FDttIKResult Result;
    TestTrue(
        TEXT("budget-limited solve returns an inspectable status"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            Settings,
            Result));
    TestEqual(TEXT("one-FK budget is reported exactly"), Result.FKEvaluations, 1);
    TestTrue(TEXT("one-FK budget stops before a central Jacobian"), Result.Status == EDttIKStatus::IterationLimit);
    TestTrue(TEXT("projected active limits are reported"), Result.ActiveJointLimits.Num() > 0);
    const FDttNamedJointPosition* Gripper = nullptr;
    for (const FDttNamedJointPosition& Position : Result.JointPositions)
    {
        if (Position.JointName == FName(TEXT("gripper")))
        {
            Gripper = &Position;
        }
    }
    TestTrue(TEXT("inactive gripper remains present"), Gripper != nullptr);
    if (Gripper != nullptr)
    {
        TestTrue(TEXT("inactive gripper remains unchanged at the seed"), NearlyEqual(Gripper->PositionRadians, 0.27));
    }

    Settings.MaxFKEvaluations = 1024;
    Settings.MaxIterations = 0;
    TestTrue(
        TEXT("zero-iteration solve still reports initial FK"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            Settings,
            Result));
    TestTrue(TEXT("zero-iteration status is iteration limit"), Result.Status == EDttIKStatus::IterationLimit);
    TestEqual(TEXT("zero-iteration FK count is one"), Result.FKEvaluations, 1);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKGenericJointCountTest,
    "DeferredTeleop.M2.IK.GenericJointGroup.MoreThanFive",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKGenericJointCountTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeSixDofGenericDescription();
    constexpr double TargetJointRadians = 0.4;
    const TArray<FDttNamedJointPosition> State = {
        JointPosition(TEXT("joint_z0"), 0.0),
        JointPosition(TEXT("joint_z1"), 0.0),
        JointPosition(TEXT("joint_z2"), 0.0),
        JointPosition(TEXT("joint_z3"), 0.0),
        JointPosition(TEXT("joint_z4"), 0.0),
        JointPosition(TEXT("joint_y5"), 0.0),
    };
    // A nonzero first-five seed still leaves the tool at Z=0.  This makes the
    // sixth joint's Z contribution observable rather than allowing the test
    // to converge at a target generated from its own seed.
    TArray<FDttNamedJointPosition> FirstFiveOnly = State;
    FirstFiveOnly[0].PositionRadians = 0.23;
    FirstFiveOnly[1].PositionRadians = -0.17;
    FirstFiveOnly[2].PositionRadians = 0.31;
    FirstFiveOnly[3].PositionRadians = -0.19;
    FirstFiveOnly[4].PositionRadians = 0.11;
    FDttForwardKinematicsResult FirstFiveFK;
    TestTrue(
        TEXT("five Z joints leave the tool Z coordinate unchanged"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            FirstFiveOnly,
            FirstFiveFK));
    const FDttNamedCanonicalTransform* FirstFiveTool = FindTool(FirstFiveFK, TEXT("tool"));
    if (FirstFiveTool == nullptr)
    {
        return false;
    }
    TestTrue(
        TEXT("five Z joints cannot produce the target Z displacement"),
        FMath::Abs(FirstFiveTool->Transform.GetTranslationMetres().Z) <= 1.0e-12);

    const FDttCanonicalVector TargetPosition = Vector(
        FMath::Cos(TargetJointRadians),
        0.0,
        -FMath::Sin(TargetJointRadians));
    TArray<FDttNamedJointPosition> TargetState = State;
    TargetState[5].PositionRadians = TargetJointRadians;
    FDttForwardKinematicsResult TargetFK;
    TestTrue(
        TEXT("the analytic sixth-joint target is reachable by FK"),
        DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            FDttCanonicalTransform::Identity(),
            TargetState,
            TargetFK));
    const FDttNamedCanonicalTransform* TargetTool = FindTool(TargetFK, TEXT("tool"));
    if (TargetTool == nullptr)
    {
        return false;
    }
    TestTrue(
        TEXT("the FK target matches (cos(q6), 0, -sin(q6))"),
        (TargetTool->Transform.GetTranslationMetres() - TargetPosition.ToVector3d()).Size()
            <= 1.0e-12);

    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("six-dof"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionOnly;
    Request.TargetPositionMetres = TargetPosition;
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = State;
    FDttIKResult Result;
    TestTrue(
        TEXT("generic group accepts six active joints and solves a distinct target"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            FDttIKSettings(),
            Result));
    TestTrue(TEXT("six-joint group converges at its known target"), Result.Status == EDttIKStatus::Converged);
    TestEqual(TEXT("all six active names are preserved"), Result.ActiveJointNames.Num(), 6);
    TestTrue(TEXT("six-joint target position is within tolerance"), Result.PositionResidualMetres <= 1.0e-3);
    TestTrue(
        TEXT("a central Jacobian and candidate FK were evaluated"),
        Result.FKEvaluations >= 14);
    const FDttNamedJointPosition* SixthJoint = nullptr;
    for (const FDttNamedJointPosition& Position : Result.JointPositions)
    {
        if (Position.JointName == FName(TEXT("joint_y5")))
        {
            SixthJoint = &Position;
            break;
        }
    }
    TestTrue(TEXT("the sixth joint is returned"), SixthJoint != nullptr);
    if (SixthJoint != nullptr)
    {
        TestTrue(
            TEXT("the sixth joint changes to produce the missing Z component"),
            FMath::Abs(SixthJoint->PositionRadians) > 0.2);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKSingularUnreachableTest,
    "DeferredTeleop.M2.IK.SingularTargetStopsHonestly",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKSingularUnreachableTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeSingularRotationCenterDescription();
    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("singular"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionOnly;
    Request.TargetPositionMetres = Vector(0.2, 0.0, 0.0);
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = {JointPosition(TEXT("joint_z"), 0.0)};

    // Keep the normal 1024-FK work budget.  A one-FK budget would only prove
    // that the solver stopped before it could inspect this singularity.
    const FDttIKSettings Settings;
    FDttIKResult Result;
    TestTrue(
        TEXT("singular unreachable target returns an inspectable result"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            Settings,
            Result));
    TestTrue(
        TEXT("singular target is reported as partial or iteration limited"),
        Result.Status == EDttIKStatus::Partial
            || Result.Status == EDttIKStatus::IterationLimit);
    TestTrue(TEXT("singular target is not falsely converged"), Result.Status != EDttIKStatus::Converged);
    TestTrue(TEXT("local failure does not claim workspace unreachability"), Result.Status != EDttIKStatus::Unreachable);
    TestTrue(TEXT("singular target evaluates more than one FK"), Result.FKEvaluations > 1);
    TestTrue(TEXT("singular solve stays inside the normal FK budget"), Result.FKEvaluations <= Settings.MaxFKEvaluations);
    TestTrue(TEXT("singular solve stays inside the iteration budget"), Result.Iterations <= Settings.MaxIterations);
    TestTrue(TEXT("singular position residual is finite"), FMath::IsFinite(Result.PositionResidualMetres));
    TestTrue(TEXT("singular target remains visibly outside the reachable set"), Result.PositionResidualMetres >= 0.19);
    TestTrue(TEXT("singular approach residual is finite"), FMath::IsFinite(Result.ApproachResidualRadians));

    const FVector3d AchievedPosition = Result.AchievedToolTransform.GetTranslationMetres();
    const FQuat4d AchievedRotation = Result.AchievedToolTransform.GetRotationQuaternion();
    TestTrue(
        TEXT("singular achieved transform is finite"),
        FMath::IsFinite(AchievedPosition.X)
            && FMath::IsFinite(AchievedPosition.Y)
            && FMath::IsFinite(AchievedPosition.Z)
            && FMath::IsFinite(AchievedRotation.X)
            && FMath::IsFinite(AchievedRotation.Y)
            && FMath::IsFinite(AchievedRotation.Z)
            && FMath::IsFinite(AchievedRotation.W));
    TestEqual(TEXT("singular result contains the complete revolute state"), Result.JointPositions.Num(), 1);
    if (Result.JointPositions.Num() == 1)
    {
        const double Joint = Result.JointPositions[0].PositionRadians;
        TestTrue(TEXT("singular joint result is finite"), FMath::IsFinite(Joint));
        TestTrue(TEXT("singular joint result respects its lower limit"), Joint >= -Pi);
        TestTrue(TEXT("singular joint result respects its upper limit"), Joint <= Pi);
    }
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKWarmStartDeterminismTest,
    "DeferredTeleop.M2.IK.WarmStartDeterminism",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKWarmStartDeterminismTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    const FDttRobotDescription Description = MakeIKDescription();
    const TArray<FDttNamedJointPosition> TargetState = Seed(0.18, -0.23, 0.29, 0.12, 0.52);
    const TArray<FDttNamedJointPosition> StartState = Seed(0.0, 0.0, 0.0, 0.0, 0.52);
    FDttIKResult First;
    FDttForwardKinematicsResult FirstFK;
    TestTrue(
        TEXT("first warm-start solve converges"),
        SolveTarget(
            Description,
            TargetState,
            StartState,
            EDttIKMode::PositionOnly,
            First,
            FirstFK));

    const FDttNamedCanonicalTransform* FirstTool = FindTool(FirstFK, TEXT("tool"));
    if (FirstTool == nullptr)
    {
        return false;
    }
    FDttIKRequest NearbyRequest;
    NearbyRequest.JointGroupName = FName(TEXT("arm"));
    NearbyRequest.ToolFrameName = FName(TEXT("tool"));
    NearbyRequest.Mode = EDttIKMode::PositionOnly;
    NearbyRequest.TargetPositionMetres = FDttCanonicalVector::FromVector3d(
        FirstTool->Transform.TranslationMetres.ToVector3d()
        + FVector3d(0.0002, -0.0001, 0.00015));
    NearbyRequest.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    NearbyRequest.SeedJointPositions = First.JointPositions;
    FDttIKResult Second;
    TestTrue(
        TEXT("nearby target accepts the previous solution as a warm start"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            NearbyRequest,
            FDttIKSettings(),
            Second));
    TestTrue(TEXT("nearby target converges"), Second.Status == EDttIKStatus::Converged);
    TestTrue(TEXT("warm start needs no more iterations than the cold solve"), Second.Iterations <= First.Iterations);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKPositiveApproachSignTest,
    "DeferredTeleop.M2.IK.ApproachAxis.PositiveSign",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKPositiveApproachSignTest::RunTest(const FString& Parameters)
{
    (void)Parameters;
    FDttRobotDescription Description;
    Description.ModelId = TEXT("axis-sign");
    Description.ModelRevision = TEXT("axis-sign:1");
    Description.RootLinkName = FName(TEXT("root"));
    Description.Links = {Link(TEXT("root")), Link(TEXT("tool_link"))};
    Description.Joints = {
        Revolute(
            TEXT("joint_y"),
            TEXT("root"),
            TEXT("tool_link"),
            Translation(0.0, 0.0, 0.0),
            Vector(0.0, 1.0, 0.0),
            -Pi,
            Pi),
    };
    FDttRobotJointGroupDescription Group;
    Group.Name = FName(TEXT("arm"));
    Group.JointNames = {FName(TEXT("joint_y"))};
    Description.JointGroups = {Group};
    FDttRobotToolFrameDescription Tool;
    Tool.Name = FName(TEXT("tool"));
    Tool.LinkName = FName(TEXT("tool_link"));
    Description.ToolFrames = {Tool};

    FDttIKRequest Request;
    Request.JointGroupName = FName(TEXT("arm"));
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.Mode = EDttIKMode::PositionPlusApproachAxis;
    Request.TargetPositionMetres = Vector(0.0, 0.0, 0.0);
    Request.TargetApproachDirectionCanonical = Vector(1.0, 0.0, 0.0);
    Request.LocalToolApproachAxis = Vector(0.0, 0.0, 1.0);
    Request.WorldTransformOfRoot = FDttCanonicalTransform::Identity();
    Request.SeedJointPositions = {JointPosition(TEXT("joint_y"), 0.0)};
    FDttIKSettings Settings;
    Settings.MaxIterations = 1;
    Settings.MaxFKEvaluations = 4;
    FDttIKResult Result;
    TestTrue(
        TEXT("axis-sign fixture produces a valid partial result"),
        DeferredTeleop::Kinematics::SolveInverseKinematics(
            Description,
            Request,
            Settings,
            Result));
    TestTrue(TEXT("positive +Y to +X move has positive joint result"), Result.JointPositions.Num() == 1
        && Result.JointPositions[0].PositionRadians > 0.0);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FDeferredTeleopIKCentralJacobianReferenceTest,
    "DeferredTeleop.M2.IK.CentralJacobian.ReferenceFivePoint",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FDeferredTeleopIKCentralJacobianReferenceTest::RunTest(const FString& Parameters)
{
    (void)Parameters;

    FDttRobotDescription Description = MakeIKDescription();
    FDttRobotJointGroupDescription PermutedGroup;
    PermutedGroup.Name = FName(TEXT("permuted-arm"));
    PermutedGroup.JointNames = {
        FName(TEXT("wrist_roll")),
        FName(TEXT("joint_x")),
        FName(TEXT("joint_y")),
        FName(TEXT("joint_z")),
    };
    Description.JointGroups.Add(PermutedGroup);

    const TArray<FDttNamedJointPosition> SeedState =
        Seed(0.27, -0.31, 0.41, -0.23, 0.44);
    const TArray<FDttNamedJointPosition> TargetState =
        Seed(-0.19, 0.33, -0.26, 0.51, 0.44);
    const FDttCanonicalTransform WorldTransformOfRoot =
        FDttCanonicalTransform::FromAxisAngle(
            FVector3d(0.17, -0.08, 0.21),
            FVector3d(0.31, -0.47, 0.62),
            0.37);
    FDttCanonicalTransform CurrentToolTransform;
    FDttCanonicalTransform TargetToolTransform;
    TestTrue(
        TEXT("nonsymmetric seed FK evaluates under a non-identity root"),
        EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            SeedState,
            CurrentToolTransform));
    TestTrue(
        TEXT("nonsymmetric target FK evaluates under a non-identity root"),
        EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            TargetState,
            TargetToolTransform));

    FDttIKRequest Request;
    Request.JointGroupName = PermutedGroup.Name;
    Request.ToolFrameName = FName(TEXT("tool"));
    Request.TargetPositionMetres = TargetToolTransform.TranslationMetres;
    Request.WorldTransformOfRoot = WorldTransformOfRoot;
    Request.SeedJointPositions = SeedState;

    constexpr double H = 1.0e-5;
    FDttIKSettings Settings;
    Settings.PositionWeight = 1.7;
    Settings.OrientationWeight = 0.37;
    Settings.CentralDifferenceStepRadians = H;

    const TArray<FName>& ActiveJointNames = PermutedGroup.JointNames;
    TArray<FDttCanonicalTransform> PlusOneTransforms;
    TArray<FDttCanonicalTransform> MinusOneTransforms;
    TArray<FDttCanonicalTransform> PlusTwoTransforms;
    TArray<FDttCanonicalTransform> MinusTwoTransforms;
    PlusOneTransforms.Reserve(ActiveJointNames.Num());
    MinusOneTransforms.Reserve(ActiveJointNames.Num());
    PlusTwoTransforms.Reserve(ActiveJointNames.Num());
    MinusTwoTransforms.Reserve(ActiveJointNames.Num());
    bool bFivePointSamplesValid = true;
    for (const FName JointName : ActiveJointNames)
    {
        FDttCanonicalTransform PlusOne;
        FDttCanonicalTransform MinusOne;
        FDttCanonicalTransform PlusTwo;
        FDttCanonicalTransform MinusTwo;
        bFivePointSamplesValid = EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            StateWithJointOffset(SeedState, JointName, H),
            PlusOne) && bFivePointSamplesValid;
        bFivePointSamplesValid = EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            StateWithJointOffset(SeedState, JointName, -H),
            MinusOne) && bFivePointSamplesValid;
        bFivePointSamplesValid = EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            StateWithJointOffset(SeedState, JointName, 2.0 * H),
            PlusTwo) && bFivePointSamplesValid;
        bFivePointSamplesValid = EvaluateToolTransform(
            Description,
            WorldTransformOfRoot,
            StateWithJointOffset(SeedState, JointName, -2.0 * H),
            MinusTwo) && bFivePointSamplesValid;
        PlusOneTransforms.Add(PlusOne);
        MinusOneTransforms.Add(MinusOne);
        PlusTwoTransforms.Add(PlusTwo);
        MinusTwoTransforms.Add(MinusTwo);
    }
    TestTrue(TEXT("independent five-point FK samples are finite"), bFivePointSamplesValid);
    if (!bFivePointSamplesValid)
    {
        return false;
    }

    const FVector3d CurrentPosition = CurrentToolTransform.GetTranslationMetres();
    const FVector3d TargetPosition = TargetToolTransform.GetTranslationMetres();
    const FVector3d PositionError = TargetPosition - CurrentPosition;
    const double PositionScale = FMath::Sqrt(Settings.PositionWeight);
    const double OrientationScale = FMath::Sqrt(Settings.OrientationWeight);
    const double ExpectedPositionError[3] = {
        PositionScale * PositionError.X,
        PositionScale * PositionError.Y,
        PositionScale * PositionError.Z,
    };
    constexpr double JacobianTolerance = 1.0e-6;

    Request.Mode = EDttIKMode::PositionOnly;
    DeferredTeleop::Kinematics::IKTestBridge::FDeferredTeleopIKTestJacobian PositionBridge;
    FString BridgeError;
    TestTrue(
        TEXT("position-only bridge calls the production Jacobian helper"),
        DeferredTeleop::Kinematics::IKTestBridge::BuildTaskJacobianForTest(
            Description,
            Request,
            Settings,
            CurrentToolTransform,
            PlusOneTransforms,
            MinusOneTransforms,
            PositionBridge,
            BridgeError));
    if (PositionBridge.Jacobian.Num() != ActiveJointNames.Num()
        || PositionBridge.TaskError.Num() != 3)
    {
        TestTrue(TEXT("position-only bridge returned 3 rows and all columns"), false);
        return false;
    }
    TestEqual(
        TEXT("position-only bridge preserves the named group column count"),
        PositionBridge.ActiveJointNames.Num(),
        ActiveJointNames.Num());
    for (int32 Column = 0; Column < ActiveJointNames.Num(); ++Column)
    {
        TestTrue(
            *FString::Printf(TEXT("position-only column %d keeps named group order"), Column),
            PositionBridge.ActiveJointNames[Column] == ActiveJointNames[Column]);
        TestEqual(
            *FString::Printf(TEXT("position-only column %d has three rows"), Column),
            PositionBridge.Jacobian[Column].Num(),
            3);
        TestTrue(
            *FString::Printf(TEXT("position-only task error row 0 is weighted")),
            FMath::Abs(PositionBridge.TaskError[0] - ExpectedPositionError[0])
                <= JacobianTolerance);
        const FVector3d FivePointPositionDerivative = (
            MinusTwoTransforms[Column].GetTranslationMetres()
            - 8.0 * MinusOneTransforms[Column].GetTranslationMetres()
            + 8.0 * PlusOneTransforms[Column].GetTranslationMetres()
            - PlusTwoTransforms[Column].GetTranslationMetres())
            * (1.0 / (12.0 * H));
        const double ExpectedJacobian[3] = {
            PositionScale * FivePointPositionDerivative.X,
            PositionScale * FivePointPositionDerivative.Y,
            PositionScale * FivePointPositionDerivative.Z,
        };
        for (int32 Row = 0; Row < 3; ++Row)
        {
            TestTrue(
                *FString::Printf(TEXT("position-only J column %d row %d matches five-point FK"), Column, Row),
                FMath::IsFinite(PositionBridge.Jacobian[Column][Row])
                    && FMath::Abs(PositionBridge.Jacobian[Column][Row] - ExpectedJacobian[Row])
                        <= JacobianTolerance);
        }
    }
    for (int32 Row = 0; Row < 3; ++Row)
    {
        TestTrue(
            *FString::Printf(TEXT("position-only task error row %d matches independent target"), Row),
            FMath::Abs(PositionBridge.TaskError[Row] - ExpectedPositionError[Row])
                <= JacobianTolerance);
    }

    FQuat4d TargetRotation = TargetToolTransform.GetRotationQuaternion();
    TargetRotation.Normalize();
    const FDttCanonicalVector LocalToolApproachAxis = Vector(0.0, 0.0, 1.0);
    const FVector3d TargetApproachDirection = TargetRotation.RotateVector(
        LocalToolApproachAxis.ToVector3d());
    FDttIKRequest ApproachRequest = Request;
    ApproachRequest.Mode = EDttIKMode::PositionPlusApproachAxis;
    ApproachRequest.TargetApproachDirectionCanonical =
        FDttCanonicalVector::FromVector3d(TargetApproachDirection);
    ApproachRequest.LocalToolApproachAxis = LocalToolApproachAxis;
    DeferredTeleop::Kinematics::IKTestBridge::FDeferredTeleopIKTestJacobian ApproachBridge;
    BridgeError.Reset();
    TestTrue(
        TEXT("approach-axis bridge calls the production Jacobian helper"),
        DeferredTeleop::Kinematics::IKTestBridge::BuildTaskJacobianForTest(
            Description,
            ApproachRequest,
            Settings,
            CurrentToolTransform,
            PlusOneTransforms,
            MinusOneTransforms,
            ApproachBridge,
            BridgeError));
    if (ApproachBridge.Jacobian.Num() != ActiveJointNames.Num()
        || ApproachBridge.TaskError.Num() != 5)
    {
        TestTrue(TEXT("approach-axis bridge returned 5 rows and all columns"), false);
        return false;
    }

    FVector3d CurrentApproachAxis;
    FVector3d TargetApproachAxis;
    TestTrue(
        TEXT("independent current approach axis is finite"),
        ToolApproachAxis(CurrentToolTransform, LocalToolApproachAxis, CurrentApproachAxis));
    TestTrue(
        TEXT("independent target approach axis is finite"),
        NormalizeOracleDirection(TargetApproachDirection, TargetApproachAxis));
    FVector3d ExpectedBasisU;
    FVector3d ExpectedBasisV;
    FVector3d ExpectedApproachError3;
    double ExpectedApproachAngle = 0.0;
    TestTrue(
        TEXT("independent approach basis and error are finite"),
        BuildOracleApproachBasis(
            CurrentApproachAxis,
            TargetApproachAxis,
            ExpectedBasisU,
            ExpectedBasisV,
            ExpectedApproachError3,
            ExpectedApproachAngle));
    TestTrue(
        TEXT("bridge current approach axis matches independent construction"),
        (ApproachBridge.CurrentApproachAxis - CurrentApproachAxis).Size()
            <= JacobianTolerance);
    TestTrue(
        TEXT("bridge target approach axis matches independent construction"),
        (ApproachBridge.TargetApproachAxis - TargetApproachAxis).Size()
            <= JacobianTolerance);
    TestTrue(
        TEXT("bridge approach basis U matches independent construction"),
        (ApproachBridge.ApproachBasisU - ExpectedBasisU).Size()
            <= JacobianTolerance);
    TestTrue(
        TEXT("bridge approach basis V matches independent construction"),
        (ApproachBridge.ApproachBasisV - ExpectedBasisV).Size()
            <= JacobianTolerance);

    const double ExpectedApproachError[5] = {
        ExpectedPositionError[0],
        ExpectedPositionError[1],
        ExpectedPositionError[2],
        OrientationScale * FVector3d::DotProduct(ExpectedApproachError3, ExpectedBasisU),
        OrientationScale * FVector3d::DotProduct(ExpectedApproachError3, ExpectedBasisV),
    };
    TestTrue(
        TEXT("bridge approach residual matches the independent S2 angle"),
        FMath::Abs(ApproachBridge.ApproachResidualRadians - ExpectedApproachAngle)
            <= JacobianTolerance);
    for (int32 Row = 0; Row < 5; ++Row)
    {
        TestTrue(
            *FString::Printf(TEXT("approach task error row %d matches independent projection"), Row),
            FMath::IsFinite(ApproachBridge.TaskError[Row])
                && FMath::Abs(ApproachBridge.TaskError[Row] - ExpectedApproachError[Row])
                    <= JacobianTolerance);
    }

    for (int32 Column = 0; Column < ActiveJointNames.Num(); ++Column)
    {
        TestTrue(
            *FString::Printf(TEXT("approach column %d keeps named group order"), Column),
            ApproachBridge.ActiveJointNames[Column] == ActiveJointNames[Column]);
        TestEqual(
            *FString::Printf(TEXT("approach column %d has five rows"), Column),
            ApproachBridge.Jacobian[Column].Num(),
            5);

        FVector3d PlusOneAxis;
        FVector3d MinusOneAxis;
        FVector3d PlusTwoAxis;
        FVector3d MinusTwoAxis;
        const bool bAxesValid =
            ToolApproachAxis(PlusOneTransforms[Column], LocalToolApproachAxis, PlusOneAxis)
            && ToolApproachAxis(MinusOneTransforms[Column], LocalToolApproachAxis, MinusOneAxis)
            && ToolApproachAxis(PlusTwoTransforms[Column], LocalToolApproachAxis, PlusTwoAxis)
            && ToolApproachAxis(MinusTwoTransforms[Column], LocalToolApproachAxis, MinusTwoAxis);
        TestTrue(
            *FString::Printf(TEXT("approach five-point axis samples for column %d are finite"), Column),
            bAxesValid);
        if (!bAxesValid)
        {
            return false;
        }
        const FVector3d FivePointAxisDerivative = (
            MinusTwoAxis
            - 8.0 * MinusOneAxis
            + 8.0 * PlusOneAxis
            - PlusTwoAxis)
            * (1.0 / (12.0 * H));
        const double ExpectedJacobian[5] = {
            PositionScale * (
                (MinusTwoTransforms[Column].GetTranslationMetres().X
                    - 8.0 * MinusOneTransforms[Column].GetTranslationMetres().X
                    + 8.0 * PlusOneTransforms[Column].GetTranslationMetres().X
                    - PlusTwoTransforms[Column].GetTranslationMetres().X)
                * (1.0 / (12.0 * H))),
            PositionScale * (
                (MinusTwoTransforms[Column].GetTranslationMetres().Y
                    - 8.0 * MinusOneTransforms[Column].GetTranslationMetres().Y
                    + 8.0 * PlusOneTransforms[Column].GetTranslationMetres().Y
                    - PlusTwoTransforms[Column].GetTranslationMetres().Y)
                * (1.0 / (12.0 * H))),
            PositionScale * (
                (MinusTwoTransforms[Column].GetTranslationMetres().Z
                    - 8.0 * MinusOneTransforms[Column].GetTranslationMetres().Z
                    + 8.0 * PlusOneTransforms[Column].GetTranslationMetres().Z
                    - PlusTwoTransforms[Column].GetTranslationMetres().Z)
                * (1.0 / (12.0 * H))),
            OrientationScale * FVector3d::DotProduct(FivePointAxisDerivative, ExpectedBasisU),
            OrientationScale * FVector3d::DotProduct(FivePointAxisDerivative, ExpectedBasisV),
        };
        for (int32 Row = 0; Row < 5; ++Row)
        {
            TestTrue(
                *FString::Printf(TEXT("approach J column %d row %d matches five-point FK"), Column, Row),
                FMath::IsFinite(ApproachBridge.Jacobian[Column][Row])
                    && FMath::Abs(ApproachBridge.Jacobian[Column][Row] - ExpectedJacobian[Row])
                        <= JacobianTolerance);
        }
    }
    return true;
}

} // namespace DeferredTeleop::Tests::IK

#endif
