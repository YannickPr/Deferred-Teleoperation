#include "Articulated/DeferredTeleopArticulatedViewParser.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace DeferredTeleop::ArticulatedView::Private
{
using FJsonObjectPtr = TSharedPtr<FJsonObject>;

constexpr double MaxArticulatedDelaySeconds = 86'400.0;

bool Fail(FString& OutError, const FString& Path, const FString& Detail)
{
    OutError = FString::Printf(TEXT("%s: %s"), *Path, *Detail);
    return false;
}

bool EqualsCaseSensitive(const FString& Value, const TCHAR* Expected)
{
    return Value.Equals(FString(Expected), ESearchCase::CaseSensitive);
}

bool RequireExactFields(
    const FJsonObjectPtr& Object,
    const std::initializer_list<const TCHAR*>& Names,
    const FString& Path,
    FString& OutError)
{
    if (!Object.IsValid())
    {
        return Fail(OutError, Path, TEXT("expected object"));
    }
    if (Object->Values.Num() != static_cast<int32>(Names.size()))
    {
        return Fail(OutError, Path, TEXT("missing or unknown field"));
    }
    for (const TCHAR* Name : Names)
    {
        if (!Object->HasField(FStringView(Name)))
        {
            return Fail(OutError, Path, FString::Printf(TEXT("missing field '%s'"), Name));
        }
    }
    return true;
}

bool ReadString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FString& OutValue,
    FString& OutError)
{
    if (!Object->TryGetStringField(FStringView(Name), OutValue)
        || OutValue.TrimStartAndEnd().IsEmpty())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected non-blank string"));
    }
    return true;
}

bool ReadLiteral(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const TCHAR* Expected,
    const FString& Path,
    FString& OutError)
{
    FString Value;
    if (!ReadString(Object, Name, Path, Value, OutError))
    {
        return false;
    }
    if (!EqualsCaseSensitive(Value, Expected))
    {
        return Fail(
            OutError,
            Path + TEXT(".") + Name,
            FString::Printf(TEXT("expected '%s'"), Expected));
    }
    return true;
}

bool ReadInteger(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    int32 Minimum,
    const FString& Path,
    int32& OutValue,
    FString& OutError)
{
    double Number = 0.0;
    if (!Object->TryGetNumberField(FStringView(Name), Number)
        || !FMath::IsFinite(Number)
        || Number != FMath::RoundToDouble(Number)
        || Number < static_cast<double>(Minimum)
        || Number > static_cast<double>(MAX_int32))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected bounded integer"));
    }
    OutValue = static_cast<int32>(Number);
    return true;
}

bool ReadNumber(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    double Minimum,
    double Maximum,
    const FString& Path,
    double& OutValue,
    FString& OutError)
{
    if (!Object->TryGetNumberField(FStringView(Name), OutValue)
        || !FMath::IsFinite(OutValue)
        || OutValue < Minimum
        || OutValue > Maximum)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected finite bounded number"));
    }
    return true;
}

bool ReadDateTime(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FDateTime& OutValue,
    FString& OutError)
{
    FString Encoded;
    if (!ReadString(Object, Name, Path, Encoded, OutError)
        || !FDateTime::ParseIso8601(*Encoded, OutValue))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected ISO-8601 timestamp"));
    }
    return true;
}

bool ReadNullableDateTime(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    bool& bOutPresent,
    FDateTime& OutValue,
    FString& OutError)
{
    const TSharedPtr<FJsonValue> Value = Object->TryGetField(FStringView(Name));
    if (!Value.IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("missing field"));
    }
    if (Value->Type == EJson::Null)
    {
        bOutPresent = false;
        OutValue = FDateTime();
        return true;
    }
    bOutPresent = true;
    return ReadDateTime(Object, Name, Path, OutValue, OutError);
}

bool ReadNullableString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FString& OutValue,
    FString& OutError)
{
    const TSharedPtr<FJsonValue> Value = Object->TryGetField(FStringView(Name));
    if (!Value.IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("missing field"));
    }
    if (Value->Type == EJson::Null)
    {
        OutValue.Reset();
        return true;
    }
    return ReadString(Object, Name, Path, OutValue, OutError);
}

