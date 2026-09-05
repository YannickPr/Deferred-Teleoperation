#include "Kinematics/DeferredTeleopKinematicPreviewLibrary.h"

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Math/UnrealMathUtility.h"

namespace DeferredTeleop::KinematicPreview::Private
{
constexpr double ToolPositionToleranceMetres = 1.0e-9;
constexpr double ToolRotationToleranceRadians = 1.0e-9;
constexpr double MaximumPreviewRateHz = 1000.0;
constexpr double MaximumPreviewDurationSeconds = 30.0;
constexpr int32 MinimumPreviewSamples = 2;
constexpr int32 MaximumPreviewSamples = 128;

bool Fail(FString& OutError, const FString& Message)
{
    OutError = Message;
    return false;
}

bool IsValidDescriptionHash(const FString& DescriptionHash)
{
    constexpr int32 PrefixLength = 7; // "sha256:"
    constexpr int32 DigestLength = 64;
    if (DescriptionHash.Len() != PrefixLength + DigestLength
        || !DescriptionHash.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive))
    {
        return false;
    }

    for (int32 Index = PrefixLength; Index < DescriptionHash.Len(); ++Index)
    {
        const TCHAR Character = DescriptionHash[Index];
        const bool bLowerHex =
            (Character >= TEXT('0') && Character <= TEXT('9'))
            || (Character >= TEXT('a') && Character <= TEXT('f'));
        if (!bLowerHex)
        {
            return false;
        }
    }
    return true;
}

bool ValidateIdsAndModelReference(
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request,
    FString& OutError)
{
    if (!Request.PreviewId.IsValid() || !Request.GoalId.IsValid())
    {
        return Fail(OutError, TEXT("preview_id and goal_id must be valid non-zero GUIDs"));
    }
    if (Request.ModelReference.ModelId.IsEmpty()
        || Request.ModelReference.ModelRevision.IsEmpty())
    {
        return Fail(
            OutError,
            TEXT("preview model reference model_id and model_revision are required"));
    }
    if (!IsValidDescriptionHash(Request.ModelReference.DescriptionHash))
    {
        return Fail(
            OutError,
            TEXT("preview model reference description_hash must be sha256: followed by 64 lowercase hex digits"));
    }
    if (!Request.ModelReference.ModelId.Equals(Description.ModelId, ESearchCase::CaseSensitive)
        || !Request.ModelReference.ModelRevision.Equals(
            Description.ModelRevision,
            ESearchCase::CaseSensitive))
    {
        return Fail(OutError, TEXT("preview model reference does not match the description"));
    }
    if (!Request.IKResult.ModelId.Equals(Description.ModelId, ESearchCase::CaseSensitive)
        || !Request.IKResult.ModelRevision.Equals(
            Description.ModelRevision,
            ESearchCase::CaseSensitive))
    {
        return Fail(OutError, TEXT("IK result model reference does not match the description"));
    }
    return true;
}

