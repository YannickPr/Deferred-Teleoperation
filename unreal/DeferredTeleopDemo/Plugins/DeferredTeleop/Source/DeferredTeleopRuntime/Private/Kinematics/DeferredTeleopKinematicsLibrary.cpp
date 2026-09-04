#include "Kinematics/DeferredTeleopKinematicsLibrary.h"

#include "Math/UnrealMathUtility.h"

namespace DeferredTeleop::Kinematics::Private
{
constexpr double AxisNormTolerance = 1.0e-6;
constexpr double QuaternionNormTolerance = 1.0e-6;
constexpr double ScaleTolerance = 1.0e-5;

bool Fail(FString& OutError, const FString& Message)
{
    OutError = Message;
    return false;
}

bool IsFiniteQuaternion(const FQuat4d& Quaternion)
{
    return FMath::IsFinite(Quaternion.X)
        && FMath::IsFinite(Quaternion.Y)
        && FMath::IsFinite(Quaternion.Z)
        && FMath::IsFinite(Quaternion.W);
}

bool IsUnitAxis(const FDttCanonicalVector& Axis)
{
    if (!Axis.IsFinite())
    {
        return false;
    }

    const double NormSquared = Axis.X * Axis.X + Axis.Y * Axis.Y + Axis.Z * Axis.Z;
    return FMath::IsFinite(NormSquared)
        && NormSquared > UE_DOUBLE_SMALL_NUMBER
        && FMath::Abs(NormSquared - 1.0) <= AxisNormTolerance;
}

FQuat4d NormalizeQuaternion(const FQuat4d& Quaternion)
{
    const double NormSquared =
        Quaternion.X * Quaternion.X
        + Quaternion.Y * Quaternion.Y
        + Quaternion.Z * Quaternion.Z
        + Quaternion.W * Quaternion.W;
    const double InverseNorm = 1.0 / FMath::Sqrt(NormSquared);
    return FQuat4d(
        Quaternion.X * InverseNorm,
        Quaternion.Y * InverseNorm,
        Quaternion.Z * InverseNorm,
        Quaternion.W * InverseNorm);
}

struct FDttMatrix3
{
    double M[3][3] = {
        {1.0, 0.0, 0.0},
        {0.0, 1.0, 0.0},
        {0.0, 0.0, 1.0},
    };
};

FDttMatrix3 Multiply(const FDttMatrix3& Left, const FDttMatrix3& Right)
{
    FDttMatrix3 Result;
    for (int32 Row = 0; Row < 3; ++Row)
    {
        for (int32 Column = 0; Column < 3; ++Column)
        {
            Result.M[Row][Column] = 0.0;
            for (int32 Index = 0; Index < 3; ++Index)
            {
                Result.M[Row][Column] += Left.M[Row][Index] * Right.M[Index][Column];
            }
        }
    }
    return Result;
}

FDttMatrix3 CanonicalRotationMatrix(const FQuat4d& InputQuaternion)
{
    const FQuat4d Quaternion = NormalizeQuaternion(InputQuaternion);
    const double XX = Quaternion.X * Quaternion.X;
    const double YY = Quaternion.Y * Quaternion.Y;
    const double ZZ = Quaternion.Z * Quaternion.Z;
    const double XY = Quaternion.X * Quaternion.Y;
    const double XZ = Quaternion.X * Quaternion.Z;
    const double YZ = Quaternion.Y * Quaternion.Z;
    const double WX = Quaternion.W * Quaternion.X;
    const double WY = Quaternion.W * Quaternion.Y;
    const double WZ = Quaternion.W * Quaternion.Z;

    FDttMatrix3 Result;
    Result.M[0][0] = 1.0 - 2.0 * (YY + ZZ);
    Result.M[0][1] = 2.0 * (XY - WZ);
    Result.M[0][2] = 2.0 * (XZ + WY);
    Result.M[1][0] = 2.0 * (XY + WZ);
    Result.M[1][1] = 1.0 - 2.0 * (XX + ZZ);
    Result.M[1][2] = 2.0 * (YZ - WX);
    Result.M[2][0] = 2.0 * (XZ - WY);
    Result.M[2][1] = 2.0 * (YZ + WX);
    Result.M[2][2] = 1.0 - 2.0 * (XX + YY);
    return Result;
}

/** Apply S = diag(1, -1, 1), i.e. a change from canonical RH to Unreal LH. */
FDttMatrix3 ReflectYBasis(const FDttMatrix3& Matrix)
{
    constexpr double Signs[3] = {1.0, -1.0, 1.0};
    FDttMatrix3 Result;
    for (int32 Row = 0; Row < 3; ++Row)
    {
        for (int32 Column = 0; Column < 3; ++Column)
        {
            Result.M[Row][Column] = Signs[Row] * Matrix.M[Row][Column] * Signs[Column];
        }
    }
    return Result;
}

bool IsFiniteMatrix(const FDttMatrix3& Matrix)
{
    for (int32 Row = 0; Row < 3; ++Row)
    {
        for (int32 Column = 0; Column < 3; ++Column)
        {
            if (!FMath::IsFinite(Matrix.M[Row][Column]))
            {
                return false;
            }
        }
    }
    return true;
}

bool MatrixToQuaternion(const FDttMatrix3& Matrix, FQuat4d& OutQuaternion)
{
    if (!IsFiniteMatrix(Matrix))
    {
        return false;
    }

    const double Trace = Matrix.M[0][0] + Matrix.M[1][1] + Matrix.M[2][2];
    double X = 0.0;
    double Y = 0.0;
    double Z = 0.0;
    double W = 0.0;

    if (Trace > 0.0)
    {
        const double S = 2.0 * FMath::Sqrt(FMath::Max(0.0, Trace + 1.0));
        if (S <= UE_DOUBLE_SMALL_NUMBER)
        {
            return false;
        }
        W = 0.25 * S;
        X = (Matrix.M[2][1] - Matrix.M[1][2]) / S;
        Y = (Matrix.M[0][2] - Matrix.M[2][0]) / S;
        Z = (Matrix.M[1][0] - Matrix.M[0][1]) / S;
    }
    else if (Matrix.M[0][0] > Matrix.M[1][1] && Matrix.M[0][0] > Matrix.M[2][2])
    {
        const double S = 2.0 * FMath::Sqrt(FMath::Max(
            0.0,
            1.0 + Matrix.M[0][0] - Matrix.M[1][1] - Matrix.M[2][2]));
        if (S <= UE_DOUBLE_SMALL_NUMBER)
        {
            return false;
        }
        W = (Matrix.M[2][1] - Matrix.M[1][2]) / S;
        X = 0.25 * S;
        Y = (Matrix.M[0][1] + Matrix.M[1][0]) / S;
        Z = (Matrix.M[0][2] + Matrix.M[2][0]) / S;
    }
    else if (Matrix.M[1][1] > Matrix.M[2][2])
    {
        const double S = 2.0 * FMath::Sqrt(FMath::Max(
            0.0,
            1.0 + Matrix.M[1][1] - Matrix.M[0][0] - Matrix.M[2][2]));
        if (S <= UE_DOUBLE_SMALL_NUMBER)
        {
            return false;
        }
        W = (Matrix.M[0][2] - Matrix.M[2][0]) / S;
        X = (Matrix.M[0][1] + Matrix.M[1][0]) / S;
        Y = 0.25 * S;
        Z = (Matrix.M[1][2] + Matrix.M[2][1]) / S;
    }
    else
    {
        const double S = 2.0 * FMath::Sqrt(FMath::Max(
            0.0,
            1.0 + Matrix.M[2][2] - Matrix.M[0][0] - Matrix.M[1][1]));
        if (S <= UE_DOUBLE_SMALL_NUMBER)
        {
            return false;
        }
        W = (Matrix.M[1][0] - Matrix.M[0][1]) / S;
        X = (Matrix.M[0][2] + Matrix.M[2][0]) / S;
        Y = (Matrix.M[1][2] + Matrix.M[2][1]) / S;
        Z = 0.25 * S;
    }

    const FQuat4d Candidate(X, Y, Z, W);
    if (!IsFiniteQuaternion(Candidate))
    {
        return false;
    }

    const double NormSquared =
        Candidate.X * Candidate.X
        + Candidate.Y * Candidate.Y
        + Candidate.Z * Candidate.Z
        + Candidate.W * Candidate.W;
    if (!FMath::IsFinite(NormSquared) || NormSquared <= UE_DOUBLE_SMALL_NUMBER)
    {
        return false;
    }

    OutQuaternion = NormalizeQuaternion(Candidate);

    // q and -q encode the same rotation.  Pick a stable hemisphere for
    // deterministic Blueprint/debug output and cross-platform fixtures.
    if (OutQuaternion.W < 0.0
        || (FMath::IsNearlyZero(OutQuaternion.W)
            && (OutQuaternion.X < 0.0
                || (FMath::IsNearlyZero(OutQuaternion.X) && OutQuaternion.Y < 0.0)
                || (FMath::IsNearlyZero(OutQuaternion.X)
                    && FMath::IsNearlyZero(OutQuaternion.Y)
                    && OutQuaternion.Z < 0.0))))
    {
        OutQuaternion.X = -OutQuaternion.X;
        OutQuaternion.Y = -OutQuaternion.Y;
        OutQuaternion.Z = -OutQuaternion.Z;
        OutQuaternion.W = -OutQuaternion.W;
    }
    return true;
}

bool ValidateTransform(const FDttCanonicalTransform& Transform, const FString& Path, FString& OutError)
{
    if (!Transform.IsFinite())
    {
        return Fail(OutError, Path + TEXT(" must contain only finite values"));
    }
    if (!Transform.Rotation.IsNormalized(QuaternionNormTolerance))
    {
        return Fail(OutError, Path + TEXT(" rotation must be a normalized quaternion"));
    }
    return true;
}

bool ValidateAxis(const FDttCanonicalVector& Axis, const FString& Path, FString& OutError)
{
    if (!Axis.IsFinite())
    {
        return Fail(OutError, Path + TEXT(" must contain only finite values"));
    }
    const double NormSquared = Axis.X * Axis.X + Axis.Y * Axis.Y + Axis.Z * Axis.Z;
    if (!FMath::IsFinite(NormSquared) || NormSquared <= UE_DOUBLE_SMALL_NUMBER)
    {
        return Fail(OutError, Path + TEXT(" must be non-zero"));
    }
    if (FMath::Abs(NormSquared - 1.0) > AxisNormTolerance)
    {
        return Fail(OutError, Path + TEXT(" must be unit length"));
    }
    return true;
}
} // namespace DeferredTeleop::Kinematics::Private