bool ReadObject(
    const FJsonObjectPtr& Parent,
    const TCHAR* Name,
    const FString& Path,
    FJsonObjectPtr& OutObject,
    FString& OutError)
{
    const TSharedPtr<FJsonValue> Value = Parent->TryGetField(FStringView(Name));
    if (!Value.IsValid() || Value->Type != EJson::Object)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected object"));
    }
    OutObject = Value->AsObject();
    return OutObject.IsValid() || Fail(OutError, Path + TEXT(".") + Name, TEXT("invalid object"));
}

bool ReadNullableObject(
    const FJsonObjectPtr& Parent,
    const TCHAR* Name,
    const FString& Path,
    bool& bOutPresent,
    FJsonObjectPtr& OutObject,
    FString& OutError)
{
    const TSharedPtr<FJsonValue> Value = Parent->TryGetField(FStringView(Name));
    if (!Value.IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("missing field"));
    }
    if (Value->Type == EJson::Null)
    {
        bOutPresent = false;
        OutObject.Reset();
        return true;
    }
    if (Value->Type != EJson::Object)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected object or null"));
    }
    bOutPresent = true;
    OutObject = Value->AsObject();
    return OutObject.IsValid() || Fail(OutError, Path + TEXT(".") + Name, TEXT("invalid object"));
}

bool ParseProvenance(
    const FString& Value,
    EDeferredTeleopProvenance& OutValue,
    const FString& Path,
    FString& OutError)
{
    if (EqualsCaseSensitive(Value, TEXT("MEASURED")))
    {
        OutValue = EDeferredTeleopProvenance::Measured;
    }
    else if (EqualsCaseSensitive(Value, TEXT("FUSED")))
    {
        OutValue = EDeferredTeleopProvenance::Fused;
    }
    else if (EqualsCaseSensitive(Value, TEXT("OPERATOR_ASSERTED")))
    {
        OutValue = EDeferredTeleopProvenance::OperatorAsserted;
    }
    else if (EqualsCaseSensitive(Value, TEXT("INFERRED")))
    {
        OutValue = EDeferredTeleopProvenance::Inferred;
    }
    else if (EqualsCaseSensitive(Value, TEXT("PREDICTED")))
    {
        OutValue = EDeferredTeleopProvenance::Predicted;
    }
    else if (EqualsCaseSensitive(Value, TEXT("SIMULATED")))
    {
        OutValue = EDeferredTeleopProvenance::Simulated;
    }
    else
    {
        return Fail(OutError, Path, TEXT("unsupported provenance"));
    }
    return true;
}