bool ValidateSourceReference(
    const FDttPreviewSourceReference& Source,
    FString& OutError)
{
    if (Source.SourceMessageId.IsEmpty()
        || Source.CorrelationId.IsEmpty()
        || Source.FrameId.IsEmpty()
        || Source.CalibrationVersion.IsEmpty())
    {
        return Fail(
            OutError,
            TEXT("preview source message, correlation, frame, and calibration identifiers are required"));
    }

    const FDeferredTeleopEvidence& Evidence = Source.Evidence;
    if (Evidence.SourceIds.Num() == 0)
    {
        return Fail(OutError, TEXT("preview source evidence must contain at least one source id"));
    }
    for (const FString& SourceId : Evidence.SourceIds)
    {
        if (SourceId.IsEmpty())
        {
            return Fail(OutError, TEXT("preview source evidence source ids must be non-empty"));
        }
    }
    const int64 MinimumDateTicks = FDateTime::MinValue().GetTicks();
    const int64 MaximumDateTicks = FDateTime::MaxValue().GetTicks();
    const bool bObservedDateInRange =
        Evidence.ObservedAt.GetTicks() >= MinimumDateTicks
        && Evidence.ObservedAt.GetTicks() <= MaximumDateTicks;
    const bool bProducedDateInRange =
        Evidence.ProducedAt.GetTicks() >= MinimumDateTicks
        && Evidence.ProducedAt.GetTicks() <= MaximumDateTicks;
    if (!bObservedDateInRange || !bProducedDateInRange)
    {
        return Fail(OutError, TEXT("preview source evidence dates must be valid"));
    }
    if (Evidence.ProducedAt < Evidence.ObservedAt)
    {
        return Fail(OutError, TEXT("preview source evidence produced_at precedes observed_at"));
    }
    if (Evidence.WorldRevision <= 0)
    {
        return Fail(OutError, TEXT("preview source evidence world_revision must be positive"));
    }

    EDeferredTeleopProvenance ExpectedProvenance = EDeferredTeleopProvenance::Unknown;
    switch (Source.SourceKind)
    {
    case EDttPreviewSourceKind::Measured:
        ExpectedProvenance = EDeferredTeleopProvenance::Measured;
        break;
    case EDttPreviewSourceKind::Fused:
        ExpectedProvenance = EDeferredTeleopProvenance::Fused;
        break;
    case EDttPreviewSourceKind::Synthetic:
        ExpectedProvenance = EDeferredTeleopProvenance::Simulated;
        break;
    case EDttPreviewSourceKind::OperatorAsserted:
        ExpectedProvenance = EDeferredTeleopProvenance::OperatorAsserted;
        break;
    default:
        return Fail(OutError, TEXT("preview source kind is unsupported"));
    }
    if (Evidence.Provenance != ExpectedProvenance)
    {
        return Fail(OutError, TEXT("preview source kind and evidence provenance do not match"));
    }
    return true;
}

bool ValidateSettings(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    const FDttKinematicPreviewSettings& Settings,
    TMap<FName, double>& OutVelocities,
    FString& OutError)
{
    if (!FMath::IsFinite(Settings.SampleRateHz)
        || Settings.SampleRateHz <= 0.0
        || Settings.SampleRateHz > MaximumPreviewRateHz)
    {
        return Fail(OutError, TEXT("preview sample_rate_hz must be in (0, 1000]"));
    }
    if (!FMath::IsFinite(Settings.MaximumDurationSeconds)
        || Settings.MaximumDurationSeconds <= 0.0
        || Settings.MaximumDurationSeconds > MaximumPreviewDurationSeconds)
    {
        return Fail(OutError, TEXT("preview maximum_duration_seconds must be in (0, 30]"));
    }
    if (Settings.MaximumSamples < MinimumPreviewSamples
        || Settings.MaximumSamples > MaximumPreviewSamples)
    {
        return Fail(OutError, TEXT("preview maximum_samples must be in [2, 128]"));
    }

    OutVelocities.Reset();
    OutVelocities.Reserve(Settings.JointVelocities.Num());
    TSet<FName> SeenNames;
    SeenNames.Reserve(Settings.JointVelocities.Num());
    for (const FDttPreviewJointVelocity& Velocity : Settings.JointVelocities)
    {
        if (Velocity.JointName.IsNone())
        {
            return Fail(OutError, TEXT("preview joint velocity name must be non-empty"));
        }
        const int32 JointIndex = Model.FindJointIndex(Velocity.JointName);
        if (JointIndex == INDEX_NONE)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("preview joint velocity references unknown joint: %s"),
                    *Velocity.JointName.ToString()));
        }
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("preview joint velocity references fixed joint: %s"),
                    *Velocity.JointName.ToString()));
        }
        if (SeenNames.Contains(Velocity.JointName))
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("preview joint velocity is duplicated: %s"),
                    *Velocity.JointName.ToString()));
        }
        if (!FMath::IsFinite(Velocity.MaximumRadiansPerSecond)
            || Velocity.MaximumRadiansPerSecond <= 0.0)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("preview joint velocity must be finite and positive in radians per second: %s"),
                    *Velocity.JointName.ToString()));
        }
        SeenNames.Add(Velocity.JointName);
        OutVelocities.Add(Velocity.JointName, Velocity.MaximumRadiansPerSecond);
    }

    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type == EDttRobotJointType::Revolute
            && !SeenNames.Contains(Joint.Name))
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("preview joint velocity is missing for revolute joint: %s"),
                    *Joint.Name.ToString()));
        }
    }
    return true;
}

