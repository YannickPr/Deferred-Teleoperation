#include "Articulated/DeferredTeleopArticulatedSceneValidation.h"

#include "Articulated/DeferredTeleopArticulatedViewParser.h"
#include "HAL/Platform.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "RobotModel/DeferredTeleopRobotModelTypes.h"
#include "Containers/StringConv.h"

#if PLATFORM_WINDOWS || PLATFORM_LINUX
#include <openssl/sha.h>
#endif

namespace DeferredTeleop::ArticulatedScene::Private
{

bool Fail(FString& OutError, const FString& Detail)
{
    OutError = Detail;
    return false;
}

bool DecodeUtf8Exact(
    const TArray<uint8>& Bytes,
    FString& OutJson,
    FString& OutError)
{
    OutJson.Reset();
    if (Bytes.Num() == 0)
    {
        return true;
    }
    if (Bytes.Contains(static_cast<uint8>(0)))
    {
        return Fail(OutError, TEXT("description file contains an embedded NUL byte"));
    }

    const ANSICHAR* Data = reinterpret_cast<const ANSICHAR*>(Bytes.GetData());
    const FUTF8ToTCHAR Converted(Data, Bytes.Num());
    if (Converted.Length() < 0 || Converted.Get() == nullptr)
    {
        return Fail(OutError, TEXT("description file is not valid UTF-8"));
    }
    OutJson = FString(Converted.Length(), Converted.Get());

    // A round trip catches malformed UTF-8 that the permissive conversion
    // helper would otherwise replace.  The parser still receives the exact
    // decoded text, and no normalization or JSON reserialization occurs.
    const FTCHARToUTF8 Reencoded(*OutJson);
    if (Reencoded.Length() != Bytes.Num()
        || FMemory::Memcmp(Reencoded.Get(), Bytes.GetData(), Bytes.Num()) != 0)
    {
        return Fail(OutError, TEXT("description file is not valid UTF-8"));
    }
    return true;
}

bool LoadLocalDescription(
    FDeferredTeleopArticulatedModelBinding& InOutBinding,
    FString& OutError)
{
    OutError.Reset();
    if (InOutBinding.RobotId.TrimStartAndEnd().IsEmpty())
    {
        return Fail(OutError, TEXT("articulated binding RobotId is required"));
    }
    if (InOutBinding.DescriptionFilePath.TrimStartAndEnd().IsEmpty())
    {
        return Fail(OutError, TEXT("articulated binding DescriptionFilePath is required"));
    }
    if (InOutBinding.ExpectedFrameId.TrimStartAndEnd().IsEmpty())
    {
        return Fail(OutError, TEXT("articulated binding ExpectedFrameId is required"));
    }
    if (InOutBinding.ExpectedCalibrationVersion.TrimStartAndEnd().IsEmpty())
    {
        return Fail(
            OutError,
            TEXT("articulated binding ExpectedCalibrationVersion is required"));
    }

    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *InOutBinding.DescriptionFilePath))
    {
        return Fail(
            OutError,
            FString::Printf(
                TEXT("could not read articulated description file: %s"),
                *InOutBinding.DescriptionFilePath));
    }

    FString Hash;
    if (!ComputeDescriptionHash(Bytes, Hash, OutError))
    {
        return false;
    }

    FString Json;
    if (!DecodeUtf8Exact(Bytes, Json, OutError))
    {
        return false;
    }

    FDttRobotDescription Description;
    if (!DeferredTeleop::RobotModel::ParseRobotDescriptionJson(Json, Description, OutError))
    {
        return false;
    }

    FDttValidatedRobotModel ValidatedModel;
    if (!DeferredTeleop::Kinematics::ValidateRobotDescription(
            Description,
            ValidatedModel,
            OutError))
    {
        return false;
    }

    InOutBinding.DescriptionBytes = MoveTemp(Bytes);
    InOutBinding.Description = MoveTemp(Description);
    InOutBinding.ValidatedModel = MoveTemp(ValidatedModel);
    InOutBinding.CachedDescriptionHash = MoveTemp(Hash);
    InOutBinding.CachedModelKey = FString::Printf(
        TEXT("%s/%s/%s"),
        *InOutBinding.Description.ModelId,
        *InOutBinding.Description.ModelRevision,
        *InOutBinding.CachedDescriptionHash);
    InOutBinding.bHasLoadedDescription = true;
    return true;
}