bool ParsePose(
    const FJsonObjectPtr& Object,
    FDeferredTeleopPose& OutPose,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(Object, {TEXT("position"), TEXT("orientation"), TEXT("frame")}, Path, OutError))
    {
        return false;
    }

    FJsonObjectPtr Position;
    FJsonObjectPtr Orientation;
    FJsonObjectPtr Frame;
    if (!ReadObject(Object, TEXT("position"), Path, Position, OutError)
        || !ReadObject(Object, TEXT("orientation"), Path, Orientation, OutError)
        || !ReadObject(Object, TEXT("frame"), Path, Frame, OutError))
    {
        return false;
    }
    if (!RequireExactFields(Position, {TEXT("x"), TEXT("y"), TEXT("z")}, Path + TEXT(".position"), OutError)
        || !RequireExactFields(
            Orientation,
            {TEXT("x"), TEXT("y"), TEXT("z"), TEXT("w")},
            Path + TEXT(".orientation"),
            OutError)
        || !RequireExactFields(
            Frame,
            {
                TEXT("frame_id"),
                TEXT("convention"),
                TEXT("length_unit"),
                TEXT("angle_unit"),
                TEXT("calibration_version"),
            },
            Path + TEXT(".frame"),
            OutError))
    {
        return false;
    }

    double X = 0.0;
    double Y = 0.0;
    double Z = 0.0;
    double QX = 0.0;
    double QY = 0.0;
    double QZ = 0.0;
    double QW = 0.0;
    if (!ReadNumber(Position, TEXT("x"), -TNumericLimits<double>::Max(), TNumericLimits<double>::Max(), Path + TEXT(".position"), X, OutError)
        || !ReadNumber(Position, TEXT("y"), -TNumericLimits<double>::Max(), TNumericLimits<double>::Max(), Path + TEXT(".position"), Y, OutError)
        || !ReadNumber(Position, TEXT("z"), -TNumericLimits<double>::Max(), TNumericLimits<double>::Max(), Path + TEXT(".position"), Z, OutError)
        || !ReadNumber(Orientation, TEXT("x"), -1.0, 1.0, Path + TEXT(".orientation"), QX, OutError)
        || !ReadNumber(Orientation, TEXT("y"), -1.0, 1.0, Path + TEXT(".orientation"), QY, OutError)
        || !ReadNumber(Orientation, TEXT("z"), -1.0, 1.0, Path + TEXT(".orientation"), QZ, OutError)
        || !ReadNumber(Orientation, TEXT("w"), -1.0, 1.0, Path + TEXT(".orientation"), QW, OutError))
    {
        return false;
    }
    const double NormSquared = QX * QX + QY * QY + QZ * QZ + QW * QW;
    if (!FMath::IsFinite(NormSquared) || !FMath::IsNearlyEqual(NormSquared, 1.0, 1.0e-6))
    {
        return Fail(OutError, Path + TEXT(".orientation"), TEXT("quaternion is not unit length"));
    }
    if (!ReadString(Frame, TEXT("frame_id"), Path + TEXT(".frame"), OutPose.FrameId, OutError)
        || !ReadLiteral(Frame, TEXT("convention"), TEXT("RIGHT_HANDED_Z_UP"), Path + TEXT(".frame"), OutError)
        || !ReadLiteral(Frame, TEXT("length_unit"), TEXT("metre"), Path + TEXT(".frame"), OutError)
        || !ReadLiteral(Frame, TEXT("angle_unit"), TEXT("radian"), Path + TEXT(".frame"), OutError)
        || !ReadString(Frame, TEXT("calibration_version"), Path + TEXT(".frame"), OutPose.CalibrationVersion, OutError))
    {
        return false;
    }
    OutPose.PositionMetres = FVector(X, Y, Z);
    OutPose.Orientation = FQuat(QX, QY, QZ, QW);
    return true;
}

bool ParseEvidence(
    const FJsonObjectPtr& Object,
    FDeferredTeleopEvidence& OutEvidence,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {
                TEXT("source_ids"),
                TEXT("observed_at"),
                TEXT("produced_at"),
                TEXT("provenance"),
                TEXT("world_revision"),
                TEXT("fresh_until"),
                TEXT("model_version"),
            },
            Path,
            OutError))
    {
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* SourceValues = nullptr;
    if (!Object->TryGetArrayField(TEXT("source_ids"), SourceValues)
        || SourceValues == nullptr
        || SourceValues->IsEmpty())
    {
        return Fail(OutError, Path + TEXT(".source_ids"), TEXT("expected non-empty string array"));
    }
    OutEvidence.SourceIds.Reset();
    for (const TSharedPtr<FJsonValue>& SourceValue : *SourceValues)
    {
        FString Source;
        if (!SourceValue.IsValid()
            || !SourceValue->TryGetString(Source)
            || Source.TrimStartAndEnd().IsEmpty())
        {
            return Fail(OutError, Path + TEXT(".source_ids"), TEXT("expected non-blank string array"));
        }
        OutEvidence.SourceIds.Add(Source);
    }

    FString Provenance;
    if (!ReadDateTime(Object, TEXT("observed_at"), Path, OutEvidence.ObservedAt, OutError)
        || !ReadDateTime(Object, TEXT("produced_at"), Path, OutEvidence.ProducedAt, OutError)
        || !ReadString(Object, TEXT("provenance"), Path, Provenance, OutError)
        || !ParseProvenance(Provenance, OutEvidence.Provenance, Path + TEXT(".provenance"), OutError)
        || !ReadInteger(Object, TEXT("world_revision"), 1, Path, OutEvidence.WorldRevision, OutError)
        || !ReadNullableDateTime(Object, TEXT("fresh_until"), Path, OutEvidence.bHasFreshUntil, OutEvidence.FreshUntil, OutError)
        || !ReadNullableString(Object, TEXT("model_version"), Path, OutEvidence.ModelVersion, OutError))
    {
        return false;
    }
    if (OutEvidence.ProducedAt < OutEvidence.ObservedAt)
    {
        return Fail(OutError, Path, TEXT("produced_at precedes observed_at"));
    }
    if (OutEvidence.bHasFreshUntil && OutEvidence.FreshUntil < OutEvidence.ObservedAt)
    {
        return Fail(OutError, Path, TEXT("fresh_until precedes observed_at"));
    }
    if ((OutEvidence.Provenance == EDeferredTeleopProvenance::Predicted
            || OutEvidence.Provenance == EDeferredTeleopProvenance::Simulated)
        && OutEvidence.ModelVersion.IsEmpty())
    {
        return Fail(OutError, Path, TEXT("predicted evidence requires model_version"));
    }
    return true;
}