bool ValidateJointLimit(
    const FDttRobotJointDescription& Joint,
    double PositionRadians,
    const TCHAR* StateLabel,
    FString& OutError)
{
    if (!FMath::IsFinite(PositionRadians))
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("%s joint position is non-finite: %s"),
                StateLabel,
                *Joint.Name.ToString()));
    }
    if (Joint.bHasPositionLimits
        && (PositionRadians < Joint.LowerPositionRadians
            || PositionRadians > Joint.UpperPositionRadians))
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("%s joint position is outside limits: %s"),
                StateLabel,
                *Joint.Name.ToString()));
    }
    return true;
}

bool AddValidatedJointValue(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    FName JointName,
    double PositionRadians,
    const TCHAR* StateLabel,
    TSet<FName>& InOutSeenNames,
    TMap<FName, double>& InOutValues,
    FString& OutError)
{
    if (JointName.IsNone())
    {
        return Fail(
            OutError,
            FString::Printf(TEXT("%s joint name must be non-empty"), StateLabel));
    }
    const int32 JointIndex = Model.FindJointIndex(JointName);
    if (JointIndex == INDEX_NONE)
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("%s references unknown joint: %s"),
                StateLabel,
                *JointName.ToString()));
    }
    if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("%s references fixed joint: %s"),
                StateLabel,
                *JointName.ToString()));
    }
    if (InOutSeenNames.Contains(JointName))
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("%s contains duplicate joint: %s"),
                StateLabel,
                *JointName.ToString()));
    }
    if (!ValidateJointLimit(
            Description.Joints[JointIndex],
            PositionRadians,
            StateLabel,
            OutError))
    {
        return false;
    }
    InOutSeenNames.Add(JointName);
    InOutValues.Add(JointName, PositionRadians);
    return true;
}

bool EnsureAllRevoluteJointsPresent(
    const FDttRobotDescription& Description,
    const TSet<FName>& SeenNames,
    const TCHAR* StateLabel,
    FString& OutError)
{
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type == EDttRobotJointType::Revolute
            && !SeenNames.Contains(Joint.Name))
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("%s is missing revolute joint: %s"),
                    StateLabel,
                    *Joint.Name.ToString()));
        }
    }
    return true;
}

bool ValidateStartJointPositions(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    const TArray<FDeferredTeleopArticulatedJointPosition>& Input,
    TMap<FName, double>& OutValues,
    FString& OutError)
{
    OutValues.Reset();
    OutValues.Reserve(Input.Num());
    TSet<FName> SeenNames;
    SeenNames.Reserve(Input.Num());
    for (const FDeferredTeleopArticulatedJointPosition& Position : Input)
    {
        if (Position.JointName.IsEmpty())
        {
            return Fail(OutError, TEXT("start joint name must be non-empty"));
        }
        const FName JointName(*Position.JointName);
        if (!AddValidatedJointValue(
                Description,
                Model,
                JointName,
                Position.PositionRadians,
                TEXT("start state"),
                SeenNames,
                OutValues,
                OutError))
        {
            return false;
        }
    }
    return EnsureAllRevoluteJointsPresent(
        Description,
        SeenNames,
        TEXT("start state"),
        OutError);
}

bool ValidateIKJointPositions(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    const TArray<FDttNamedJointPosition>& Input,
    TMap<FName, double>& OutValues,
    FString& OutError)
{
    OutValues.Reset();
    OutValues.Reserve(Input.Num());
    TSet<FName> SeenNames;
    SeenNames.Reserve(Input.Num());
    for (const FDttNamedJointPosition& Position : Input)
    {
        if (!AddValidatedJointValue(
                Description,
                Model,
                Position.JointName,
                Position.PositionRadians,
                TEXT("IK result"),
                SeenNames,
                OutValues,
                OutError))
        {
            return false;
        }
    }
    return EnsureAllRevoluteJointsPresent(
        Description,
        SeenNames,
        TEXT("IK result"),
        OutError);
}

bool ValidateActiveJointNames(
    const FDttRobotDescription& Description,
    const FDttValidatedRobotModel& Model,
    const TArray<FName>& ActiveNames,
    TSet<FName>& OutActiveNames,
    FString& OutError)
{
    OutActiveNames.Reset();
    OutActiveNames.Reserve(ActiveNames.Num());
    for (const FName JointName : ActiveNames)
    {
        if (JointName.IsNone())
        {
            return Fail(OutError, TEXT("IK active joint name must be non-empty"));
        }
        const int32 JointIndex = Model.FindJointIndex(JointName);
        if (JointIndex == INDEX_NONE)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("IK active joint is unknown: %s"),
                    *JointName.ToString()));
        }
        if (Description.Joints[JointIndex].Type != EDttRobotJointType::Revolute)
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("IK active joint is fixed: %s"),
                    *JointName.ToString()));
        }
        if (OutActiveNames.Contains(JointName))
        {
            return Fail(
                OutError,
                FString::Printf(
                    TEXT("IK active joint is duplicated: %s"),
                    *JointName.ToString()));
        }
        OutActiveNames.Add(JointName);
    }
    return true;
}