void FDttValidatedRobotModel::Reset()
{
    LinkIndexByName.Reset();
    JointIndexByName.Reset();
    ToolIndexByName.Reset();
    ParentJointByLink.Reset();
    LinkTraversalOrder.Reset();
    JointTraversalOrder.Reset();
}

int32 FDttValidatedRobotModel::FindLinkIndex(FName LinkName) const
{
    const int32* Found = LinkIndexByName.Find(LinkName);
    return Found != nullptr ? *Found : INDEX_NONE;
}

int32 FDttValidatedRobotModel::FindJointIndex(FName JointName) const
{
    const int32* Found = JointIndexByName.Find(JointName);
    return Found != nullptr ? *Found : INDEX_NONE;
}

int32 FDttValidatedRobotModel::FindToolIndex(FName ToolName) const
{
    const int32* Found = ToolIndexByName.Find(ToolName);
    return Found != nullptr ? *Found : INDEX_NONE;
}

namespace DeferredTeleop::Kinematics
{
using namespace Private;

bool ValidateRobotDescription(
    const FDttRobotDescription& Description,
    FDttValidatedRobotModel& OutModel,
    FString& OutError)
{
    OutModel.Reset();
    OutError.Reset();

    if (Description.Links.Num() == 0)
    {
        return Fail(OutError, TEXT("robot description must contain at least one link"));
    }
    if (Description.RootLinkName.IsNone())
    {
        return Fail(OutError, TEXT("robot description root_link_name is required"));
    }
    if (Description.ModelId.IsEmpty() || Description.ModelRevision.IsEmpty())
    {
        return Fail(
            OutError,
            TEXT("robot description model_id and model_revision are required"));
    }

    OutModel.LinkIndexByName.Reserve(Description.Links.Num());
    for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
    {
        const FName LinkName = Description.Links[LinkIndex].Name;
        if (LinkName.IsNone())
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("links[%d].name must be non-empty"), LinkIndex));
        }
        if (OutModel.LinkIndexByName.Contains(LinkName))
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("duplicate link name: %s"), *LinkName.ToString()));
        }
        OutModel.LinkIndexByName.Add(LinkName, LinkIndex);
    }

    const int32 RootLinkIndex = OutModel.FindLinkIndex(Description.RootLinkName);
    if (RootLinkIndex == INDEX_NONE)
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("root link does not exist: %s"),
                *Description.RootLinkName.ToString()));
    }

    OutModel.ParentJointByLink.Init(INDEX_NONE, Description.Links.Num());
    OutModel.JointIndexByName.Reserve(Description.Joints.Num());
    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Name.IsNone())
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("joints[%d].name must be non-empty"), JointIndex));
        }
        if (OutModel.JointIndexByName.Contains(Joint.Name))
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("duplicate joint name: %s"), *Joint.Name.ToString()));
        }
        OutModel.JointIndexByName.Add(Joint.Name, JointIndex);

        const int32 ParentIndex = OutModel.FindLinkIndex(Joint.ParentLink);
        const int32 ChildIndex = OutModel.FindLinkIndex(Joint.ChildLink);
        if (ParentIndex == INDEX_NONE || ChildIndex == INDEX_NONE)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("joint %s references an unknown parent or child link"),
                    *Joint.Name.ToString()));
        }
        if (ParentIndex == ChildIndex)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("joint %s cannot connect a link to itself"),
                    *Joint.Name.ToString()));
        }
        if (OutModel.ParentJointByLink[ChildIndex] != INDEX_NONE)
        {
            const FDttRobotJointDescription& ExistingJoint =
                Description.Joints[OutModel.ParentJointByLink[ChildIndex]];
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("link %s has multiple parent joints: %s and %s"),
                    *Joint.ChildLink.ToString(),
                    *ExistingJoint.Name.ToString(),
                    *Joint.Name.ToString()));
        }
        OutModel.ParentJointByLink[ChildIndex] = JointIndex;

        const FString JointPath = FString::Printf(TEXT("joint %s parent_to_joint"), *Joint.Name.ToString());
        if (!ValidateTransform(Joint.ParentToJoint, JointPath, OutError))
        {
            return false;
        }

        if (Joint.Type == EDttRobotJointType::Revolute)
        {
            if (!ValidateAxis(
                    Joint.AxisJointFrame,
                    FString::Printf(TEXT("joint %s axis_joint_frame"), *Joint.Name.ToString()),
                    OutError))
            {
                return false;
            }
            if (Joint.bHasPositionLimits)
            {
                if (!FMath::IsFinite(Joint.LowerPositionRadians)
                    || !FMath::IsFinite(Joint.UpperPositionRadians))
                {
                    return Fail(
                        OutError,
                        FString::Printf(
                            TEXT("joint %s position limits must be finite"),
                            *Joint.Name.ToString()));
                }
                if (Joint.LowerPositionRadians > Joint.UpperPositionRadians)
                {
                    return Fail(
                        OutError,
                        FString::Printf(
                            TEXT("joint %s position limits must be ordered"),
                            *Joint.Name.ToString()));
                }
            }
        }
        else if (Joint.Type == EDttRobotJointType::Fixed)
        {
            if (Joint.bHasPositionLimits)
            {
                return Fail(
                    OutError,
                    FString::Printf(
                        TEXT("fixed joint %s cannot define position limits"),
                        *Joint.Name.ToString()));
            }
        }
        else
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("joint %s has an unsupported type"), *Joint.Name.ToString()));
        }
    }

    TSet<FName> GroupNames;
    for (int32 GroupIndex = 0; GroupIndex < Description.JointGroups.Num(); ++GroupIndex)
    {
        const FDttRobotJointGroupDescription& Group = Description.JointGroups[GroupIndex];
        if (Group.Name.IsNone())
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("joint_groups[%d].name must be non-empty"), GroupIndex));
        }
        if (GroupNames.Contains(Group.Name))
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("duplicate joint group name: %s"), *Group.Name.ToString()));
        }
        if (Group.JointNames.Num() == 0)
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("joint group %s must not be empty"), *Group.Name.ToString()));
        }
        GroupNames.Add(Group.Name);

        TSet<FName> NamesInGroup;
        for (const FName JointName : Group.JointNames)
        {
            const int32 JointIndex = OutModel.FindJointIndex(JointName);
            if (JointIndex == INDEX_NONE)
            {
                return Fail(
                    OutError,
                    FString::Printf(
                        TEXT("joint group %s references unknown joint %s"),
                        *Group.Name.ToString(),
                        *JointName.ToString()));
            }
            if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
            {
                return Fail(
                    OutError,
                    FString::Printf(
                        TEXT("joint group %s references non-revolute joint %s"),
                        *Group.Name.ToString(),
                        *JointName.ToString()));
            }
            if (NamesInGroup.Contains(JointName))
            {
                return Fail(
                    OutError,
                    FString::Printf(
                        TEXT("joint group %s contains duplicate joint %s"),
                        *Group.Name.ToString(),
                        *JointName.ToString()));
            }
            NamesInGroup.Add(JointName);
        }
    }

    TArray<TArray<int32>> ChildJointsByLink;
    ChildJointsByLink.SetNum(Description.Links.Num());
    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const int32 ParentIndex = OutModel.FindLinkIndex(Description.Joints[JointIndex].ParentLink);
        ChildJointsByLink[ParentIndex].Add(JointIndex);
    }
    for (TArray<int32>& ChildJoints : ChildJointsByLink)
    {
        ChildJoints.Sort([&Description](const int32 LeftIndex, const int32 RightIndex)
        {
            return Description.Joints[LeftIndex].Name.ToString()
                < Description.Joints[RightIndex].Name.ToString();
        });
    }

    // Check every connected component before applying the single-root rule so
    // a disconnected cycle reports the cycle itself rather than a misleading
    // root-count error.
    TArray<uint8> CycleState;
    CycleState.Init(0, Description.Links.Num());
    auto DetectCycle = [&](auto&& Self, int32 LinkIndex) -> bool
    {
        if (CycleState[LinkIndex] == 1)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("cycle detected at link %s"),
                    *Description.Links[LinkIndex].Name.ToString()));
        }
        if (CycleState[LinkIndex] == 2)
        {
            return true;
        }
        CycleState[LinkIndex] = 1;
        for (const int32 JointIndex : ChildJointsByLink[LinkIndex])
        {
            const int32 ChildIndex = OutModel.FindLinkIndex(Description.Joints[JointIndex].ChildLink);
            if (!Self(Self, ChildIndex))
            {
                return false;
            }
        }
        CycleState[LinkIndex] = 2;
        return true;
    };
    for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
    {
        if (CycleState[LinkIndex] == 0 && !DetectCycle(DetectCycle, LinkIndex))
        {
            OutModel.Reset();
            return false;
        }
    }

    TArray<int32> Roots;
    Roots.Reserve(Description.Links.Num());
    for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
    {
        if (OutModel.ParentJointByLink[LinkIndex] == INDEX_NONE)
        {
            Roots.Add(LinkIndex);
        }
    }
    if (Roots.Num() != 1)
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("robot description must have exactly one root link; found %d"),
                Roots.Num()));
    }
    if (Roots[0] != RootLinkIndex)
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("configured root %s is not the tree root"),
                *Description.RootLinkName.ToString()));
    }

    TArray<uint8> VisitState;
    VisitState.Init(0, Description.Links.Num());
    auto Visit = [&](auto&& Self, int32 LinkIndex) -> bool
    {
        if (VisitState[LinkIndex] == 1)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("cycle detected at link %s"),
                    *Description.Links[LinkIndex].Name.ToString()));
        }
        if (VisitState[LinkIndex] == 2)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("link %s is reachable more than once"),
                    *Description.Links[LinkIndex].Name.ToString()));
        }

        VisitState[LinkIndex] = 1;
        OutModel.LinkTraversalOrder.Add(LinkIndex);
        for (const int32 JointIndex : ChildJointsByLink[LinkIndex])
        {
            OutModel.JointTraversalOrder.Add(JointIndex);
            const int32 ChildIndex = OutModel.FindLinkIndex(Description.Joints[JointIndex].ChildLink);
            if (!Self(Self, ChildIndex))
            {
                return false;
            }
        }
        VisitState[LinkIndex] = 2;
        return true;
    };

    if (!Visit(Visit, RootLinkIndex))
    {
        OutModel.Reset();
        return false;
    }
    if (OutModel.LinkTraversalOrder.Num() != Description.Links.Num())
    {
        for (int32 LinkIndex = 0; LinkIndex < Description.Links.Num(); ++LinkIndex)
        {
            if (VisitState[LinkIndex] != 2)
            {
                return Fail(
                    OutError,
                    FString::Printf(
                        TEXT("disconnected link: %s"),
                        *Description.Links[LinkIndex].Name.ToString()));
            }
        }
        return Fail(OutError, TEXT("robot description contains disconnected links"));
    }

    OutModel.ToolIndexByName.Reserve(Description.ToolFrames.Num());
    for (int32 ToolIndex = 0; ToolIndex < Description.ToolFrames.Num(); ++ToolIndex)
    {
        const FDttRobotToolFrameDescription& Tool = Description.ToolFrames[ToolIndex];
        if (Tool.Name.IsNone())
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("tool_frames[%d].name must be non-empty"), ToolIndex));
        }
        if (OutModel.ToolIndexByName.Contains(Tool.Name))
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("duplicate tool frame name: %s"), *Tool.Name.ToString()));
        }
        if (OutModel.FindLinkIndex(Tool.LinkName) == INDEX_NONE)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("tool frame %s references an unknown link %s"),
                    *Tool.Name.ToString(),
                    *Tool.LinkName.ToString()));
        }
        if (!ValidateTransform(
                Tool.LinkToTool,
                FString::Printf(TEXT("tool frame %s link_to_tool"), *Tool.Name.ToString()),
                OutError))
        {
            return false;
        }
        OutModel.ToolIndexByName.Add(Tool.Name, ToolIndex);
    }

    return true;
}