bool IsSha256Hash(const FString& Value)
{
    if (!Value.StartsWith(TEXT("sha256:"), ESearchCase::CaseSensitive) || Value.Len() != 71)
    {
        return false;
    }
    for (int32 Index = 7; Index < Value.Len(); ++Index)
    {
        const TCHAR Character = Value[Index];
        const bool bHex = (Character >= TEXT('0') && Character <= TEXT('9'))
            || (Character >= TEXT('a') && Character <= TEXT('f'));
        if (!bHex)
        {
            return false;
        }
    }
    return true;
}

bool ParseModelReference(
    const FJsonObjectPtr& Object,
    FDeferredTeleopRobotModelReference& OutReference,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {TEXT("model_id"), TEXT("model_revision"), TEXT("description_hash")},
            Path,
            OutError)
        || !ReadString(Object, TEXT("model_id"), Path, OutReference.ModelId, OutError)
        || !ReadString(Object, TEXT("model_revision"), Path, OutReference.ModelRevision, OutError)
        || !ReadString(Object, TEXT("description_hash"), Path, OutReference.DescriptionHash, OutError))
    {
        return false;
    }
    if (!IsSha256Hash(OutReference.DescriptionHash))
    {
        return Fail(
            OutError,
            Path + TEXT(".description_hash"),
            TEXT("expected sha256:<64 lowercase hexadecimal digits>"));
    }
    return true;
}

bool ParseArticulatedRobotState(
    const FJsonObjectPtr& Object,
    FDeferredTeleopArticulatedRobotState& OutState,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {TEXT("robot_id"), TEXT("model_reference"), TEXT("root_pose"), TEXT("joints"), TEXT("evidence")},
            Path,
            OutError))
    {
        return false;
    }

    FJsonObjectPtr ModelReference;
    FJsonObjectPtr RootPose;
    FJsonObjectPtr Evidence;
    if (!ReadString(Object, TEXT("robot_id"), Path, OutState.RobotId, OutError)
        || !ReadObject(Object, TEXT("model_reference"), Path, ModelReference, OutError)
        || !ParseModelReference(ModelReference, OutState.ModelReference, Path + TEXT(".model_reference"), OutError)
        || !ReadObject(Object, TEXT("root_pose"), Path, RootPose, OutError)
        || !ParsePose(RootPose, OutState.RootPose, Path + TEXT(".root_pose"), OutError)
        || !ReadObject(Object, TEXT("evidence"), Path, Evidence, OutError)
        || !ParseEvidence(Evidence, OutState.Evidence, Path + TEXT(".evidence"), OutError))
    {
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* JointValues = nullptr;
    if (!Object->TryGetArrayField(TEXT("joints"), JointValues)
        || JointValues == nullptr
        || JointValues->IsEmpty())
    {
        return Fail(OutError, Path + TEXT(".joints"), TEXT("expected non-empty object array"));
    }
    OutState.Joints.Reset();
    for (int32 Index = 0; Index < JointValues->Num(); ++Index)
    {
        const TSharedPtr<FJsonValue>& Value = (*JointValues)[Index];
        if (!Value.IsValid() || Value->Type != EJson::Object)
        {
            return Fail(OutError, Path + TEXT(".joints"), TEXT("expected non-empty object array"));
        }
        const FJsonObjectPtr JointObject = Value->AsObject();
        if (!RequireExactFields(
                JointObject,
                {TEXT("joint_name"), TEXT("position_radians")},
                FString::Printf(TEXT("%s.joints[%d]"), *Path, Index),
                OutError))
        {
            return false;
        }
        FDeferredTeleopArticulatedJointPosition Joint;
        const FString JointPath = FString::Printf(TEXT("%s.joints[%d]"), *Path, Index);
        if (!ReadString(JointObject, TEXT("joint_name"), JointPath, Joint.JointName, OutError)
            || !ReadNumber(
                JointObject,
                TEXT("position_radians"),
                -TNumericLimits<double>::Max(),
                TNumericLimits<double>::Max(),
                JointPath,
                Joint.PositionRadians,
                OutError))
        {
            return false;
        }
        if (OutState.Joints.ContainsByPredicate(
                [&Joint](const FDeferredTeleopArticulatedJointPosition& Existing)
                {
                    return Existing.JointName.Equals(
                        Joint.JointName,
                        ESearchCase::CaseSensitive);
                }))
        {
            return Fail(OutError, JointPath + TEXT(".joint_name"), TEXT("duplicate joint name"));
        }
        OutState.Joints.Add(MoveTemp(Joint));
    }
    return true;
}