bool ValidateIKStatus(
    const FDttIKResult& IKResult,
    bool bAcceptPartial,
    bool& OutAcceptedPartial,
    FString& OutError)
{
    OutAcceptedPartial = false;
    switch (IKResult.Status)
    {
    case EDttIKStatus::Converged:
        if (!IKResult.bSuccess)
        {
            return Fail(OutError, TEXT("converged IK result must have bSuccess=true"));
        }
        return true;
    case EDttIKStatus::Partial:
    case EDttIKStatus::IterationLimit:
        if (IKResult.bSuccess)
        {
            return Fail(OutError, TEXT("partial IK result must have bSuccess=false"));
        }
        if (!bAcceptPartial)
        {
            return Fail(OutError, TEXT("partial IK result requires bAcceptPartial=true"));
        }
        OutAcceptedPartial = true;
        return true;
    default:
        return Fail(OutError, TEXT("IK result status is not accepted by a kinematic preview"));
    }
}

TArray<FDttNamedJointPosition> MakeNamedJointState(
    const FDttRobotDescription& Description,
    const TMap<FName, double>& Values)
{
    TArray<FDttNamedJointPosition> Result;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double* Value = Values.Find(Joint.Name);
        if (Value == nullptr)
        {
            continue;
        }
        FDttNamedJointPosition& Position = Result.AddDefaulted_GetRef();
        Position.JointName = Joint.Name;
        Position.PositionRadians = *Value;
    }
    return Result;
}

TArray<FDeferredTeleopArticulatedJointPosition> MakeArticulatedJointState(
    const FDttRobotDescription& Description,
    const TMap<FName, double>& Values)
{
    TArray<FDeferredTeleopArticulatedJointPosition> Result;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double* Value = Values.Find(Joint.Name);
        if (Value == nullptr)
        {
            continue;
        }
        FDeferredTeleopArticulatedJointPosition& Position = Result.AddDefaulted_GetRef();
        Position.JointName = Joint.Name.ToString();
        Position.PositionRadians = *Value;
    }
    return Result;
}

bool FindToolTransform(
    const TArray<FDttNamedCanonicalTransform>& ToolTransforms,
    FName ToolFrameName,
    FDttCanonicalTransform& OutToolTransform,
    FString& OutError)
{
    for (const FDttNamedCanonicalTransform& NamedTransform : ToolTransforms)
    {
        if (NamedTransform.Name == ToolFrameName)
        {
            if (!NamedTransform.Transform.IsRigid())
            {
                return Fail(OutError, TEXT("FK returned a non-rigid tool transform"));
            }
            OutToolTransform = NamedTransform.Transform;
            return true;
        }
    }
    return Fail(
        OutError,
        FString::Printf(
            TEXT("FK did not return the requested tool frame: %s"),
            *ToolFrameName.ToString()));
}

bool EvaluateToolTransform(
    const FDttRobotDescription& Description,
    const FDttCanonicalTransform& WorldTransformOfRoot,
    FName ToolFrameName,
    const TArray<FDttNamedJointPosition>& JointPositions,
    FDttCanonicalTransform& OutToolTransform,
    FString& OutError)
{
    FDttForwardKinematicsResult FKResult;
    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Description,
            WorldTransformOfRoot,
            JointPositions,
            FKResult))
    {
        const FString FKError = FKResult.ErrorMessage.IsEmpty()
            ? FString(TEXT("FK rejected a preview joint state"))
            : FKResult.ErrorMessage;
        return Fail(OutError, FKError);
    }
    if (!FKResult.bSuccess)
    {
        return Fail(OutError, TEXT("FK returned an unsuccessful preview joint state"));
    }
    if (!FKResult.bWithinJointLimits)
    {
        return Fail(OutError, TEXT("FK reported a preview joint state outside limits"));
    }
    return FindToolTransform(FKResult.ToolTransforms, ToolFrameName, OutToolTransform, OutError);
}

