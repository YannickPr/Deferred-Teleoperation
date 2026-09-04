#include "Kinematics/DeferredTeleopIKLibrary.h"

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Math/UnrealMathUtility.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Kinematics/DeferredTeleopIKTestBridge.h"
#endif

namespace DeferredTeleop::Kinematics::IKPrivate
{
constexpr double Pi = 3.1415926535897932384626433832795;
constexpr double AxisEpsilon = 1.0e-12;
constexpr double PivotEpsilon = 1.0e-14;
constexpr double MinimumAllowedDamping = 0.001;
constexpr double MaximumAllowedDamping = 1.0;
constexpr int32 MaximumTaskRows = 5;
constexpr int32 MaximumIterations = 64;
constexpr int32 MaximumFKEvaluations = 1024;
constexpr int32 MaximumLineSearchCandidates = 5;
constexpr int32 MaximumDampingTrials = 4;

struct FForwardSnapshot
{
    TArray<FDttCanonicalTransform> LinkTransforms;
    TArray<FVector3d> JointOrigins;
    TArray<FVector3d> JointAxes;
    FDttCanonicalTransform ToolTransform = FDttCanonicalTransform::Identity();
};

struct FTaskEvaluation
{
    FVector3d ToolPosition = FVector3d::ZeroVector;
    FVector3d ToolApproachAxis = FVector3d::ZeroVector;
    FVector3d ApproachError3 = FVector3d::ZeroVector;
    FVector3d ApproachBasisU = FVector3d::ZeroVector;
    FVector3d ApproachBasisV = FVector3d::ZeroVector;
    double PositionResidualMetres = 0.0;
    double ApproachResidualRadians = 0.0;
    double WeightedCost = 0.0;
};

bool Fail(FString& OutError, const FString& Message)
{
    OutError = Message;
    return false;
}

bool IsFiniteVector(const FVector3d& Value)
{
    return FMath::IsFinite(Value.X)
        && FMath::IsFinite(Value.Y)
        && FMath::IsFinite(Value.Z);
}

bool IsFiniteTransform(const FDttCanonicalTransform& Transform)
{
    return Transform.IsRigid();
}

bool NormalizeDirection(const FVector3d& Input, FVector3d& OutDirection)
{
    if (!IsFiniteVector(Input))
    {
        return false;
    }
    const double NormSquared = Input.SizeSquared();
    if (!FMath::IsFinite(NormSquared) || NormSquared <= AxisEpsilon * AxisEpsilon)
    {
        return false;
    }
    const double InverseNorm = 1.0 / FMath::Sqrt(NormSquared);
    OutDirection = Input * InverseNorm;
    return IsFiniteVector(OutDirection);
}

double ClampDot(const double Value)
{
    return FMath::Clamp(Value, -1.0, 1.0);
}

/**
 * Build the two-dimensional logarithmic error for a point on S^2.
 *
 * For a and b that are antiparallel, the logarithm has no unique direction.
 * The least-aligned canonical basis axis makes that choice deterministic.  u
 * and v are returned for both the task error and the finite-difference rows.
 */
bool BuildApproachError(
    const FVector3d& CurrentAxisInput,
    const FVector3d& TargetAxisInput,
    FVector3d& OutBasisU,
    FVector3d& OutBasisV,
    FVector3d& OutError3,
    double& OutAngle)
{
    FVector3d CurrentAxis;
    FVector3d TargetAxis;
    if (!NormalizeDirection(CurrentAxisInput, CurrentAxis)
        || !NormalizeDirection(TargetAxisInput, TargetAxis))
    {
        return false;
    }

    const FVector3d CanonicalAxes[] = {
        FVector3d(1.0, 0.0, 0.0),
        FVector3d(0.0, 1.0, 0.0),
        FVector3d(0.0, 0.0, 1.0),
    };
    int32 LeastAlignedIndex = 0;
    double LeastAligned = FMath::Abs(FVector3d::DotProduct(CurrentAxis, CanonicalAxes[0]));
    for (int32 Index = 1; Index < UE_ARRAY_COUNT(CanonicalAxes); ++Index)
    {
        const double Alignment = FMath::Abs(
            FVector3d::DotProduct(CurrentAxis, CanonicalAxes[Index]));
        // Strict comparison deliberately keeps X, then Y, then Z on ties.
        if (Alignment < LeastAligned)
        {
            LeastAligned = Alignment;
            LeastAlignedIndex = Index;
        }
    }

    const FVector3d BaseAxis = CanonicalAxes[LeastAlignedIndex];
    OutBasisU = BaseAxis
        - FVector3d::DotProduct(BaseAxis, CurrentAxis) * CurrentAxis;
    if (!NormalizeDirection(OutBasisU, OutBasisU))
    {
        return false;
    }
    OutBasisV = FVector3d::CrossProduct(CurrentAxis, OutBasisU);
    if (!NormalizeDirection(OutBasisV, OutBasisV))
    {
        return false;
    }

    const FVector3d Cross = FVector3d::CrossProduct(CurrentAxis, TargetAxis);
    const double SineMagnitude = FMath::Sqrt(FMath::Max(0.0, Cross.SizeSquared()));
    const double Dot = ClampDot(FVector3d::DotProduct(CurrentAxis, TargetAxis));
    OutAngle = FMath::Atan2(SineMagnitude, Dot);
    if (!FMath::IsFinite(OutAngle))
    {
        return false;
    }

    if (OutAngle <= AxisEpsilon)
    {
        OutError3 = FVector3d::ZeroVector;
        OutAngle = 0.0;
        return true;
    }

    if (SineMagnitude > AxisEpsilon)
    {
        // e3 = (theta / sin(theta)) * (b - dot(a,b) * a).
        OutError3 = (OutAngle / SineMagnitude) * (TargetAxis - Dot * CurrentAxis);
    }
    else if (Dot < 0.0)
    {
        // Exact antiparallel input has b - dot(a,b) a == 0.  Its deterministic
        // limit is pi times the canonical tangent basis selected above.
        OutError3 = Pi * OutBasisU;
        OutAngle = Pi;
    }
    else
    {
        OutError3 = FVector3d::ZeroVector;
        OutAngle = 0.0;
    }
    return IsFiniteVector(OutError3);
}

int32 FindToolTransformIndex(
    const TArray<FDttNamedCanonicalTransform>& NamedTransforms,
    FName ToolName)
{
    for (int32 Index = 0; Index < NamedTransforms.Num(); ++Index)
    {
        if (NamedTransforms[Index].Name == ToolName)
        {
            return Index;
        }
    }
    return INDEX_NONE;
}

bool EvaluateState(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttValidatedRobotModel& Model,
    const TArray<double>& JointValues,
    int32 MaxFKEvaluations,
    int32& InOutFKEvaluations,
    FForwardSnapshot& OutSnapshot,
    FString& OutError)
{
    OutSnapshot = FForwardSnapshot();
    if (InOutFKEvaluations >= MaxFKEvaluations)
    {
        return Fail(OutError, TEXT("maximum FK evaluation budget reached"));
    }

    TArray<FDttNamedJointPosition> NamedState;
    NamedState.Reserve(Description.Joints.Num());
    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        FDttNamedJointPosition& NamedPosition = NamedState.AddDefaulted_GetRef();
        NamedPosition.JointName = Joint.Name;
        NamedPosition.PositionRadians = JointValues[JointIndex];
    }