bool ParseConnection(
    const FJsonObjectPtr& Object,
    FDeferredTeleopArticulatedConnectionStatus& OutConnection,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {TEXT("mission_to_field"), TEXT("changed_at"), TEXT("detail")},
            Path,
            OutError)
        || !ReadLiteral(Object, TEXT("detail"), TEXT("delayed-link"), Path, OutError)
        || !ReadDateTime(Object, TEXT("changed_at"), Path, OutConnection.ChangedAt, OutError))
    {
        return false;
    }
    OutConnection.Detail = TEXT("delayed-link");
    FString State;
    if (!ReadString(Object, TEXT("mission_to_field"), Path, State, OutError))
    {
        return false;
    }
    if (EqualsCaseSensitive(State, TEXT("DISCONNECTED")))
    {
        OutConnection.MissionToField = EDeferredTeleopConnectionState::Disconnected;
    }
    else if (EqualsCaseSensitive(State, TEXT("CONNECTING")))
    {
        OutConnection.MissionToField = EDeferredTeleopConnectionState::Connecting;
    }
    else if (EqualsCaseSensitive(State, TEXT("CONNECTED")))
    {
        OutConnection.MissionToField = EDeferredTeleopConnectionState::Connected;
    }
    else
    {
        return Fail(OutError, Path + TEXT(".mission_to_field"), TEXT("unsupported state"));
    }
    return true;
}

bool ReadNullableGuidString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FString& OutValue,
    FString& OutError)
{
    const TSharedPtr<FJsonValue> Value = Object->TryGetField(FStringView(Name));
    if (!Value.IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("missing field"));
    }
    if (Value->Type == EJson::Null)
    {
        OutValue.Reset();
        return true;
    }
    if (!ReadString(Object, Name, Path, OutValue, OutError))
    {
        return false;
    }
    FGuid Parsed;
    return FGuid::Parse(OutValue, Parsed)
        || Fail(OutError, Path + TEXT(".") + Name, TEXT("expected UUID or null"));
}

bool ParseStatus(
    const FJsonObjectPtr& Object,
    FDeferredTeleopMissionStatus& OutStatus,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {
                TEXT("operation_id"),
                TEXT("correlation_id"),
                TEXT("terminal_state"),
                TEXT("terminal_contract_id"),
                TEXT("received_message_count"),
            },
            Path,
            OutError)
        || !ReadNullableGuidString(Object, TEXT("operation_id"), Path, OutStatus.OperationId, OutError)
        || !ReadNullableGuidString(Object, TEXT("correlation_id"), Path, OutStatus.CorrelationId, OutError)
        || !ReadNullableString(Object, TEXT("terminal_state"), Path, OutStatus.TerminalState, OutError)
        || !ReadNullableGuidString(Object, TEXT("terminal_contract_id"), Path, OutStatus.TerminalContractId, OutError)
        || !ReadInteger(Object, TEXT("received_message_count"), 0, Path, OutStatus.ReceivedMessageCount, OutError))
    {
        return false;
    }
    if (!OutStatus.TerminalState.IsEmpty()
        && !EqualsCaseSensitive(OutStatus.TerminalState, TEXT("SUCCEEDED"))
        && !EqualsCaseSensitive(OutStatus.TerminalState, TEXT("FAILED"))
        && !EqualsCaseSensitive(OutStatus.TerminalState, TEXT("HELD"))
        && !EqualsCaseSensitive(OutStatus.TerminalState, TEXT("CANCELLED")))
    {
        return Fail(OutError, Path + TEXT(".terminal_state"), TEXT("unsupported terminal state"));
    }
    return true;
}