bool CompareRigidTransforms(
    const FDttCanonicalTransform& Expected,
    const FDttCanonicalTransform& Actual,
    double& OutPositionErrorMetres,
    double& OutRotationErrorRadians,
    FString& OutError)
{
    const FVector3d TranslationDelta =
        Expected.GetTranslationMetres() - Actual.GetTranslationMetres();
    const double PositionErrorSquared =
        TranslationDelta.X * TranslationDelta.X
        + TranslationDelta.Y * TranslationDelta.Y
        + TranslationDelta.Z * TranslationDelta.Z;
    if (!FMath::IsFinite(PositionErrorSquared) || PositionErrorSquared < 0.0)
    {
        return Fail(OutError, TEXT("tool transform position comparison is non-finite"));
    }
    OutPositionErrorMetres = FMath::Sqrt(PositionErrorSquared);

    const FQuat4d ExpectedRotation = Expected.GetRotationQuaternion();
    const FQuat4d ActualRotation = Actual.GetRotationQuaternion();
    const double ExpectedNormSquared =
        ExpectedRotation.X * ExpectedRotation.X
        + ExpectedRotation.Y * ExpectedRotation.Y
        + ExpectedRotation.Z * ExpectedRotation.Z
        + ExpectedRotation.W * ExpectedRotation.W;
    const double ActualNormSquared =
        ActualRotation.X * ActualRotation.X
        + ActualRotation.Y * ActualRotation.Y
        + ActualRotation.Z * ActualRotation.Z
        + ActualRotation.W * ActualRotation.W;
    const double NormProduct = FMath::Sqrt(ExpectedNormSquared * ActualNormSquared);
    if (!FMath::IsFinite(ExpectedNormSquared)
        || !FMath::IsFinite(ActualNormSquared)
        || !FMath::IsFinite(NormProduct)
        || NormProduct <= UE_DOUBLE_SMALL_NUMBER)
    {
        return Fail(OutError, TEXT("tool transform rotation comparison is non-finite"));
    }

    const double ExpectedInverseScale = 1.0 / FMath::Sqrt(ExpectedNormSquared);
    const double ActualScale = 1.0 / FMath::Sqrt(ActualNormSquared);
    const FQuat4d ExpectedUnit(
        -ExpectedRotation.X * ExpectedInverseScale,
        -ExpectedRotation.Y * ExpectedInverseScale,
        -ExpectedRotation.Z * ExpectedInverseScale,
        ExpectedRotation.W * ExpectedInverseScale);
    const FQuat4d ActualUnit(
        ActualRotation.X * ActualScale,
        ActualRotation.Y * ActualScale,
        ActualRotation.Z * ActualScale,
        ActualRotation.W * ActualScale);
    // Use the relative quaternion's vector norm so very small rotations are
    // measured without losing the 1e-9-radian tolerance in acos(1 - eps).
    const FQuat4d Relative(
        ExpectedUnit.W * ActualUnit.X
            + ExpectedUnit.X * ActualUnit.W
            + ExpectedUnit.Y * ActualUnit.Z
            - ExpectedUnit.Z * ActualUnit.Y,
        ExpectedUnit.W * ActualUnit.Y
            - ExpectedUnit.X * ActualUnit.Z
            + ExpectedUnit.Y * ActualUnit.W
            + ExpectedUnit.Z * ActualUnit.X,
        ExpectedUnit.W * ActualUnit.Z
            + ExpectedUnit.X * ActualUnit.Y
            - ExpectedUnit.Y * ActualUnit.X
            + ExpectedUnit.Z * ActualUnit.W,
        ExpectedUnit.W * ActualUnit.W
            - ExpectedUnit.X * ActualUnit.X
            - ExpectedUnit.Y * ActualUnit.Y
            - ExpectedUnit.Z * ActualUnit.Z);
    const double RelativeVectorNormSquared =
        Relative.X * Relative.X
        + Relative.Y * Relative.Y
        + Relative.Z * Relative.Z;
    if (!FMath::IsFinite(Relative.W)
        || !FMath::IsFinite(RelativeVectorNormSquared)
        || RelativeVectorNormSquared < 0.0)
    {
        return Fail(OutError, TEXT("tool transform rotation comparison is non-finite"));
    }
    OutRotationErrorRadians = 2.0 * FMath::Atan2(
        FMath::Sqrt(RelativeVectorNormSquared),
        FMath::Abs(Relative.W));
    if (!FMath::IsFinite(OutPositionErrorMetres)
        || !FMath::IsFinite(OutRotationErrorRadians))
    {
        return Fail(OutError, TEXT("tool transform comparison is non-finite"));
    }
    return true;
}

} // namespace DeferredTeleop::KinematicPreview::Private