    // The counter is incremented before calling FK.  Thus every attempted
    // initial, central-difference, or line-search FK is visible even if FK
    // itself reports a malformed result.
    ++InOutFKEvaluations;
    FDttForwardKinematicsResult FKResult;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            Request.WorldTransformOfRoot,
            NamedState,
            FKResult))
    {
        return Fail(OutError, FString::Printf(TEXT("FK evaluation failed: %s"), *FKResult.ErrorMessage));
    }

    OutSnapshot.LinkTransforms.Init(
        FDttCanonicalTransform::Identity(),
        Description.Links.Num());
    TArray<uint8> SeenLinks;
    SeenLinks.Init(0, Description.Links.Num());
    for (const FDttNamedCanonicalTransform& NamedTransform : FKResult.LinkTransforms)
    {
        const int32 LinkIndex = Model.FindLinkIndex(NamedTransform.Name);
        if (LinkIndex == INDEX_NONE)
        {
            return Fail(OutError, TEXT("FK returned an unknown link transform"));
        }
        OutSnapshot.LinkTransforms[LinkIndex] = NamedTransform.Transform;
        SeenLinks[LinkIndex] = 1;
    }
    for (int32 LinkIndex = 0; LinkIndex < SeenLinks.Num(); ++LinkIndex)
    {
        if (SeenLinks[LinkIndex] == 0 || !IsFiniteTransform(OutSnapshot.LinkTransforms[LinkIndex]))
        {
            return Fail(OutError, TEXT("FK returned an incomplete or non-finite link transform"));
        }
    }

    const int32 ToolTransformIndex = FindToolTransformIndex(
        FKResult.ToolTransforms,
        Request.ToolFrameName);
    if (ToolTransformIndex == INDEX_NONE)
    {
        return Fail(OutError, TEXT("FK did not return the requested tool frame"));
    }
    OutSnapshot.ToolTransform = FKResult.ToolTransforms[ToolTransformIndex].Transform;
    if (!IsFiniteTransform(OutSnapshot.ToolTransform))
    {
        return Fail(OutError, TEXT("FK returned a non-finite tool transform"));
    }

    OutSnapshot.JointOrigins.Init(FVector3d::ZeroVector, Description.Joints.Num());
    OutSnapshot.JointAxes.Init(FVector3d::ZeroVector, Description.Joints.Num());
    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const int32 ParentIndex = Model.FindLinkIndex(Joint.ParentLink);
        const FDttCanonicalTransform JointTransform =
            OutSnapshot.LinkTransforms[ParentIndex] * Joint.ParentToJoint;
        FQuat4d JointRotation = JointTransform.GetRotationQuaternion();
        JointRotation.Normalize();
        const FVector3d Axis = JointRotation.RotateVector(Joint.AxisJointFrame.ToVector3d());
        if (!NormalizeDirection(Axis, OutSnapshot.JointAxes[JointIndex]))
        {
            return Fail(OutError, TEXT("FK produced a non-finite joint axis"));
        }
        OutSnapshot.JointOrigins[JointIndex] = JointTransform.GetTranslationMetres();
        if (!IsFiniteVector(OutSnapshot.JointOrigins[JointIndex]))
        {
            return Fail(OutError, TEXT("FK produced a non-finite joint origin"));
        }
    }
    return true;
}

bool EvaluateTask(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FForwardSnapshot& Snapshot,
    const FVector3d& TargetApproachAxis,
    FTaskEvaluation& OutEvaluation)
{
    OutEvaluation = FTaskEvaluation();
    OutEvaluation.ToolPosition = Snapshot.ToolTransform.GetTranslationMetres();
    if (!IsFiniteVector(OutEvaluation.ToolPosition))
    {
        return false;
    }

    const FVector3d PositionError = Request.TargetPositionMetres.ToVector3d()
        - OutEvaluation.ToolPosition;
    OutEvaluation.PositionResidualMetres = FMath::Sqrt(
        FMath::Max(0.0, PositionError.SizeSquared()));
    if (!FMath::IsFinite(OutEvaluation.PositionResidualMetres))
    {
        return false;
    }
    OutEvaluation.WeightedCost = Settings.PositionWeight * PositionError.SizeSquared();

    if (Request.Mode == EDttIKMode::PositionPlusApproachAxis)
    {
        FQuat4d ToolRotation = Snapshot.ToolTransform.GetRotationQuaternion();
        ToolRotation.Normalize();
        if (!NormalizeDirection(
                ToolRotation.RotateVector(Request.LocalToolApproachAxis.ToVector3d()),
                OutEvaluation.ToolApproachAxis))
        {
            return false;
        }
        if (!BuildApproachError(
                OutEvaluation.ToolApproachAxis,
                TargetApproachAxis,
                OutEvaluation.ApproachBasisU,
                OutEvaluation.ApproachBasisV,
                OutEvaluation.ApproachError3,
                OutEvaluation.ApproachResidualRadians))
        {
            return false;
        }
        OutEvaluation.WeightedCost += Settings.OrientationWeight
            * OutEvaluation.ApproachResidualRadians
            * OutEvaluation.ApproachResidualRadians;
    }
    else
    {
        OutEvaluation.ApproachResidualRadians = 0.0;
    }
    return FMath::IsFinite(OutEvaluation.WeightedCost);
}

bool IsConverged(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FTaskEvaluation& Evaluation)
{
    const bool bPositionConverged =
        Evaluation.PositionResidualMetres <= Settings.PositionToleranceMetres;
    const bool bApproachConverged = Request.Mode == EDttIKMode::PositionOnly
        || Evaluation.ApproachResidualRadians <= Settings.ApproachToleranceRadians;
    return bPositionConverged && bApproachConverged;
}

struct FLineSearchCandidateSelection
{
    TArray<double> JointValues;
    FForwardSnapshot Snapshot;
    FTaskEvaluation Evaluation;
    bool bHaveCandidate = false;
    bool bConvergedCandidate = false;
};

/**
 * Keep the least-cost candidate, except that satisfying both task tolerances
 * is a stronger acceptance condition than weighted-cost ordering.  The
 * converged state is copied here so the outer loop cannot later retain a
 * different, infeasible candidate merely because it is cheaper.
 */
void ConsiderLineSearchCandidate(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const TArray<double>& CandidateValues,
    const FForwardSnapshot& CandidateSnapshot,
    const FTaskEvaluation& CandidateEvaluation,
    FLineSearchCandidateSelection& InOutSelection)
{
    if (IsConverged(Request, Settings, CandidateEvaluation))
    {
        InOutSelection.JointValues = CandidateValues;
        InOutSelection.Snapshot = CandidateSnapshot;
        InOutSelection.Evaluation = CandidateEvaluation;
        InOutSelection.bHaveCandidate = true;
        InOutSelection.bConvergedCandidate = true;
        return;
    }

    if (!InOutSelection.bHaveCandidate
        || CandidateEvaluation.WeightedCost < InOutSelection.Evaluation.WeightedCost)
    {
        InOutSelection.JointValues = CandidateValues;
        InOutSelection.Snapshot = CandidateSnapshot;
        InOutSelection.Evaluation = CandidateEvaluation;
        InOutSelection.bHaveCandidate = true;
    }
}