bool ParseArrival(
    const FJsonObjectPtr& Object,
    FDeferredTeleopArticulatedArrivalRobotState& OutArrival,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {
                TEXT("robot_state"),
                TEXT("predicted_for"),
                TEXT("estimated_intent_arrival_at"),
                TEXT("link_one_way_delay_seconds"),
            },
            Path,
            OutError))
    {
        return false;
    }
    FJsonObjectPtr RobotState;
    double Delay = 0.0;
    if (!ReadObject(Object, TEXT("robot_state"), Path, RobotState, OutError)
        || !ParseArticulatedRobotState(RobotState, OutArrival.RobotState, Path + TEXT(".robot_state"), OutError)
        || !ReadDateTime(Object, TEXT("predicted_for"), Path, OutArrival.PredictedFor, OutError)
        || !ReadNullableDateTime(
            Object,
            TEXT("estimated_intent_arrival_at"),
            Path,
            OutArrival.bHasEstimatedIntentArrival,
            OutArrival.EstimatedIntentArrivalAt,
            OutError)
        || !ReadNumber(
            Object,
            TEXT("link_one_way_delay_seconds"),
            0.0,
            MaxArticulatedDelaySeconds,
            Path,
            Delay,
            OutError))
    {
        return false;
    }
    if (OutArrival.RobotState.Evidence.Provenance != EDeferredTeleopProvenance::Predicted)
    {
        return Fail(OutError, Path + TEXT(".robot_state.evidence.provenance"), TEXT("expected PREDICTED"));
    }
    if (OutArrival.PredictedFor <= OutArrival.RobotState.Evidence.ProducedAt)
    {
        return Fail(OutError, Path + TEXT(".predicted_for"), TEXT("must be after robot_state evidence produced_at"));
    }
    OutArrival.LinkOneWayDelaySeconds = Delay;
    OutArrival.bAvailable = true;
    return true;
}
}

namespace DeferredTeleop::ArticulatedView
{
using Private::FJsonObjectPtr;
using Private::Fail;
using Private::ParseArticulatedRobotState;
using Private::ParseArrival;
using Private::ParseConnection;
using Private::ParseStatus;
using Private::ReadDateTime;
using Private::ReadInteger;
using Private::ReadLiteral;
using Private::ReadNullableObject;
using Private::ReadNullableString;
using Private::RequireExactFields;
using Private::ReadObject;
using Private::ReadString;

bool CompareModelReference(
    const FDeferredTeleopRobotModelReference& Actual,
    const FDeferredTeleopRobotModelReference& Expected,
    FString& OutDiagnostic)
{
    OutDiagnostic.Reset();
    if (!Actual.ModelId.Equals(Expected.ModelId, ESearchCase::CaseSensitive))
    {
        OutDiagnostic = FString::Printf(
            TEXT("model_id mismatch: actual='%s', expected='%s'"),
            *Actual.ModelId,
            *Expected.ModelId);
        return false;
    }
    if (!Actual.ModelRevision.Equals(Expected.ModelRevision, ESearchCase::CaseSensitive))
    {
        OutDiagnostic = FString::Printf(
            TEXT("model_revision mismatch: actual='%s', expected='%s'"),
            *Actual.ModelRevision,
            *Expected.ModelRevision);
        return false;
    }
    if (!Actual.DescriptionHash.Equals(Expected.DescriptionHash, ESearchCase::CaseSensitive))
    {
        OutDiagnostic = FString::Printf(
            TEXT("description_hash mismatch: actual='%s', expected='%s'"),
            *Actual.DescriptionHash,
            *Expected.DescriptionHash);
        return false;
    }
    return true;
}

bool ParseArticulated(
    const FString& Json,
    FDeferredTeleopArticulatedViewState& OutState,
    FString& OutError)
{
    OutError.Reset();
    FJsonObjectPtr Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        return Fail(OutError, TEXT("$"), TEXT("malformed JSON object"));
    }
    if (!RequireExactFields(
            Root,
            {
                TEXT("protocol_version"),
                TEXT("message_type"),
                TEXT("source_id"),
                TEXT("source_sequence"),
                TEXT("produced_at"),
                TEXT("connection"),
                TEXT("status"),
                TEXT("confirmed_robot_state"),
                TEXT("arrival_robot_state"),
                TEXT("target_robot_state"),
            },
            TEXT("$"),
            OutError))
    {
        return false;
    }