bool EvaluateForwardKinematics(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    const TArray<FDttNamedJointPosition>& JointPositions,
    FDttForwardKinematicsResult& OutResult)
{
    OutResult = FDttForwardKinematicsResult();

    FDttValidatedRobotModel Model;
    FString Error;
    if (!ValidateRobotDescription(Description, Model, Error))
    {
        OutResult.ErrorMessage = Error;
        return false;
    }
    if (!ValidateTransform(WorldTransformOfRoot, TEXT("world_transform_of_root"), Error))
    {
        OutResult.ErrorMessage = Error;
        return false;
    }

    OutResult.ModelId = Description.ModelId;
    OutResult.ModelRevision = Description.ModelRevision;

    TArray<double> JointValues;
    JointValues.Init(0.0, Description.Joints.Num());
    TArray<uint8> Provided;
    Provided.Init(0, Description.Joints.Num());

    for (const FDttNamedJointPosition& NamedPosition : JointPositions)
    {
        if (NamedPosition.JointName.IsNone())
        {
            OutResult.ErrorMessage = TEXT("joint input name must be non-empty");
            return false;
        }
        const int32 JointIndex = Model.FindJointIndex(NamedPosition.JointName);
        if (JointIndex == INDEX_NONE)
        {
            OutResult.ErrorMessage = FString::Printf(
                TEXT("unknown joint input: %s"),
                *NamedPosition.JointName.ToString());
            return false;
        }
        if (Provided[JointIndex] != 0)
        {
            OutResult.ErrorMessage = FString::Printf(
                TEXT("duplicate joint input: %s"),
                *NamedPosition.JointName.ToString());
            return false;
        }
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            OutResult.ErrorMessage = FString::Printf(
                TEXT("joint input is not allowed for fixed joint: %s"),
                *NamedPosition.JointName.ToString());
            return false;
        }
        if (!FMath::IsFinite(NamedPosition.PositionRadians))
        {
            OutResult.ErrorMessage = FString::Printf(
                TEXT("joint input is non-finite: %s"),
                *NamedPosition.JointName.ToString());
            return false;
        }
        JointValues[JointIndex] = NamedPosition.PositionRadians;
        Provided[JointIndex] = 1;
    }

    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        if (Provided[JointIndex] == 0)
        {
            OutResult.ErrorMessage = FString::Printf(
                TEXT("missing joint input: %s"),
                *Joint.Name.ToString());
            return false;
        }
        if (Joint.bHasPositionLimits
            && (JointValues[JointIndex] < Joint.LowerPositionRadians
                || JointValues[JointIndex] > Joint.UpperPositionRadians))
        {
            OutResult.bWithinJointLimits = false;
            OutResult.Diagnostics.Add(FString::Printf(
                TEXT("joint %s is outside limits [%0.17g, %0.17g] at %0.17g radians"),
                *Joint.Name.ToString(),
                Joint.LowerPositionRadians,
                Joint.UpperPositionRadians,
                JointValues[JointIndex]));
        }
    }

    TArray<FDttCanonicalTransform> WorldTransforms;
    WorldTransforms.SetNum(Description.Links.Num());
    const int32 RootLinkIndex = Model.FindLinkIndex(Description.RootLinkName);
    WorldTransforms[RootLinkIndex] = WorldTransformOfRoot;

    for (const int32 JointIndex : Model.JointTraversalOrder)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        const int32 ParentIndex = Model.FindLinkIndex(Joint.ParentLink);
        const int32 ChildIndex = Model.FindLinkIndex(Joint.ChildLink);
        FDttCanonicalTransform Motion = FDttCanonicalTransform::Identity();
        if (Joint.Type == EDttRobotJointType::Revolute)
        {
            Motion = FDttCanonicalTransform::FromAxisAngle(
                FVector3d(0.0, 0.0, 0.0),
                Joint.AxisJointFrame.ToVector3d(),
                JointValues[JointIndex]);
        }
        WorldTransforms[ChildIndex] =
            WorldTransforms[ParentIndex] * Joint.ParentToJoint * Motion;
    }

    for (const int32 LinkIndex : Model.LinkTraversalOrder)
    {
        FDttNamedCanonicalTransform& NamedTransform = OutResult.LinkTransforms.AddDefaulted_GetRef();
        NamedTransform.Name = Description.Links[LinkIndex].Name;
        NamedTransform.Transform = WorldTransforms[LinkIndex];
    }
    for (const FDttRobotToolFrameDescription& Tool : Description.ToolFrames)
    {
        const int32 LinkIndex = Model.FindLinkIndex(Tool.LinkName);
        FDttNamedCanonicalTransform& NamedTransform = OutResult.ToolTransforms.AddDefaulted_GetRef();
        NamedTransform.Name = Tool.Name;
        NamedTransform.Transform = WorldTransforms[LinkIndex] * Tool.LinkToTool;
    }

    OutResult.bSuccess = true;
    return true;
}