namespace DeferredTeleop::Kinematics
{
namespace PreviewPrivate = ::DeferredTeleop::KinematicPreview::Private;

bool BuildPreview(
    const FDttRobotDescription& Description,
    const FDttKinematicPreviewRequest& Request,
    FDttKinematicPreview& OutPreview,
    FString& OutError)
{
    OutPreview = FDttKinematicPreview();
    OutError.Reset();

    FDttValidatedRobotModel Model;
    if (!ValidateRobotDescription(Description, Model, OutError))
    {
        return false;
    }
    if (!PreviewPrivate::ValidateIdsAndModelReference(Description, Request, OutError)
        || !PreviewPrivate::ValidateSourceReference(Request.SourceReference, OutError))
    {
        return false;
    }
    if (!Request.WorldTransformOfRoot.IsRigid())
    {
        return PreviewPrivate::Fail(
            OutError,
            TEXT("world_transform_of_root must be a finite rigid canonical transform"));
    }

    TMap<FName, double> Velocities;
    if (!PreviewPrivate::ValidateSettings(Description, Model, Request.Settings, Velocities, OutError))
    {
        return false;
    }

    TMap<FName, double> StartValues;
    if (!PreviewPrivate::ValidateStartJointPositions(
            Description,
            Model,
            Request.StartJointPositions,
            StartValues,
            OutError))
    {
        return false;
    }

    TMap<FName, double> IKValues;
    if (!PreviewPrivate::ValidateIKJointPositions(
            Description,
            Model,
            Request.IKResult.JointPositions,
            IKValues,
            OutError))
    {
        return false;
    }

    bool bAcceptedPartial = false;
    if (!PreviewPrivate::ValidateIKStatus(
            Request.IKResult,
            Request.Settings.bAcceptPartial,
            bAcceptedPartial,
            OutError))
    {
        return false;
    }

    TSet<FName> ActiveNames;
    if (!PreviewPrivate::ValidateActiveJointNames(
            Description,
            Model,
            Request.IKResult.ActiveJointNames,
            ActiveNames,
            OutError))
    {
        return false;
    }
    if (Request.IKResult.ToolFrameName.IsNone()
        || Model.FindToolIndex(Request.IKResult.ToolFrameName) == INDEX_NONE)
    {
        return PreviewPrivate::Fail(
            OutError,
            TEXT("IK result tool frame is required and must be known to the description"));
    }
    if (!FMath::IsFinite(Request.IKResult.PositionResidualMetres)
        || Request.IKResult.PositionResidualMetres < 0.0
        || !FMath::IsFinite(Request.IKResult.ApproachResidualRadians)
        || Request.IKResult.ApproachResidualRadians < 0.0)
    {
        return PreviewPrivate::Fail(
            OutError,
            TEXT("IK residuals must be finite and non-negative"));
    }
    if (!Request.IKResult.AchievedToolTransform.IsRigid())
    {
        return PreviewPrivate::Fail(
            OutError,
            TEXT("IK achieved tool transform must be a finite rigid transform"));
    }

    TMap<FName, double> GoalValues;
    GoalValues.Reserve(Description.Joints.Num());
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double* StartValue = StartValues.Find(Joint.Name);
        const double* IKValue = IKValues.Find(Joint.Name);
        if (StartValue == nullptr || IKValue == nullptr)
        {
            return PreviewPrivate::Fail(
                OutError,
                FString::Printf(
                    TEXT("preview state is missing revolute joint: %s"),
                    *Joint.Name.ToString()));
        }
        if (!ActiveNames.Contains(Joint.Name) && *IKValue != *StartValue)
        {
            return PreviewPrivate::Fail(
                OutError,
                FString::Printf(
                    TEXT("inactive IK goal must equal start exactly: %s"),
                    *Joint.Name.ToString()));
        }
        const double GoalValue = ActiveNames.Contains(Joint.Name) ? *IKValue : *StartValue;
        if (!PreviewPrivate::ValidateJointLimit(
                Joint,
                GoalValue,
                TEXT("goal state"),
                OutError))
        {
            return false;
        }
        GoalValues.Add(Joint.Name, GoalValue);
    }