bool AcceptLineSearchCandidate(
    const FDttIKSettings& Settings,
    const FTaskEvaluation& CurrentEvaluation,
    FLineSearchCandidateSelection& InOutSelection,
    TArray<double>& OutJointValues,
    FForwardSnapshot& OutSnapshot,
    FTaskEvaluation& OutEvaluation)
{
    if (InOutSelection.bConvergedCandidate)
    {
        OutJointValues = MoveTemp(InOutSelection.JointValues);
        OutSnapshot = MoveTemp(InOutSelection.Snapshot);
        OutEvaluation = InOutSelection.Evaluation;
        return true;
    }
    if (InOutSelection.bHaveCandidate
        && InOutSelection.Evaluation.WeightedCost
            < CurrentEvaluation.WeightedCost - Settings.StagnationCostTolerance)
    {
        OutJointValues = MoveTemp(InOutSelection.JointValues);
        OutSnapshot = MoveTemp(InOutSelection.Snapshot);
        OutEvaluation = InOutSelection.Evaluation;
        return true;
    }
    return false;
}

bool SolveLinearSystem(
    const TArray<TArray<double>>& Matrix,
    const TArray<double>& RightHandSide,
    TArray<double>& OutSolution)
{
    const int32 Size = RightHandSide.Num();
    if (Size <= 0 || Size > MaximumTaskRows || Matrix.Num() != Size)
    {
        return false;
    }

    double A[MaximumTaskRows][MaximumTaskRows] = {};
    double B[MaximumTaskRows] = {};
    for (int32 Row = 0; Row < Size; ++Row)
    {
        if (Matrix[Row].Num() != Size || !FMath::IsFinite(RightHandSide[Row]))
        {
            return false;
        }
        B[Row] = RightHandSide[Row];
        for (int32 Column = 0; Column < Size; ++Column)
        {
            if (!FMath::IsFinite(Matrix[Row][Column]))
            {
                return false;
            }
            A[Row][Column] = Matrix[Row][Column];
        }
    }

    for (int32 Column = 0; Column < Size; ++Column)
    {
        int32 PivotRow = Column;
        double PivotMagnitude = FMath::Abs(A[Column][Column]);
        for (int32 Row = Column + 1; Row < Size; ++Row)
        {
            const double Magnitude = FMath::Abs(A[Row][Column]);
            if (Magnitude > PivotMagnitude)
            {
                PivotMagnitude = Magnitude;
                PivotRow = Row;
            }
        }
        if (!FMath::IsFinite(PivotMagnitude) || PivotMagnitude <= PivotEpsilon)
        {
            return false;
        }
        if (PivotRow != Column)
        {
            for (int32 Index = Column; Index < Size; ++Index)
            {
                Swap(A[Column][Index], A[PivotRow][Index]);
            }
            Swap(B[Column], B[PivotRow]);
        }

        for (int32 Row = Column + 1; Row < Size; ++Row)
        {
            const double Factor = A[Row][Column] / A[Column][Column];
            if (!FMath::IsFinite(Factor))
            {
                return false;
            }
            A[Row][Column] = 0.0;
            for (int32 Index = Column + 1; Index < Size; ++Index)
            {
                A[Row][Index] -= Factor * A[Column][Index];
            }
            B[Row] -= Factor * B[Column];
        }
    }

    OutSolution.Init(0.0, Size);
    for (int32 Row = Size - 1; Row >= 0; --Row)
    {
        double Sum = B[Row];
        for (int32 Column = Row + 1; Column < Size; ++Column)
        {
            Sum -= A[Row][Column] * OutSolution[Column];
        }
        if (!FMath::IsFinite(Sum)
            || !FMath::IsFinite(A[Row][Row])
            || FMath::Abs(A[Row][Row]) <= PivotEpsilon)
        {
            return false;
        }
        OutSolution[Row] = Sum / A[Row][Row];
        if (!FMath::IsFinite(OutSolution[Row]))
        {
            return false;
        }
    }
    return true;
}

bool BuildDampedLeastSquaresStep(
    const TArray<double>& TaskError,
    const TArray<TArray<double>>& Jacobian,
    double Lambda,
    double MaxJointStepRadians,
    TArray<double>& OutStep)
{
    const int32 Rows = TaskError.Num();
    const int32 Columns = Jacobian.Num();
    if (Rows <= 0 || Rows > MaximumTaskRows || Columns <= 0 || Rows != Jacobian[0].Num())
    {
        return false;
    }
    if (!FMath::IsFinite(Lambda) || Lambda <= 0.0)
    {
        return false;
    }

    TArray<TArray<double>> NormalMatrix;
    NormalMatrix.SetNum(Rows);
    for (int32 Row = 0; Row < Rows; ++Row)
    {
        NormalMatrix[Row].Init(0.0, Rows);
    }
    for (int32 Row = 0; Row < Rows; ++Row)
    {
        for (int32 Column = 0; Column < Rows; ++Column)
        {
            double Value = 0.0;
            for (int32 Joint = 0; Joint < Columns; ++Joint)
            {
                Value += Jacobian[Joint][Row] * Jacobian[Joint][Column];
            }
            if (Row == Column)
            {
                Value += Lambda * Lambda;
            }
            NormalMatrix[Row][Column] = Value;
        }
    }

    TArray<double> RowSolution;
    if (!SolveLinearSystem(NormalMatrix, TaskError, RowSolution))
    {
        return false;
    }

    OutStep.Init(0.0, Columns);
    double MaximumAbsStep = 0.0;
    for (int32 Joint = 0; Joint < Columns; ++Joint)
    {
        double Value = 0.0;
        for (int32 Row = 0; Row < Rows; ++Row)
        {
            Value += Jacobian[Joint][Row] * RowSolution[Row];
        }
        if (!FMath::IsFinite(Value))
        {
            return false;
        }
        OutStep[Joint] = Value;
        MaximumAbsStep = FMath::Max(MaximumAbsStep, FMath::Abs(Value));
    }
    if (MaximumAbsStep > MaxJointStepRadians)
    {
        const double Scale = MaxJointStepRadians / MaximumAbsStep;
        for (double& Value : OutStep)
        {
            Value *= Scale;
        }
    }
    return true;
}