bool ConvertCanonicalToUnrealTransform(
    const FDttCanonicalTransform& CanonicalTransform,
    FTransform& OutUnrealTransform,
    FString& OutError)
{
    OutError.Reset();
    if (!ValidateTransform(CanonicalTransform, TEXT("canonical_transform"), OutError))
    {
        return false;
    }

    const FDttMatrix3 UnrealRotationMatrix = ReflectYBasis(
        CanonicalRotationMatrix(CanonicalTransform.GetRotationQuaternion()));
    FQuat4d UnrealQuaternion;
    if (!MatrixToQuaternion(UnrealRotationMatrix, UnrealQuaternion))
    {
        return Fail(OutError, TEXT("canonical rotation could not be converted to Unreal"));
    }

    const FVector3d CanonicalTranslation = CanonicalTransform.GetTranslationMetres();
    const FVector3d UnrealTranslationCentimetres(
        100.0 * CanonicalTranslation.X,
        -100.0 * CanonicalTranslation.Y,
        100.0 * CanonicalTranslation.Z);
    const FQuat UnrealRotationQuaternion(
        UnrealQuaternion.X,
        UnrealQuaternion.Y,
        UnrealQuaternion.Z,
        UnrealQuaternion.W);
    const FVector UnrealTranslation(
        UnrealTranslationCentimetres.X,
        UnrealTranslationCentimetres.Y,
        UnrealTranslationCentimetres.Z);
    if (!FMath::IsFinite(UnrealRotationQuaternion.X)
        || !FMath::IsFinite(UnrealRotationQuaternion.Y)
        || !FMath::IsFinite(UnrealRotationQuaternion.Z)
        || !FMath::IsFinite(UnrealRotationQuaternion.W)
        || !FMath::IsFinite(UnrealTranslation.X)
        || !FMath::IsFinite(UnrealTranslation.Y)
        || !FMath::IsFinite(UnrealTranslation.Z))
    {
        return Fail(OutError, TEXT("canonical transform exceeds Unreal finite range"));
    }

    OutUnrealTransform = FTransform(UnrealRotationQuaternion, UnrealTranslation);
    return true;
}