    FDeferredTeleopArticulatedViewState Parsed;
    FJsonObjectPtr Connection;
    FJsonObjectPtr Status;
    if (!ReadLiteral(Root, TEXT("protocol_version"), TEXT("dtt/0"), TEXT("$"), OutError)
        || !ReadLiteral(Root, TEXT("message_type"), TEXT("mission.articulated_view_state"), TEXT("$"), OutError)
        || !ReadString(Root, TEXT("protocol_version"), TEXT("$"), Parsed.ProtocolVersion, OutError)
        || !ReadString(Root, TEXT("message_type"), TEXT("$"), Parsed.MessageType, OutError)
        || !ReadString(Root, TEXT("source_id"), TEXT("$"), Parsed.SourceId, OutError)
        || !ReadInteger(Root, TEXT("source_sequence"), 1, TEXT("$"), Parsed.SourceSequence, OutError)
        || !ReadDateTime(Root, TEXT("produced_at"), TEXT("$"), Parsed.ProducedAt, OutError)
        || !ReadObject(Root, TEXT("connection"), TEXT("$"), Connection, OutError)
        || !ParseConnection(Connection, Parsed.Connection, TEXT("$.connection"), OutError)
        || !ReadObject(Root, TEXT("status"), TEXT("$"), Status, OutError)
        || !ParseStatus(Status, Parsed.Status, TEXT("$.status"), OutError))
    {
        return false;
    }

    bool bPresent = false;
    FJsonObjectPtr Optional;
    if (!ReadNullableObject(Root, TEXT("confirmed_robot_state"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent)
    {
        if (!ParseArticulatedRobotState(
                Optional,
                Parsed.ConfirmedRobotState,
                TEXT("$.confirmed_robot_state"),
                OutError))
        {
            return false;
        }
        if (Parsed.ConfirmedRobotState.Evidence.Provenance != EDeferredTeleopProvenance::Measured
            && Parsed.ConfirmedRobotState.Evidence.Provenance != EDeferredTeleopProvenance::Fused)
        {
            return Fail(
                OutError,
                TEXT("$.confirmed_robot_state.evidence.provenance"),
                TEXT("expected MEASURED or FUSED"));
        }
        Parsed.bHasConfirmedRobotState = true;
    }

    if (!ReadNullableObject(Root, TEXT("arrival_robot_state"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent
        && !ParseArrival(Optional, Parsed.ArrivalRobotState, TEXT("$.arrival_robot_state"), OutError))
    {
        return false;
    }

    if (!ReadNullableObject(Root, TEXT("target_robot_state"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent)
    {
        if (!ParseArticulatedRobotState(
                Optional,
                Parsed.TargetRobotState,
                TEXT("$.target_robot_state"),
                OutError))
        {
            return false;
        }
        if (Parsed.TargetRobotState.Evidence.Provenance != EDeferredTeleopProvenance::OperatorAsserted)
        {
            return Fail(
                OutError,
                TEXT("$.target_robot_state.evidence.provenance"),
                TEXT("expected OPERATOR_ASSERTED"));
        }
        Parsed.bHasTargetRobotState = true;
    }

    // Assign only after every field and nested layer has passed validation.  Callers can keep
    // rendering their last valid frame after a malformed update.
    OutState = MoveTemp(Parsed);
    return true;
}
}