bool FailModel(FPreparedLayerState& OutPrepared, FString& OutError, const FString& Detail)
{
    OutPrepared.Failure = ELayerValidationFailure::InvalidModel;
    return Fail(OutError, Detail);
}

bool FailLayer(FPreparedLayerState& OutPrepared, FString& OutError, const FString& Detail)
{
    OutPrepared.Failure = ELayerValidationFailure::InvalidLayer;
    return Fail(OutError, Detail);
}

int32 FindJointByExactWireName(
    const FDttRobotDescription& Description,
    const FString& WireName)
{
    // This intentionally compares FString values before constructing an
    // FName.  FName comparisons are case-insensitive and cannot enforce the
    // wire contract's case-sensitive joint identity.
    for (int32 Index = 0; Index < Description.Joints.Num(); ++Index)
    {
        if (Description.Joints[Index].Name.ToString().Equals(
                WireName,
                ESearchCase::CaseSensitive))
        {
            return Index;
        }
    }
    return INDEX_NONE;
}

bool IsKnownJointWithDifferentCase(
    const FDttRobotDescription& Description,
    const FString& WireName)
{
    for (const FDttRobotJointDescription& Joint : Description.Joints)
    {
        const FString DescriptionName = Joint.Name.ToString();
        if (DescriptionName.Equals(WireName, ESearchCase::IgnoreCase)
            && !DescriptionName.Equals(WireName, ESearchCase::CaseSensitive))
        {
            return true;
        }
    }
    return false;
}

bool ValidateDirectEvidence(
    const FDeferredTeleopEvidence& Evidence,
    FString& OutError)
{
    if (Evidence.SourceIds.IsEmpty())
    {
        return Fail(OutError, TEXT("evidence.source_ids must be non-empty"));
    }
    for (const FString& SourceId : Evidence.SourceIds)
    {
        if (SourceId.TrimStartAndEnd().IsEmpty())
        {
            return Fail(OutError, TEXT("evidence.source_ids must contain non-blank values"));
        }
    }
    if (Evidence.ObservedAt.GetTicks() == 0
        || Evidence.ProducedAt.GetTicks() == 0)
    {
        return Fail(OutError, TEXT("evidence observed_at and produced_at are required"));
    }
    if (Evidence.Provenance == EDeferredTeleopProvenance::Unknown)
    {
        return Fail(OutError, TEXT("evidence provenance must be declared"));
    }
    if (Evidence.WorldRevision <= 0)
    {
        return Fail(OutError, TEXT("evidence world_revision must be positive"));
    }
    // Keep the same temporal relationship as the strict wire parser.  The
    // direct struct API does not impose a tighter clock or freshness policy.
    if (Evidence.ProducedAt < Evidence.ObservedAt)
    {
        return Fail(OutError, TEXT("evidence produced_at precedes observed_at"));
    }
    if (Evidence.bHasFreshUntil
        && (Evidence.FreshUntil.GetTicks() == 0
            || Evidence.FreshUntil < Evidence.ObservedAt))
    {
        return Fail(OutError, TEXT("evidence fresh_until is invalid"));
    }
    if ((Evidence.Provenance == EDeferredTeleopProvenance::Predicted
            || Evidence.Provenance == EDeferredTeleopProvenance::Simulated)
        && Evidence.ModelVersion.TrimStartAndEnd().IsEmpty())
    {
        return Fail(
            OutError,
            TEXT("predicted or simulated evidence requires model_version"));
    }
    return true;
}

} // namespace DeferredTeleop::ArticulatedScene::Private

void FDeferredTeleopArticulatedModelBinding::ResetRuntime()
{
    CachedDescriptionHash.Reset();
    CachedModelKey.Reset();
    DescriptionBytes.Reset();
    Description = FDttRobotDescription();
    ValidatedModel.Reset();
    bHasLoadedDescription = false;
}