bool ConvertUnrealToCanonicalTransform(
    const FTransform& UnrealTransform,
    FDttCanonicalTransform& OutCanonicalTransform,
    FString& OutError)
{
    OutError.Reset();
    const FVector UnrealScale = UnrealTransform.GetScale3D();
    const FVector UnrealTranslation = UnrealTransform.GetLocation();
    const FQuat UnrealQuaternionValue = UnrealTransform.GetRotation();
    if (!FMath::IsFinite(UnrealScale.X)
        || !FMath::IsFinite(UnrealScale.Y)
        || !FMath::IsFinite(UnrealScale.Z)
        || !FMath::IsFinite(UnrealTranslation.X)
        || !FMath::IsFinite(UnrealTranslation.Y)
        || !FMath::IsFinite(UnrealTranslation.Z)
        || !FMath::IsFinite(UnrealQuaternionValue.X)
        || !FMath::IsFinite(UnrealQuaternionValue.Y)
        || !FMath::IsFinite(UnrealQuaternionValue.Z)
        || !FMath::IsFinite(UnrealQuaternionValue.W))
    {
        return Fail(OutError, TEXT("Unreal transform contains non-finite values"));
    }
    if (!FMath::IsNearlyEqual(UnrealScale.X, 1.0F, static_cast<float>(ScaleTolerance))
        || !FMath::IsNearlyEqual(UnrealScale.Y, 1.0F, static_cast<float>(ScaleTolerance))
        || !FMath::IsNearlyEqual(UnrealScale.Z, 1.0F, static_cast<float>(ScaleTolerance)))
    {
        return Fail(OutError, TEXT("Unreal transform must have unit scale"));
    }

    const FQuat4d UnrealQuaternion(
        UnrealQuaternionValue.X,
        UnrealQuaternionValue.Y,
        UnrealQuaternionValue.Z,
        UnrealQuaternionValue.W);
    const double NormSquared =
        UnrealQuaternion.X * UnrealQuaternion.X
        + UnrealQuaternion.Y * UnrealQuaternion.Y
        + UnrealQuaternion.Z * UnrealQuaternion.Z
        + UnrealQuaternion.W * UnrealQuaternion.W;
    if (!IsFiniteQuaternion(UnrealQuaternion) || !FMath::IsFinite(NormSquared)
        || NormSquared <= UE_DOUBLE_SMALL_NUMBER)
    {
        return Fail(OutError, TEXT("Unreal transform rotation must be non-zero and finite"));
    }

    const FDttMatrix3 CanonicalRotation = ReflectYBasis(
        CanonicalRotationMatrix(NormalizeQuaternion(UnrealQuaternion)));
    FQuat4d CanonicalQuaternion;
    if (!MatrixToQuaternion(CanonicalRotation, CanonicalQuaternion))
    {
        return Fail(OutError, TEXT("Unreal rotation could not be converted to canonical"));
    }
    const FVector3d CanonicalTranslation(
        UnrealTranslation.X / 100.0,
        -UnrealTranslation.Y / 100.0,
        UnrealTranslation.Z / 100.0);

    OutCanonicalTransform = FDttCanonicalTransform::FromTranslationRotation(
        CanonicalTranslation,
        CanonicalQuaternion);
    return true;
}
} // namespace DeferredTeleop::Kinematics