bool BuildTaskErrorAndJacobian(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FTaskEvaluation& CurrentEvaluation,
    const TArray<FForwardSnapshot>& PlusSnapshots,
    const TArray<FForwardSnapshot>& MinusSnapshots,
    const TArray<int32>& ActiveJointIndices,
    TArray<double>& OutTaskError,
    TArray<TArray<double>>& OutJacobian)
{
    const bool bApproach = Request.Mode == EDttIKMode::PositionPlusApproachAxis;
    const int32 Rows = bApproach ? 5 : 3;
    const double PositionScale = FMath::Sqrt(Settings.PositionWeight);
    const double OrientationScale = FMath::Sqrt(Settings.OrientationWeight);
    OutTaskError.Init(0.0, Rows);
    OutJacobian.SetNum(ActiveJointIndices.Num());
    const FVector3d PositionError = Request.TargetPositionMetres.ToVector3d()
        - CurrentEvaluation.ToolPosition;
    OutTaskError[0] = PositionScale * PositionError.X;
    OutTaskError[1] = PositionScale * PositionError.Y;
    OutTaskError[2] = PositionScale * PositionError.Z;

    if (bApproach)
    {
        OutTaskError[3] = OrientationScale * FVector3d::DotProduct(
            CurrentEvaluation.ApproachError3,
            CurrentEvaluation.ApproachBasisU);
        OutTaskError[4] = OrientationScale * FVector3d::DotProduct(
            CurrentEvaluation.ApproachError3,
            CurrentEvaluation.ApproachBasisV);
    }

    const double DifferenceScale = 0.5 / Settings.CentralDifferenceStepRadians;
    for (int32 ActiveColumn = 0; ActiveColumn < ActiveJointIndices.Num(); ++ActiveColumn)
    {
        const int32 JointIndex = ActiveJointIndices[ActiveColumn];
        if (PlusSnapshots.Num() != ActiveJointIndices.Num()
            || MinusSnapshots.Num() != ActiveJointIndices.Num())
        {
            return false;
        }
        const FForwardSnapshot& PlusSnapshot = PlusSnapshots[ActiveColumn];
        const FForwardSnapshot& MinusSnapshot = MinusSnapshots[ActiveColumn];
        OutJacobian[ActiveColumn].Init(0.0, Rows);
        const FVector3d PositionDerivative = DifferenceScale * (
            PlusSnapshot.ToolTransform.GetTranslationMetres()
            - MinusSnapshot.ToolTransform.GetTranslationMetres());
        OutJacobian[ActiveColumn][0] = PositionScale * PositionDerivative.X;
        OutJacobian[ActiveColumn][1] = PositionScale * PositionDerivative.Y;
        OutJacobian[ActiveColumn][2] = PositionScale * PositionDerivative.Z;
        if (bApproach)
        {
            FQuat4d PlusRotation = PlusSnapshot.ToolTransform.GetRotationQuaternion();
            FQuat4d MinusRotation = MinusSnapshot.ToolTransform.GetRotationQuaternion();
            PlusRotation.Normalize();
            MinusRotation.Normalize();
            FVector3d PlusAxis;
            FVector3d MinusAxis;
            if (!NormalizeDirection(
                    PlusRotation.RotateVector(Request.LocalToolApproachAxis.ToVector3d()),
                    PlusAxis)
                || !NormalizeDirection(
                    MinusRotation.RotateVector(Request.LocalToolApproachAxis.ToVector3d()),
                    MinusAxis))
            {
                return false;
            }
            const FVector3d AxisDerivative = DifferenceScale * (PlusAxis - MinusAxis);
            OutJacobian[ActiveColumn][3] = OrientationScale * FVector3d::DotProduct(
                AxisDerivative,
                CurrentEvaluation.ApproachBasisU);
            OutJacobian[ActiveColumn][4] = OrientationScale * FVector3d::DotProduct(
                AxisDerivative,
                CurrentEvaluation.ApproachBasisV);
        }
    }
    for (const TArray<double>& Column : OutJacobian)
    {
        for (const double Value : Column)
        {
            if (!FMath::IsFinite(Value))
            {
                return false;
            }
        }
    }
    for (const double Value : OutTaskError)
    {
        if (!FMath::IsFinite(Value))
        {
            return false;
        }
    }
    return true;
}

bool ValidateSettings(const FDttIKSettings& Settings, FString& OutError)
{
    if (!FMath::IsFinite(Settings.PositionWeight) || Settings.PositionWeight <= 0.0
        || !FMath::IsFinite(Settings.OrientationWeight) || Settings.OrientationWeight <= 0.0)
    {
        return Fail(OutError, TEXT("IK task weights must be finite and positive"));
    }
    if (!FMath::IsFinite(Settings.DampingLambda)
        || !FMath::IsFinite(Settings.MinimumDampingLambda)
        || !FMath::IsFinite(Settings.MaximumDampingLambda)
        || Settings.MinimumDampingLambda < MinimumAllowedDamping
        || Settings.MaximumDampingLambda > MaximumAllowedDamping
        || Settings.MinimumDampingLambda > Settings.MaximumDampingLambda)
    {
        return Fail(OutError, TEXT("IK damping must be finite and bounded to [0.001, 1]"));
    }
    if (!FMath::IsFinite(Settings.MaxJointStepRadians) || Settings.MaxJointStepRadians <= 0.0
        || !FMath::IsFinite(Settings.PositionToleranceMetres)
        || Settings.PositionToleranceMetres <= 0.0
        || !FMath::IsFinite(Settings.ApproachToleranceRadians)
        || Settings.ApproachToleranceRadians <= 0.0
        || !FMath::IsFinite(Settings.CentralDifferenceStepRadians)
        || Settings.CentralDifferenceStepRadians <= 0.0)
    {
        return Fail(OutError, TEXT("IK tolerances, central step, and maximum step must be positive finite values"));
    }
    if (Settings.MaxIterations < 0
        || Settings.MaxIterations > MaximumIterations
        || Settings.MaxFKEvaluations <= 0
        || Settings.MaxFKEvaluations > MaximumFKEvaluations
        || Settings.MaxLineSearchCandidates < 1
        || Settings.MaxLineSearchCandidates > MaximumLineSearchCandidates
        || Settings.MaxDampingTrials < 1
        || Settings.MaxDampingTrials > MaximumDampingTrials
        || !FMath::IsFinite(Settings.StagnationCostTolerance)
        || Settings.StagnationCostTolerance < 0.0)
    {
        return Fail(OutError, TEXT("IK iteration and evaluation budgets are outside their finite bounds"));
    }
    return true;
}

bool IsActiveLimitValue(
    const FDttRobotJointDescription& Joint,
    double Value,
    bool& OutAtLower,
    bool& OutAtUpper)
{
    OutAtLower = false;
    OutAtUpper = false;
    if (!Joint.bHasPositionLimits)
    {
        return false;
    }
    const double Tolerance = 1.0e-10;
    OutAtLower = Value <= Joint.LowerPositionRadians + Tolerance;
    OutAtUpper = Value >= Joint.UpperPositionRadians - Tolerance;
    return OutAtLower || OutAtUpper;
}