    const TArray<FDttNamedJointPosition> GoalNamedState =
        PreviewPrivate::MakeNamedJointState(Description, GoalValues);
    FDttCanonicalTransform RecomputedToolTransform;
    if (!PreviewPrivate::EvaluateToolTransform(
            Description,
            Request.WorldTransformOfRoot,
            Request.IKResult.ToolFrameName,
            GoalNamedState,
            RecomputedToolTransform,
            OutError))
    {
        return false;
    }

    double GoalPositionErrorMetres = 0.0;
    double GoalRotationErrorRadians = 0.0;
    if (!PreviewPrivate::CompareRigidTransforms(
            Request.IKResult.AchievedToolTransform,
            RecomputedToolTransform,
            GoalPositionErrorMetres,
            GoalRotationErrorRadians,
            OutError))
    {
        return false;
    }
    if (GoalPositionErrorMetres > PreviewPrivate::ToolPositionToleranceMetres
        || GoalRotationErrorRadians > PreviewPrivate::ToolRotationToleranceRadians)
    {
        return PreviewPrivate::Fail(
            OutError,
            FString::Printf(
                TEXT("IK achieved tool transform does not match FK goal within tolerance (position=%0.17g m, rotation=%0.17g rad)"),
                GoalPositionErrorMetres,
                GoalRotationErrorRadians));
    }

    double DurationSeconds = 0.0;
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        if (Joint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const double* StartValue = StartValues.Find(Joint.Name);
        const double* GoalValue = GoalValues.Find(Joint.Name);
        const double* Velocity = Velocities.Find(Joint.Name);
        if (StartValue == nullptr || GoalValue == nullptr || Velocity == nullptr)
        {
            return PreviewPrivate::Fail(
                OutError,
                FString::Printf(
                    TEXT("preview timing is missing revolute joint data: %s"),
                    *Joint.Name.ToString()));
        }
        const double DeltaRadians = FMath::Abs(*GoalValue - *StartValue);
        const double JointDuration = DeltaRadians / *Velocity;
        if (!FMath::IsFinite(DeltaRadians) || !FMath::IsFinite(JointDuration))
        {
            return PreviewPrivate::Fail(
                OutError,
                FString::Printf(
                    TEXT("preview timing is non-finite for joint: %s"),
                    *Joint.Name.ToString()));
        }
        DurationSeconds = FMath::Max(DurationSeconds, JointDuration);
    }
    if (!FMath::IsFinite(DurationSeconds) || DurationSeconds < 0.0)
    {
        return PreviewPrivate::Fail(OutError, TEXT("preview duration must be finite and non-negative"));
    }
    if (DurationSeconds > Request.Settings.MaximumDurationSeconds)
    {
        return PreviewPrivate::Fail(
            OutError,
            TEXT("preview duration exceeds maximum_duration_seconds"));
    }

    int32 SampleCount = 1;
    if (DurationSeconds > 0.0)
    {
        const double RateProduct = DurationSeconds * Request.Settings.SampleRateHz;
        if (!FMath::IsFinite(RateProduct))
        {
            return PreviewPrivate::Fail(OutError, TEXT("preview sample count calculation is non-finite"));
        }
        // The validated 30-second and 1000-Hz bounds make this conversion safe.
        const int64 RequiredSamples64 = FMath::Max(
            static_cast<int64>(PreviewPrivate::MinimumPreviewSamples),
            FMath::CeilToInt(RateProduct) + 1);
        if (RequiredSamples64 > static_cast<int64>(MAX_int32))
        {
            return PreviewPrivate::Fail(OutError, TEXT("preview sample count exceeds integer range"));
        }
        const int32 RequiredSamples = static_cast<int32>(RequiredSamples64);
        SampleCount = FMath::Min(Request.Settings.MaximumSamples, RequiredSamples);
        if (SampleCount < PreviewPrivate::MinimumPreviewSamples
            || SampleCount > PreviewPrivate::MaximumPreviewSamples)
        {
            return PreviewPrivate::Fail(OutError, TEXT("preview sample count is outside the bounded range"));
        }
    }