bool UDeferredTeleopKinematicsLibrary::ParseRobotDescriptionJson(
    const FString& Json,
    FDttRobotDescription& OutDescription,
    FString& OutError)
{
    return DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Json, OutDescription, OutError);
}

bool UDeferredTeleopKinematicsLibrary::ValidateRobotDescription(
    const FDttRobotDescription& Description,
    FString& OutError)
{
    FDttValidatedRobotModel Model;
    return DeferredTeleop::Kinematics::ValidateRobotDescription(Description, Model, OutError);
}

bool UDeferredTeleopKinematicsLibrary::EvaluateForwardKinematics(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    const TArray<FDttNamedJointPosition>& JointPositions,
    FDttForwardKinematicsResult& OutResult)
{
    return DeferredTeleop::Kinematics::EvaluateForwardKinematics(
        Description,
        WorldTransformOfRoot,
        JointPositions,
        OutResult);
}

bool UDeferredTeleopKinematicsLibrary::ConvertCanonicalToUnrealTransform(
    const FDttCanonicalTransform& CanonicalTransform,
    FTransform& OutUnrealTransform,
    FString& OutError)
{
    return DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
        CanonicalTransform,
        OutUnrealTransform,
        OutError);
}

bool UDeferredTeleopKinematicsLibrary::ConvertUnrealToCanonicalTransform(
    const FTransform& UnrealTransform,
    FDttCanonicalTransform& OutCanonicalTransform,
    FString& OutError)
{
    return DeferredTeleop::Kinematics::ConvertUnrealToCanonicalTransform(
        UnrealTransform,
        OutCanonicalTransform,
        OutError);
}