namespace DeferredTeleop::ArticulatedScene
{

bool ComputeDescriptionHash(
    const TArray<uint8>& DescriptionBytes,
    FString& OutHash,
    FString& OutError)
{
    OutHash.Reset();
    OutError.Reset();

#if PLATFORM_WINDOWS || PLATFORM_LINUX
    uint8 Digest[SHA256_DIGEST_LENGTH] = {};
    const uint8 EmptyInput = 0;
    const uint8* Data = DescriptionBytes.Num() > 0
        ? DescriptionBytes.GetData()
        : &EmptyInput;
    if (SHA256(
            Data,
            static_cast<size_t>(DescriptionBytes.Num()),
            Digest)
        == nullptr)
    {
        return Private::Fail(OutError, TEXT("OpenSSL SHA-256 failed"));
    }
    OutHash = TEXT("sha256:");
    for (int32 Index = 0; Index < SHA256_DIGEST_LENGTH; ++Index)
    {
        OutHash += FString::Printf(TEXT("%02x"), Digest[Index]);
    }
    return true;
#else
    return Private::Fail(
        OutError,
        TEXT("articulated scene SHA-256 backend is unavailable on this target"));
#endif
}

bool ConfigureBinding(
    const FDeferredTeleopArticulatedModelBinding& RequestedBinding,
    FDeferredTeleopArticulatedModelBinding& OutBinding,
    FString& OutError)
{
    FDeferredTeleopArticulatedModelBinding Candidate = RequestedBinding;
    Candidate.ResetRuntime();
    if (!Private::LoadLocalDescription(Candidate, OutError))
    {
        return false;
    }
    OutBinding = MoveTemp(Candidate);
    return true;
}

bool ReloadLocalDescription(
    FDeferredTeleopArticulatedModelBinding& InOutBinding,
    FString& OutError)
{
    FDeferredTeleopArticulatedModelBinding Candidate = InOutBinding;
    Candidate.ResetRuntime();
    if (!Private::LoadLocalDescription(Candidate, OutError))
    {
        return false;
    }
    InOutBinding = MoveTemp(Candidate);
    return true;
}

bool ConvertRootPose(
    const FDeferredTeleopPose& Pose,
    FDttCanonicalTransform& OutRootTransform,
    FString& OutError)
{
    OutError.Reset();
    if (!FMath::IsFinite(Pose.PositionMetres.X)
        || !FMath::IsFinite(Pose.PositionMetres.Y)
        || !FMath::IsFinite(Pose.PositionMetres.Z)
        || !FMath::IsFinite(Pose.Orientation.X)
        || !FMath::IsFinite(Pose.Orientation.Y)
        || !FMath::IsFinite(Pose.Orientation.Z)
        || !FMath::IsFinite(Pose.Orientation.W))
    {
        return Private::Fail(OutError, TEXT("root_pose must contain only finite values"));
    }

    OutRootTransform = FDttCanonicalTransform::FromTranslationRotation(
        FVector3d(
            static_cast<double>(Pose.PositionMetres.X),
            static_cast<double>(Pose.PositionMetres.Y),
            static_cast<double>(Pose.PositionMetres.Z)),
        FQuat4d(
            static_cast<double>(Pose.Orientation.X),
            static_cast<double>(Pose.Orientation.Y),
            static_cast<double>(Pose.Orientation.Z),
            static_cast<double>(Pose.Orientation.W)));
    if (!OutRootTransform.IsRigid(1.0e-6))
    {
        return Private::Fail(OutError, TEXT("root_pose rotation must be a normalized quaternion"));
    }
    return true;
}

FString MakeModelKey(const FDeferredTeleopRobotModelReference& ModelReference)
{
    return FString::Printf(
        TEXT("%s/%s/%s"),
        *ModelReference.ModelId,
        *ModelReference.ModelRevision,
        *ModelReference.DescriptionHash);
}

bool PrepareLayerState(
    const FDeferredTeleopArticulatedModelBinding& Binding,
    const FDeferredTeleopArticulatedRobotState& RobotState,
    FPreparedLayerState& OutPrepared,
    FString& OutError)
{
    OutPrepared = FPreparedLayerState();
    OutPrepared.Evidence = RobotState.Evidence;
    OutPrepared.ModelReference = RobotState.ModelReference;
    OutPrepared.ModelKey = MakeModelKey(RobotState.ModelReference);
    OutError.Reset();

    if (!Binding.bHasLoadedDescription)
    {
        return Private::FailModel(
            OutPrepared,
            OutError,
            TEXT("InvalidModel: local articulated description is not configured"));
    }
    if (!RobotState.RobotId.Equals(Binding.RobotId, ESearchCase::CaseSensitive))
    {
        return Private::FailModel(
            OutPrepared,
            OutError,
            FString::Printf(
                TEXT("InvalidModel: robot_id mismatch: actual='%s', expected='%s'"),
                *RobotState.RobotId,
                *Binding.RobotId));
    }

    FDeferredTeleopRobotModelReference ExpectedReference;
    ExpectedReference.ModelId = Binding.Description.ModelId;
    ExpectedReference.ModelRevision = Binding.Description.ModelRevision;
    ExpectedReference.DescriptionHash = Binding.CachedDescriptionHash;
    FString ModelDiagnostic;
    if (!DeferredTeleop::ArticulatedView::CompareModelReference(
            RobotState.ModelReference,
            ExpectedReference,
            ModelDiagnostic))
    {
        return Private::FailModel(
            OutPrepared,
            OutError,
            TEXT("InvalidModel: ") + ModelDiagnostic);
    }

    if (!RobotState.RootPose.FrameId.Equals(
            Binding.ExpectedFrameId,
            ESearchCase::CaseSensitive))
    {
        return Private::FailModel(
            OutPrepared,
            OutError,
            FString::Printf(
                TEXT("InvalidModel: frame_id mismatch: actual='%s', expected='%s'"),
                *RobotState.RootPose.FrameId,
                *Binding.ExpectedFrameId));
    }
    if (!RobotState.RootPose.CalibrationVersion.Equals(
            Binding.ExpectedCalibrationVersion,
            ESearchCase::CaseSensitive))
    {
        return Private::FailModel(
            OutPrepared,
            OutError,
            FString::Printf(
                TEXT("InvalidModel: calibration_version mismatch: actual='%s', expected='%s'"),
                *RobotState.RootPose.CalibrationVersion,
                *Binding.ExpectedCalibrationVersion));
    }
    if (!ConvertRootPose(RobotState.RootPose, OutPrepared.RootTransform, OutError))
    {
        return Private::FailModel(OutPrepared, OutError, TEXT("InvalidModel: ") + OutError);
    }

    FString EvidenceError;
    if (!Private::ValidateDirectEvidence(RobotState.Evidence, EvidenceError))
    {
        return Private::FailLayer(
            OutPrepared,
            OutError,
            TEXT("InvalidLayer: ") + EvidenceError);
    }

    FTransform RootUnrealTransform;
    if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
            OutPrepared.RootTransform,
            RootUnrealTransform,
            OutError))
    {
        return Private::FailModel(OutPrepared, OutError, TEXT("InvalidModel: ") + OutError);
    }

    TArray<int32> WireJointIndexByDescription;
    WireJointIndexByDescription.Init(INDEX_NONE, Binding.Description.Joints.Num());
    for (int32 WireIndex = 0; WireIndex < RobotState.Joints.Num(); ++WireIndex)
    {
        const FDeferredTeleopArticulatedJointPosition& WireJoint = RobotState.Joints[WireIndex];
        if (WireJoint.JointName.TrimStartAndEnd().IsEmpty())
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                FString::Printf(TEXT("InvalidLayer: joints[%d] has an empty name"), WireIndex));
        }
        const int32 DescriptionJointIndex = Private::FindJointByExactWireName(
            Binding.Description,
            WireJoint.JointName);
        if (DescriptionJointIndex == INDEX_NONE)
        {
            const FString Detail = Private::IsKnownJointWithDifferentCase(
                Binding.Description,
                WireJoint.JointName)
                ? TEXT("joint name casing mismatch before FName conversion: ")
                : TEXT("unknown joint name: ");
            return Private::FailLayer(
                OutPrepared,
                OutError,
                TEXT("InvalidLayer: ") + Detail + WireJoint.JointName);
        }
        if (WireJointIndexByDescription[DescriptionJointIndex] != INDEX_NONE)
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                TEXT("InvalidLayer: duplicate joint name: ") + WireJoint.JointName);
        }
        if (Binding.Description.Joints[DescriptionJointIndex].Type
            != EDttRobotJointType::Revolute)
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                TEXT("InvalidLayer: joint input is not allowed for fixed joint: ")
                    + WireJoint.JointName);
        }
        if (!FMath::IsFinite(WireJoint.PositionRadians))
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                TEXT("InvalidLayer: non-finite joint position: ") + WireJoint.JointName);
        }
        WireJointIndexByDescription[DescriptionJointIndex] = WireIndex;
    }

    OutPrepared.OrderedJointPositions.Reserve(RobotState.Joints.Num());
    for (int32 DescriptionJointIndex = 0;
         DescriptionJointIndex < Binding.Description.Joints.Num();
         ++DescriptionJointIndex)
    {
        const FDttRobotJointDescription& DescriptionJoint =
            Binding.Description.Joints[DescriptionJointIndex];
        if (DescriptionJoint.Type != EDttRobotJointType::Revolute)
        {
            continue;
        }
        const int32 WireIndex = WireJointIndexByDescription[DescriptionJointIndex];
        if (WireIndex == INDEX_NONE)
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                TEXT("InvalidLayer: missing required revolute joint: ")
                    + DescriptionJoint.Name.ToString());
        }
        FDttNamedJointPosition& Ordered = OutPrepared.OrderedJointPositions.AddDefaulted_GetRef();
        Ordered.JointName = DescriptionJoint.Name;
        Ordered.PositionRadians = RobotState.Joints[WireIndex].PositionRadians;
    }

    if (!DeferredTeleop::Kinematics::EvaluateForwardKinematics(
            Binding.Description,
            OutPrepared.RootTransform,
            OutPrepared.OrderedJointPositions,
            OutPrepared.ForwardKinematics))
    {
        return Private::FailLayer(
            OutPrepared,
            OutError,
            TEXT("InvalidLayer: ") + OutPrepared.ForwardKinematics.ErrorMessage);
    }
    if (!OutPrepared.ForwardKinematics.bSuccess)
    {
        return Private::FailLayer(
            OutPrepared,
            OutError,
            TEXT("InvalidLayer: forward kinematics did not produce a pose"));
    }

    for (const FDttNamedCanonicalTransform& NamedTransform :
         OutPrepared.ForwardKinematics.LinkTransforms)
    {
        FTransform UnrealTransform;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                NamedTransform.Transform,
                UnrealTransform,
                OutError))
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                FString::Printf(
                    TEXT("InvalidLayer: link %s conversion failed: %s"),
                    *NamedTransform.Name.ToString(),
                    *OutError));
        }
    }
    for (const FDttNamedCanonicalTransform& NamedTransform :
         OutPrepared.ForwardKinematics.ToolTransforms)
    {
        FTransform UnrealTransform;
        if (!DeferredTeleop::Kinematics::ConvertCanonicalToUnrealTransform(
                NamedTransform.Transform,
                UnrealTransform,
                OutError))
        {
            return Private::FailLayer(
                OutPrepared,
                OutError,
                FString::Printf(
                    TEXT("InvalidLayer: tool %s conversion failed: %s"),
                    *NamedTransform.Name.ToString(),
                    *OutError));
        }
    }

    OutPrepared.bWithinJointLimits = OutPrepared.ForwardKinematics.bWithinJointLimits;
    OutPrepared.Failure = ELayerValidationFailure::None;
    return true;
}

} // namespace DeferredTeleop::ArticulatedScene