    FDttKinematicPreview Candidate;
    Candidate.PreviewId = Request.PreviewId;
    Candidate.GoalId = Request.GoalId;
    Candidate.ModelReference = Request.ModelReference;
    Candidate.SourceReference = Request.SourceReference;
    Candidate.WorldTransformOfRoot = Request.WorldTransformOfRoot;
    Candidate.StartJointPositions =
        PreviewPrivate::MakeArticulatedJointState(Description, StartValues);
    Candidate.GoalJointPositions =
        PreviewPrivate::MakeArticulatedJointState(Description, GoalValues);
    Candidate.ToolFrameName = Request.IKResult.ToolFrameName;
    Candidate.IKStatus = Request.IKResult.Status;
    Candidate.bAcceptedPartial = bAcceptedPartial;
    Candidate.PositionResidualMetres = Request.IKResult.PositionResidualMetres;
    Candidate.ApproachResidualRadians = Request.IKResult.ApproachResidualRadians;
    Candidate.DurationSeconds = DurationSeconds;
    Candidate.Diagnostic = Request.IKResult.Diagnostic;
    Candidate.Diagnostics = Request.IKResult.Diagnostics;
    Candidate.Samples.Reserve(SampleCount);

    const int32 LastSampleIndex = SampleCount - 1;
    for (int32 SampleIndex = 0; SampleIndex < SampleCount; ++SampleIndex)
    {
        FDttKinematicPreviewSample Sample;
        if (SampleIndex == 0)
        {
            Sample.TimeSeconds = 0.0;
        }
        else if (SampleIndex == LastSampleIndex)
        {
            Sample.TimeSeconds = DurationSeconds;
        }
        else
        {
            Sample.TimeSeconds =
                DurationSeconds * static_cast<double>(SampleIndex)
                / static_cast<double>(LastSampleIndex);
        }
        if (!FMath::IsFinite(Sample.TimeSeconds)
            || Sample.TimeSeconds < 0.0
            || Sample.TimeSeconds > DurationSeconds)
        {
            return PreviewPrivate::Fail(OutError, TEXT("preview sample time is invalid"));
        }

        TArray<FDttNamedJointPosition> FKJointState;
        FKJointState.Reserve(Description.Joints.Num());
        Sample.JointPositions.Reserve(Description.Joints.Num());
        for (const FDttRobotJointDescription& Joint : Description.Joints)
        {
            if (Joint.Type != EDttRobotJointType::Revolute)
            {
                continue;
            }
            const double* StartValue = StartValues.Find(Joint.Name);
            const double* GoalValue = GoalValues.Find(Joint.Name);
            if (StartValue == nullptr || GoalValue == nullptr)
            {
                return PreviewPrivate::Fail(
                    OutError,
                    FString::Printf(
                        TEXT("preview sample is missing revolute joint data: %s"),
                        *Joint.Name.ToString()));
            }

            double PositionRadians = 0.0;
            if (SampleIndex == 0)
            {
                PositionRadians = *StartValue;
            }
            else if (SampleIndex == LastSampleIndex)
            {
                PositionRadians = *GoalValue;
            }
            else
            {
                PositionRadians = *StartValue
                    + (*GoalValue - *StartValue) * Sample.TimeSeconds / DurationSeconds;
            }
            if (!PreviewPrivate::ValidateJointLimit(
                    Joint,
                    PositionRadians,
                    TEXT("preview sample"),
                    OutError))
            {
                return false;
            }

            FDttNamedJointPosition& FKPosition = FKJointState.AddDefaulted_GetRef();
            FKPosition.JointName = Joint.Name;
            FKPosition.PositionRadians = PositionRadians;
            FDeferredTeleopArticulatedJointPosition& OutputPosition =
                Sample.JointPositions.AddDefaulted_GetRef();
            OutputPosition.JointName = Joint.Name.ToString();
            OutputPosition.PositionRadians = PositionRadians;
        }

        if (!PreviewPrivate::EvaluateToolTransform(
                Description,
                Request.WorldTransformOfRoot,
                Request.IKResult.ToolFrameName,
                FKJointState,
                Sample.ToolTransform,
                OutError))
        {
            return false;
        }
        Candidate.Samples.Add(MoveTemp(Sample));
    }

    Candidate.bValid = true;
    OutPreview = MoveTemp(Candidate);
    return true;
}
} // namespace DeferredTeleop::Kinematics

bool UDeferredTeleopKinematicPreviewLibrary::BuildPreview(
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