void FillResult(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    EDttIKStatus Status,
    const TArray<double>& JointValues,
    const TArray<int32>& ActiveJointIndices,
    const FForwardSnapshot* FinalSnapshot,
    const FTaskEvaluation* FinalEvaluation,
    int32 Iterations,
    int32 FKEvaluations,
    const FString& Diagnostic,
    FDttIKResult& OutResult)
{
    OutResult.Status = Status;
    OutResult.bSuccess = Status == EDttIKStatus::Converged;
    OutResult.ModelId = Description.ModelId;
    OutResult.ModelRevision = Description.ModelRevision;
    OutResult.Iterations = Iterations;
    OutResult.FKEvaluations = FKEvaluations;
    OutResult.Diagnostic = Diagnostic;
    if (!Diagnostic.IsEmpty())
    {
        OutResult.Diagnostics.Add(Diagnostic);
    }

    OutResult.JointPositions.Reset();
    for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
    {
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        FDttNamedJointPosition& Position = OutResult.JointPositions.AddDefaulted_GetRef();
        Position.JointName = Description.Joints[JointIndex].Name;
        Position.PositionRadians = JointValues[JointIndex];
    }

    OutResult.ActiveJointNames.Reset();
    OutResult.ActiveLimits.Reset();
    OutResult.ActiveJointLimits.Reset();
    for (const int32 JointIndex : ActiveJointIndices)
    {
        OutResult.ActiveJointNames.Add(Description.Joints[JointIndex].Name);
        bool bAtLower = false;
        bool bAtUpper = false;
        if (IsActiveLimitValue(
                Description.Joints[JointIndex],
                JointValues[JointIndex],
                bAtLower,
                bAtUpper))
        {
            FDttIKActiveLimit& ActiveLimit = OutResult.ActiveLimits.AddDefaulted_GetRef();
            ActiveLimit.JointName = Description.Joints[JointIndex].Name;
            ActiveLimit.bAtLowerLimit = bAtLower;
            ActiveLimit.bAtUpperLimit = bAtUpper;
            OutResult.ActiveJointLimits.Add(ActiveLimit.JointName);
        }
    }

    if (FinalEvaluation != nullptr)
    {
        OutResult.PositionResidualMetres = FinalEvaluation->PositionResidualMetres;
        OutResult.ApproachResidualRadians = FinalEvaluation->ApproachResidualRadians;
    }
    if (FinalSnapshot != nullptr)
    {
        OutResult.AchievedToolTransform = FinalSnapshot->ToolTransform;
    }
    else
    {
        OutResult.AchievedToolTransform = FDttCanonicalTransform::Identity();
    }
    (void)Request;
}

} // namespace DeferredTeleop::Kinematics::IKPrivate

#if WITH_DEV_AUTOMATION_TESTS
namespace DeferredTeleop::Kinematics::IKTestBridge
{
bool BuildTaskJacobianForTest(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FDttCanonicalTransform& CurrentToolTransform,
    const TArray<FDttCanonicalTransform>& PlusToolTransforms,
    const TArray<FDttCanonicalTransform>& MinusToolTransforms,
    FDeferredTeleopIKTestJacobian& OutResult,
    FString& OutError)
{
    OutResult = FDeferredTeleopIKTestJacobian();
    OutError.Reset();

    if (!CurrentToolTransform.IsRigid())
    {
        OutError = TEXT("test Jacobian bridge received a non-rigid current tool transform");
        return false;
    }
    if (PlusToolTransforms.Num() != MinusToolTransforms.Num())
    {
        OutError = TEXT("test Jacobian bridge plus/minus transform counts differ");
        return false;
    }
    for (const FDttCanonicalTransform& Transform : PlusToolTransforms)
    {
        if (!Transform.IsRigid())
        {
            OutError = TEXT("test Jacobian bridge received a non-rigid plus transform");
            return false;
        }
    }
    for (const FDttCanonicalTransform& Transform : MinusToolTransforms)
    {
        if (!Transform.IsRigid())
        {
            OutError = TEXT("test Jacobian bridge received a non-rigid minus transform");
            return false;
        }
    }

    FDttValidatedRobotModel Model;
    if (!ValidateRobotDescription(Description, Model, OutError))
    {
        return false;
    }

    const FDttRobotJointGroupDescription* Group = nullptr;
    for (const FDttRobotJointGroupDescription& Candidate : Description.JointGroups)
    {
        if (Candidate.Name == Request.JointGroupName)
        {
            Group = &Candidate;
            break;
        }
    }
    if (Group == nullptr)
    {
        OutError = FString::Printf(
            TEXT("test Jacobian bridge received an unknown joint group: %s"),
            *Request.JointGroupName.ToString());
        return false;
    }
    if (Model.FindToolIndex(Request.ToolFrameName) == INDEX_NONE)
    {
        OutError = FString::Printf(
            TEXT("test Jacobian bridge received an unknown tool frame: %s"),
            *Request.ToolFrameName.ToString());
        return false;
    }

    TArray<int32> ActiveJointIndices;
    ActiveJointIndices.Reserve(Group->JointNames.Num());
    TSet<int32> SeenJointIndices;
    for (const FName JointName : Group->JointNames)
    {
        const int32 JointIndex = Model.FindJointIndex(JointName);
        if (JointIndex == INDEX_NONE)
        {
            OutError = FString::Printf(
                TEXT("test Jacobian bridge group references unknown joint: %s"),
                *JointName.ToString());
            return false;
        }
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            OutError = FString::Printf(
                TEXT("test Jacobian bridge group references a fixed joint: %s"),
                *JointName.ToString());
            return false;
        }
        if (SeenJointIndices.Contains(JointIndex))
        {
            OutError = FString::Printf(
                TEXT("test Jacobian bridge group contains a duplicate joint: %s"),
                *JointName.ToString());
            return false;
        }
        SeenJointIndices.Add(JointIndex);
        ActiveJointIndices.Add(JointIndex);
        OutResult.ActiveJointNames.Add(JointName);
    }
    if (ActiveJointIndices.Num() == 0
        || PlusToolTransforms.Num() != ActiveJointIndices.Num())
    {
        OutError = TEXT("test Jacobian bridge transform count does not match the active group");
        return false;
    }

    if (!Request.TargetPositionMetres.IsFinite())
    {
        OutError = TEXT("test Jacobian bridge received a non-finite target position");
        return false;
    }

    FVector3d TargetApproachAxis = FVector3d::ZeroVector;
    if (Request.Mode == EDttIKMode::PositionPlusApproachAxis)
    {
        if (!IKPrivate::NormalizeDirection(
                Request.TargetApproachDirectionCanonical.ToVector3d(),
                TargetApproachAxis))
        {
            OutError = TEXT("test Jacobian bridge received an invalid target approach axis");
            return false;
        }
    }
    else if (Request.Mode != EDttIKMode::PositionOnly)
    {
        OutError = TEXT("test Jacobian bridge received an unsupported task mode");
        return false;
    }

    IKPrivate::FForwardSnapshot CurrentSnapshot;
    CurrentSnapshot.ToolTransform = CurrentToolTransform;
    TArray<IKPrivate::FForwardSnapshot> PlusSnapshots;
    TArray<IKPrivate::FForwardSnapshot> MinusSnapshots;
    PlusSnapshots.SetNum(ActiveJointIndices.Num());
    MinusSnapshots.SetNum(ActiveJointIndices.Num());
    for (int32 Column = 0; Column < ActiveJointIndices.Num(); ++Column)
    {
        PlusSnapshots[Column].ToolTransform = PlusToolTransforms[Column];
        MinusSnapshots[Column].ToolTransform = MinusToolTransforms[Column];
    }

    IKPrivate::FTaskEvaluation CurrentEvaluation;
    if (!IKPrivate::EvaluateTask(
            Request,
            Settings,
            CurrentSnapshot,
            TargetApproachAxis,
            CurrentEvaluation))
    {
        OutError = TEXT("test Jacobian bridge task evaluation was non-finite");
        return false;
    }
    if (!IKPrivate::BuildTaskErrorAndJacobian(
            Request,
            Settings,
            CurrentEvaluation,
            PlusSnapshots,
            MinusSnapshots,
            ActiveJointIndices,
            OutResult.TaskError,
            OutResult.Jacobian))
    {
        OutError = TEXT("production task Jacobian helper rejected the test snapshots");
        return false;
    }

    OutResult.CurrentApproachAxis = CurrentEvaluation.ToolApproachAxis;
    OutResult.TargetApproachAxis = TargetApproachAxis;
    OutResult.ApproachBasisU = CurrentEvaluation.ApproachBasisU;
    OutResult.ApproachBasisV = CurrentEvaluation.ApproachBasisV;
    OutResult.PositionResidualMetres = CurrentEvaluation.PositionResidualMetres;
    OutResult.ApproachResidualRadians = CurrentEvaluation.ApproachResidualRadians;
    return true;
}

bool EvaluateCandidateSelectionPolicyForTest(
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    const FDeferredTeleopIKTestCandidate& ExistingCandidate,
    const FDeferredTeleopIKTestCandidate& Candidate,
    FDeferredTeleopIKTestCandidateSelection& OutResult,
    FString& OutError)
{
    OutResult = FDeferredTeleopIKTestCandidateSelection();
    OutError.Reset();

    const auto MakeEvaluation = [&OutError](
        const FDeferredTeleopIKTestCandidate& Input,
        IKPrivate::FTaskEvaluation& OutEvaluation) -> bool
    {
        if (Input.JointValues.Num() == 0
            || !FMath::IsFinite(Input.PositionResidualMetres)
            || !FMath::IsFinite(Input.ApproachResidualRadians)
            || !FMath::IsFinite(Input.WeightedCost))
        {
            OutError = TEXT("test candidate policy received non-finite or empty input");
            return false;
        }
        for (const double JointValue : Input.JointValues)
        {
            if (!FMath::IsFinite(JointValue))
            {
                OutError = TEXT("test candidate policy received a non-finite joint value");
                return false;
            }
        }
        OutEvaluation = IKPrivate::FTaskEvaluation();
        OutEvaluation.PositionResidualMetres = Input.PositionResidualMetres;
        OutEvaluation.ApproachResidualRadians = Input.ApproachResidualRadians;
        OutEvaluation.WeightedCost = Input.WeightedCost;
        return true;
    };

    IKPrivate::FTaskEvaluation ExistingEvaluation;
    IKPrivate::FTaskEvaluation CandidateEvaluation;
    if (!MakeEvaluation(ExistingCandidate, ExistingEvaluation)
        || !MakeEvaluation(Candidate, CandidateEvaluation))
    {
        return false;
    }

    IKPrivate::FLineSearchCandidateSelection Selection;
    Selection.JointValues = ExistingCandidate.JointValues;
    Selection.Evaluation = ExistingEvaluation;
    Selection.bHaveCandidate = true;
    IKPrivate::ConsiderLineSearchCandidate(
        Request,
        Settings,
        Candidate.JointValues,
        IKPrivate::FForwardSnapshot(),
        CandidateEvaluation,
        Selection);

    TArray<double> AcceptedJointValues;
    IKPrivate::FForwardSnapshot AcceptedSnapshot;
    IKPrivate::FTaskEvaluation AcceptedEvaluation;
    const bool bAccepted = IKPrivate::AcceptLineSearchCandidate(
        Settings,
        ExistingEvaluation,
        Selection,
        AcceptedJointValues,
        AcceptedSnapshot,
        AcceptedEvaluation);

    OutResult.SelectedJointValues = bAccepted
        ? MoveTemp(AcceptedJointValues)
        : Selection.JointValues;
    OutResult.bHasCandidate = Selection.bHaveCandidate;
    OutResult.bConvergedCandidate = Selection.bConvergedCandidate;
    OutResult.bAcceptedCandidate = bAccepted;
    const IKPrivate::FTaskEvaluation& SelectedEvaluation = bAccepted
        ? AcceptedEvaluation
        : Selection.Evaluation;
    OutResult.SelectedPositionResidualMetres = SelectedEvaluation.PositionResidualMetres;
    OutResult.SelectedApproachResidualRadians = SelectedEvaluation.ApproachResidualRadians;
    OutResult.SelectedWeightedCost = SelectedEvaluation.WeightedCost;
    return true;
}
} // namespace DeferredTeleop::Kinematics::IKTestBridge
#endif // WITH_DEV_AUTOMATION_TESTS

namespace DeferredTeleop::Kinematics
{
bool SolveInverseKinematics(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    FDttIKResult& OutResult)
{
    // Keep implementation helpers local even when Unreal combines .cpp files.
    using namespace IKPrivate;

    OutResult = FDttIKResult();
    OutResult.ModelId = Description.ModelId;
    OutResult.ModelRevision = Description.ModelRevision;
    OutResult.ToolFrameName = Request.ToolFrameName;
    FString Error;
    if (!ValidateSettings(Settings, Error))
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    FDttValidatedRobotModel Model;
    if (!ValidateRobotDescription(Description, Model, Error))
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }
    if (Request.JointGroupName.IsNone())
    {
        Error = TEXT("IK joint group name is required");
    }
    else if (Request.ToolFrameName.IsNone())
    {
        Error = TEXT("IK tool frame name is required");
    }
    else if (!Request.TargetPositionMetres.IsFinite())
    {
        Error = TEXT("IK target position must contain only finite values");
    }
    else if (!IsFiniteTransform(Request.WorldTransformOfRoot))
    {
        Error = TEXT("IK world transform of root must be a finite rigid transform");
    }
    if (!Error.IsEmpty())
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    const FDttRobotJointGroupDescription* Group = nullptr;
    for (const FDttRobotJointGroupDescription& Candidate : Description.JointGroups)
    {
        if (Candidate.Name == Request.JointGroupName)
        {
            Group = &Candidate;
            break;
        }
    }
    if (Group == nullptr)
    {
        Error = FString::Printf(
            TEXT("unknown IK joint group: %s"),
            *Request.JointGroupName.ToString());
    }
    if (Error.IsEmpty() && Model.FindToolIndex(Request.ToolFrameName) == INDEX_NONE)
    {
        Error = FString::Printf(
            TEXT("unknown IK tool frame: %s"),
            *Request.ToolFrameName.ToString());
    }
    if (!Error.IsEmpty())
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    FVector3d TargetApproachAxis = FVector3d::ZeroVector;
    if (Request.Mode == EDttIKMode::PositionPlusApproachAxis)
    {
        if (!NormalizeDirection(
                Request.TargetApproachDirectionCanonical.ToVector3d(),
                TargetApproachAxis))
        {
            Error = TEXT("IK target approach direction must be finite and non-zero");
        }
        FVector3d LocalApproachAxis;
        if (Error.IsEmpty()
            && !NormalizeDirection(
                   Request.LocalToolApproachAxis.ToVector3d(),
                   LocalApproachAxis))
        {
            Error = TEXT("IK local tool approach axis must be finite and non-zero");
        }
        (void)LocalApproachAxis;
    }
    else if (Request.Mode != EDttIKMode::PositionOnly)
    {
        Error = TEXT("unsupported IK task mode");
    }
    if (!Error.IsEmpty())
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    TArray<int32> ActiveJointIndices;
    ActiveJointIndices.Reserve(Group->JointNames.Num());
    TSet<int32> ActiveSet;
    for (const FName JointName : Group->JointNames)
    {
        const int32 JointIndex = Model.FindJointIndex(JointName);
        if (JointIndex == INDEX_NONE)
        {
            Error = FString::Printf(
                TEXT("IK group references unknown joint: %s"),
                *JointName.ToString());
            break;
        }
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            Error = FString::Printf(
                TEXT("IK group references fixed joint: %s"),
                *JointName.ToString());
            break;
        }
        if (ActiveSet.Contains(JointIndex))
        {
            Error = FString::Printf(
                TEXT("IK group contains duplicate joint: %s"),
                *JointName.ToString());
            break;
        }
        ActiveSet.Add(JointIndex);
        ActiveJointIndices.Add(JointIndex);
    }
    if (!Error.IsEmpty() || ActiveJointIndices.Num() == 0)
    {
        if (Error.IsEmpty())
        {
            Error = TEXT("IK joint group must contain at least one revolute joint");
        }
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    TArray<double> JointValues;
    JointValues.Init(0.0, Description.Joints.Num());
    TArray<uint8> Provided;
    Provided.Init(0, Description.Joints.Num());
    for (const FDttNamedJointPosition& NamedPosition : Request.SeedJointPositions)
    {
        if (NamedPosition.JointName.IsNone())
        {
            Error = TEXT("IK seed joint name must be non-empty");
            break;
        }
        const int32 JointIndex = Model.FindJointIndex(NamedPosition.JointName);
        if (JointIndex == INDEX_NONE)
        {
            Error = FString::Printf(
                TEXT("IK seed references unknown joint: %s"),
                *NamedPosition.JointName.ToString());
            break;
        }
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            Error = FString::Printf(
                TEXT("IK seed references fixed joint: %s"),
                *NamedPosition.JointName.ToString());
            break;
        }
        if (Provided[JointIndex] != 0)
        {
            Error = FString::Printf(
                TEXT("IK seed contains duplicate joint: %s"),
                *NamedPosition.JointName.ToString());
            break;
        }
        if (!FMath::IsFinite(NamedPosition.PositionRadians))
        {
            Error = FString::Printf(
                TEXT("IK seed position is non-finite: %s"),
                *NamedPosition.JointName.ToString());
            break;
        }
        Provided[JointIndex] = 1;
        JointValues[JointIndex] = NamedPosition.PositionRadians;
    }
    if (Error.IsEmpty())
    {
        for (int32 JointIndex = 0; JointIndex < Description.Joints.Num(); ++JointIndex)
        {
            if (Description.Joints[JointIndex].Type == EDttRobotJointType::Revolute
                && Provided[JointIndex] == 0)
            {
                Error = FString::Printf(
                    TEXT("IK seed is missing revolute joint: %s"),
                    *Description.Joints[JointIndex].Name.ToString());
                break;
            }
        }
    }
    if (!Error.IsEmpty())
    {
        OutResult.Status = EDttIKStatus::InvalidInput;
        OutResult.Diagnostic = Error;
        OutResult.Diagnostics.Add(Error);
        return false;
    }

    // Project only active joints.  Inactive joints are copied exactly from
    // the complete seed, including any out-of-limit value supplied by an
    // authoring caller.
    TArray<FString> SeedLimitDiagnostics;
    for (const int32 JointIndex : ActiveJointIndices)
    {
        const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
        if (Joint.bHasPositionLimits)
        {
            const double SeedValue = JointValues[JointIndex];
            JointValues[JointIndex] = FMath::Clamp(
                JointValues[JointIndex],
                Joint.LowerPositionRadians,
                Joint.UpperPositionRadians);
            if (JointValues[JointIndex] != SeedValue)
            {
                SeedLimitDiagnostics.Add(FString::Printf(
                    TEXT("active seed joint %s projected from %0.17g to %0.17g within [%0.17g, %0.17g]"),
                    *Joint.Name.ToString(),
                    SeedValue,
                    JointValues[JointIndex],
                    Joint.LowerPositionRadians,
                    Joint.UpperPositionRadians));
            }
        }
    }

    int32 FKEvaluations = 0;
    FForwardSnapshot CurrentSnapshot;
    if (!EvaluateState(
            Description,
            Request,
            Model,
            JointValues,
            Settings.MaxFKEvaluations,
            FKEvaluations,
            CurrentSnapshot,
            Error))
    {
        const EDttIKStatus Status = FKEvaluations >= Settings.MaxFKEvaluations
            ? EDttIKStatus::IterationLimit
            : EDttIKStatus::NumericalFailure;
        FillResult(
            Description,
            Request,
            Status,
            JointValues,
            ActiveJointIndices,
            nullptr,
            nullptr,
            0,
            FKEvaluations,
            Error,
            OutResult);
        OutResult.Diagnostics.Append(SeedLimitDiagnostics);
        return Status != EDttIKStatus::NumericalFailure;
    }

    FTaskEvaluation CurrentEvaluation;
    if (!EvaluateTask(Request, Settings, CurrentSnapshot, TargetApproachAxis, CurrentEvaluation))
    {
        Error = TEXT("initial IK task evaluation was non-finite");
        FillResult(
            Description,
            Request,
            EDttIKStatus::NumericalFailure,
            JointValues,
            ActiveJointIndices,
            &CurrentSnapshot,
            nullptr,
            0,
            FKEvaluations,
            Error,
            OutResult);
        OutResult.Diagnostics.Append(SeedLimitDiagnostics);
        return false;
    }

    TArray<double> BestJointValues = JointValues;
    FForwardSnapshot BestSnapshot = CurrentSnapshot;
    FTaskEvaluation BestEvaluation = CurrentEvaluation;
    if (IsConverged(Request, Settings, CurrentEvaluation))
    {
        FillResult(
            Description,
            Request,
            EDttIKStatus::Converged,
            BestJointValues,
            ActiveJointIndices,
            &BestSnapshot,
            &BestEvaluation,
            0,
            FKEvaluations,
            TEXT("initial seed already satisfies the task tolerances"),
            OutResult);
        OutResult.Diagnostics.Append(SeedLimitDiagnostics);
        return true;
    }

    EDttIKStatus FinalStatus = EDttIKStatus::IterationLimit;
    FString FinalDiagnostic = TEXT("IK iteration limit reached");
    int32 Iterations = 0;
    bool bNumericalFailure = false;
    bool bBudgetExhausted = false;
    for (int32 Iteration = 0; Iteration < Settings.MaxIterations; ++Iteration)
    {
        Iterations = Iteration + 1;
        TArray<TArray<double>> Jacobian;
        Jacobian.SetNum(ActiveJointIndices.Num());
        TArray<FForwardSnapshot> PlusSnapshots;
        TArray<FForwardSnapshot> MinusSnapshots;
        PlusSnapshots.SetNum(ActiveJointIndices.Num());
        MinusSnapshots.SetNum(ActiveJointIndices.Num());

        bool bCentralDifferenceComplete = true;
        for (int32 ActiveColumn = 0; ActiveColumn < ActiveJointIndices.Num(); ++ActiveColumn)
        {
            const int32 JointIndex = ActiveJointIndices[ActiveColumn];
            TArray<double> PlusValues = BestJointValues;
            TArray<double> MinusValues = BestJointValues;
            PlusValues[JointIndex] += Settings.CentralDifferenceStepRadians;
            MinusValues[JointIndex] -= Settings.CentralDifferenceStepRadians;
            if (!EvaluateState(
                    Description,
                    Request,
                    Model,
                    PlusValues,
                    Settings.MaxFKEvaluations,
                    FKEvaluations,
                    PlusSnapshots[ActiveColumn],
                    Error))
            {
                bCentralDifferenceComplete = false;
                bBudgetExhausted = FKEvaluations >= Settings.MaxFKEvaluations;
                break;
            }
            if (!EvaluateState(
                    Description,
                    Request,
                    Model,
                    MinusValues,
                    Settings.MaxFKEvaluations,
                    FKEvaluations,
                    MinusSnapshots[ActiveColumn],
                    Error))
            {
                bCentralDifferenceComplete = false;
                bBudgetExhausted = FKEvaluations >= Settings.MaxFKEvaluations;
                break;
            }
        }
        if (!bCentralDifferenceComplete)
        {
            FinalStatus = bBudgetExhausted
                ? EDttIKStatus::IterationLimit
                : EDttIKStatus::NumericalFailure;
            FinalDiagnostic = bBudgetExhausted
                ? TEXT("maximum FK evaluation budget reached while building the central Jacobian")
                : Error;
            bNumericalFailure = !bBudgetExhausted;
            break;
        }

        TArray<double> TaskError;
        if (!BuildTaskErrorAndJacobian(
                Request,
                Settings,
                BestEvaluation,
                PlusSnapshots,
                MinusSnapshots,
                ActiveJointIndices,
                TaskError,
                Jacobian))
        {
            bNumericalFailure = true;
            FinalStatus = EDttIKStatus::NumericalFailure;
            FinalDiagnostic = TEXT("central Jacobian was non-finite");
            break;
        }

        FLineSearchCandidateSelection CandidateSelection;

        double Lambda = FMath::Clamp(
            Settings.DampingLambda,
            Settings.MinimumDampingLambda,
            Settings.MaximumDampingLambda);
        for (int32 DampingTrial = 0;
             DampingTrial < Settings.MaxDampingTrials
                 && !CandidateSelection.bConvergedCandidate;
             ++DampingTrial)
        {
            TArray<double> Step;
            if (!BuildDampedLeastSquaresStep(
                    TaskError,
                    Jacobian,
                    Lambda,
                    Settings.MaxJointStepRadians,
                    Step))
            {
                Lambda = FMath::Min(
                    Settings.MaximumDampingLambda,
                    Lambda * 2.0);
                continue;
            }

            for (int32 CandidateIndex = 0;
                 CandidateIndex < Settings.MaxLineSearchCandidates;
                 ++CandidateIndex)
            {
                const double Alpha = 1.0 / static_cast<double>(1 << CandidateIndex);
                TArray<double> CandidateValues = BestJointValues;
                for (int32 ActiveColumn = 0; ActiveColumn < ActiveJointIndices.Num(); ++ActiveColumn)
                {
                    const int32 JointIndex = ActiveJointIndices[ActiveColumn];
                    CandidateValues[JointIndex] += Alpha * Step[ActiveColumn];
                    const FDttRobotJointDescription& Joint = Description.Joints[JointIndex];
                    if (Joint.bHasPositionLimits)
                    {
                        CandidateValues[JointIndex] = FMath::Clamp(
                            CandidateValues[JointIndex],
                            Joint.LowerPositionRadians,
                            Joint.UpperPositionRadians);
                    }
                }

                FForwardSnapshot CandidateSnapshot;
                if (!EvaluateState(
                        Description,
                        Request,
                        Model,
                        CandidateValues,
                        Settings.MaxFKEvaluations,
                        FKEvaluations,
                        CandidateSnapshot,
                        Error))
                {
                    bBudgetExhausted = FKEvaluations >= Settings.MaxFKEvaluations;
                    bNumericalFailure = !bBudgetExhausted;
                    break;
                }
                FTaskEvaluation CandidateEvaluation;
                if (!EvaluateTask(
                        Request,
                        Settings,
                        CandidateSnapshot,
                        TargetApproachAxis,
                        CandidateEvaluation))
                {
                    bNumericalFailure = true;
                    FinalStatus = EDttIKStatus::NumericalFailure;
                    FinalDiagnostic = TEXT("line-search task evaluation was non-finite");
                    break;
                }
                ConsiderLineSearchCandidate(
                    Request,
                    Settings,
                    CandidateValues,
                    CandidateSnapshot,
                    CandidateEvaluation,
                    CandidateSelection);
                if (CandidateSelection.bConvergedCandidate)
                {
                    break;
                }
            }
            if (bBudgetExhausted || bNumericalFailure)
            {
                break;
            }
            Lambda = FMath::Min(
                Settings.MaximumDampingLambda,
                Lambda * 2.0);
        }

        if (bNumericalFailure)
        {
            break;
        }
        if (CandidateSelection.bConvergedCandidate)
        {
            (void)AcceptLineSearchCandidate(
                Settings,
                BestEvaluation,
                CandidateSelection,
                BestJointValues,
                BestSnapshot,
                BestEvaluation);
            FinalStatus = EDttIKStatus::Converged;
            FinalDiagnostic = TEXT("IK task tolerances reached");
            break;
        }
        if (AcceptLineSearchCandidate(
                Settings,
                BestEvaluation,
                CandidateSelection,
                BestJointValues,
                BestSnapshot,
                BestEvaluation))
        {
            if (IsConverged(Request, Settings, BestEvaluation))
            {
                FinalStatus = EDttIKStatus::Converged;
                FinalDiagnostic = TEXT("IK task tolerances reached");
                break;
            }
        }
        else
        {
            FinalStatus = bBudgetExhausted
                ? EDttIKStatus::IterationLimit
                : EDttIKStatus::Partial;
            FinalDiagnostic = bBudgetExhausted
                ? TEXT("maximum FK evaluation budget reached during line search")
                : TEXT("IK stagnated before reaching the task tolerances");
            break;
        }
        if (bBudgetExhausted)
        {
            FinalStatus = EDttIKStatus::IterationLimit;
            FinalDiagnostic = TEXT("maximum FK evaluation budget reached during line search");
            break;
        }
    }

    if (!bNumericalFailure
        && FinalStatus == EDttIKStatus::IterationLimit
        && Iterations >= Settings.MaxIterations
        && Settings.MaxIterations > 0)
    {
        FinalDiagnostic = TEXT("maximum IK iterations reached");
    }
    if (bNumericalFailure)
    {
        FinalStatus = EDttIKStatus::NumericalFailure;
    }
    FillResult(
        Description,
        Request,
        FinalStatus,
        BestJointValues,
        ActiveJointIndices,
        &BestSnapshot,
        &BestEvaluation,
        Iterations,
        FKEvaluations,
        FinalDiagnostic,
        OutResult);
    OutResult.Diagnostics.Append(SeedLimitDiagnostics);
    return FinalStatus != EDttIKStatus::NumericalFailure;
}

} // namespace DeferredTeleop::Kinematics

bool UDeferredTeleopIKLibrary::SolveInverseKinematics(
    const FDttRobotDescription& Description,
    const FDttIKRequest& Request,
    const FDttIKSettings& Settings,
    FDttIKResult& OutResult)
{
    return DeferredTeleop::Kinematics::SolveInverseKinematics(
        Description,
        Request,
        Settings,
        OutResult);
}
