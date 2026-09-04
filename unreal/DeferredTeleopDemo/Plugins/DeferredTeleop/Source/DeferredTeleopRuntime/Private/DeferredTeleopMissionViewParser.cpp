#include "DeferredTeleopMissionViewParser.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
using FJsonObjectPtr = TSharedPtr<FJsonObject>;

bool Fail(FString& OutError, const FString& Path, const FString& Detail)
{
    OutError = FString::Printf(TEXT("%s: %s"), *Path, *Detail);
    return false;
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
    if (!Object->TryGetStringField(FStringView(Name), OutValue) || OutValue.IsEmpty())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected non-empty string"));
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
    if (Value != Expected)
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
        || !FMath::IsNearlyEqual(Number, FMath::RoundToDouble(Number))
        || Number < static_cast<double>(Minimum)
        || Number > static_cast<double>(MAX_int32))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected bounded integer"));
    }
    OutValue = static_cast<int32>(Number);
    return true;
}

bool ReadFloat(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    double Minimum,
    const FString& Path,
    double& OutValue,
    FString& OutError)
{
    if (!Object->TryGetNumberField(FStringView(Name), OutValue)
        || !FMath::IsFinite(OutValue)
        || OutValue < Minimum)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected finite number"));
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

bool ReadOptionalObject(
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

bool ReadGuidString(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FString& OutValue,
    FString& OutError)
{
    FGuid Parsed;
    if (!ReadString(Object, Name, Path, OutValue, OutError) || !FGuid::Parse(OutValue, Parsed))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected UUID"));
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
    if (!ReadNullableString(Object, Name, Path, OutValue, OutError))
    {
        return false;
    }
    FGuid Parsed;
    if (!OutValue.IsEmpty() && !FGuid::Parse(OutValue, Parsed))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected UUID or null"));
    }
    return true;
}

bool ParseProvenance(
    const FString& Value,
    EDeferredTeleopProvenance& OutValue,
    const FString& Path,
    FString& OutError)
{
    if (Value == TEXT("MEASURED"))
    {
        OutValue = EDeferredTeleopProvenance::Measured;
    }
    else if (Value == TEXT("FUSED"))
    {
        OutValue = EDeferredTeleopProvenance::Fused;
    }
    else if (Value == TEXT("OPERATOR_ASSERTED"))
    {
        OutValue = EDeferredTeleopProvenance::OperatorAsserted;
    }
    else if (Value == TEXT("INFERRED"))
    {
        OutValue = EDeferredTeleopProvenance::Inferred;
    }
    else if (Value == TEXT("PREDICTED"))
    {
        OutValue = EDeferredTeleopProvenance::Predicted;
    }
    else if (Value == TEXT("SIMULATED"))
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
    if (!ReadFloat(Position, TEXT("x"), -TNumericLimits<double>::Max(), Path + TEXT(".position"), X, OutError)
        || !ReadFloat(Position, TEXT("y"), -TNumericLimits<double>::Max(), Path + TEXT(".position"), Y, OutError)
        || !ReadFloat(Position, TEXT("z"), -TNumericLimits<double>::Max(), Path + TEXT(".position"), Z, OutError)
        || !ReadFloat(Orientation, TEXT("x"), -1.0, Path + TEXT(".orientation"), QX, OutError)
        || !ReadFloat(Orientation, TEXT("y"), -1.0, Path + TEXT(".orientation"), QY, OutError)
        || !ReadFloat(Orientation, TEXT("z"), -1.0, Path + TEXT(".orientation"), QZ, OutError)
        || !ReadFloat(Orientation, TEXT("w"), -1.0, Path + TEXT(".orientation"), QW, OutError))
    {
        return false;
    }
    const double NormSquared = QX * QX + QY * QY + QZ * QZ + QW * QW;
    if (!FMath::IsNearlyEqual(NormSquared, 1.0, 1.0e-5))
    {
        return Fail(OutError, Path + TEXT(".orientation"), TEXT("quaternion is not unit length"));
    }
    if (!ReadString(Frame, TEXT("frame_id"), Path + TEXT(".frame"), OutPose.FrameId, OutError)
        || !ReadLiteral(Frame, TEXT("convention"), TEXT("RIGHT_HANDED_Z_UP"), Path + TEXT(".frame"), OutError)
        || !ReadLiteral(Frame, TEXT("length_unit"), TEXT("metre"), Path + TEXT(".frame"), OutError)
        || !ReadLiteral(Frame, TEXT("angle_unit"), TEXT("radian"), Path + TEXT(".frame"), OutError)
        || !ReadString(
            Frame,
            TEXT("calibration_version"),
            Path + TEXT(".frame"),
            OutPose.CalibrationVersion,
            OutError))
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
        if (!SourceValue.IsValid() || !SourceValue->TryGetString(Source) || Source.IsEmpty())
        {
            return Fail(OutError, Path + TEXT(".source_ids"), TEXT("expected non-empty string array"));
        }
        OutEvidence.SourceIds.Add(Source);
    }

    FString Provenance;
    if (!ReadDateTime(Object, TEXT("observed_at"), Path, OutEvidence.ObservedAt, OutError)
        || !ReadDateTime(Object, TEXT("produced_at"), Path, OutEvidence.ProducedAt, OutError)
        || !ReadString(Object, TEXT("provenance"), Path, Provenance, OutError)
        || !ParseProvenance(Provenance, OutEvidence.Provenance, Path + TEXT(".provenance"), OutError)
        || !ReadInteger(Object, TEXT("world_revision"), 1, Path, OutEvidence.WorldRevision, OutError)
        || !ReadNullableDateTime(
            Object,
            TEXT("fresh_until"),
            Path,
            OutEvidence.bHasFreshUntil,
            OutEvidence.FreshUntil,
            OutError)
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

bool ParseConfirmed(
    const FJsonObjectPtr& Object,
    FDeferredTeleopConfirmedState& OutState,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {TEXT("site_id"), TEXT("robot_id"), TEXT("pose"), TEXT("evidence")},
        Path,
        OutError))
    {
        return false;
    }
    FJsonObjectPtr Pose;
    FJsonObjectPtr Evidence;
    if (!ReadString(Object, TEXT("site_id"), Path, OutState.SiteId, OutError)
        || !ReadString(Object, TEXT("robot_id"), Path, OutState.RobotId, OutError)
        || !ReadObject(Object, TEXT("pose"), Path, Pose, OutError)
        || !ReadObject(Object, TEXT("evidence"), Path, Evidence, OutError)
        || !ParsePose(Pose, OutState.Pose, Path + TEXT(".pose"), OutError)
        || !ParseEvidence(Evidence, OutState.Evidence, Path + TEXT(".evidence"), OutError))
    {
        return false;
    }
    OutState.bAvailable = true;
    return true;
}

bool ParseArrival(
    const FJsonObjectPtr& Object,
    FDeferredTeleopArrivalBelief& OutState,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {
            TEXT("robot_id"),
            TEXT("pose"),
            TEXT("predicted_for"),
            TEXT("estimated_intent_arrival_at"),
            TEXT("link_one_way_delay_seconds"),
            TEXT("evidence"),
        },
        Path,
        OutError))
    {
        return false;
    }
    FJsonObjectPtr Pose;
    FJsonObjectPtr Evidence;
    double Delay = 0.0;
    if (!ReadString(Object, TEXT("robot_id"), Path, OutState.RobotId, OutError)
        || !ReadObject(Object, TEXT("pose"), Path, Pose, OutError)
        || !ReadDateTime(Object, TEXT("predicted_for"), Path, OutState.PredictedFor, OutError)
        || !ReadNullableDateTime(
            Object,
            TEXT("estimated_intent_arrival_at"),
            Path,
            OutState.bHasEstimatedIntentArrival,
            OutState.EstimatedIntentArrivalAt,
            OutError)
        || !ReadFloat(Object, TEXT("link_one_way_delay_seconds"), 0.0, Path, Delay, OutError)
        || !ReadObject(Object, TEXT("evidence"), Path, Evidence, OutError)
        || !ParsePose(Pose, OutState.Pose, Path + TEXT(".pose"), OutError)
        || !ParseEvidence(Evidence, OutState.Evidence, Path + TEXT(".evidence"), OutError))
    {
        return false;
    }
    if (OutState.Evidence.Provenance != EDeferredTeleopProvenance::Predicted)
    {
        return Fail(OutError, Path + TEXT(".evidence.provenance"), TEXT("expected PREDICTED"));
    }
    OutState.LinkOneWayDelaySeconds = static_cast<float>(Delay);
    OutState.bAvailable = true;
    return true;
}

bool ParseTarget(
    const FJsonObjectPtr& Object,
    FDeferredTeleopTargetBranch& OutState,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {
            TEXT("entity_id"),
            TEXT("requested_state"),
            TEXT("condition"),
            TEXT("pose"),
            TEXT("evidence"),
        },
        Path,
        OutError))
    {
        return false;
    }
    FJsonObjectPtr Pose;
    FJsonObjectPtr Evidence;
    if (!ReadString(Object, TEXT("entity_id"), Path, OutState.EntityId, OutError)
        || !ReadString(Object, TEXT("requested_state"), Path, OutState.RequestedState, OutError)
        || OutState.RequestedState != TEXT("PRESSED")
        || !ReadString(Object, TEXT("condition"), Path, OutState.Condition, OutError)
        || OutState.Condition != TEXT("button effect succeeds")
        || !ReadObject(Object, TEXT("pose"), Path, Pose, OutError)
        || !ReadObject(Object, TEXT("evidence"), Path, Evidence, OutError)
        || !ParsePose(Pose, OutState.Pose, Path + TEXT(".pose"), OutError)
        || !ParseEvidence(Evidence, OutState.Evidence, Path + TEXT(".evidence"), OutError))
    {
        if (OutError.IsEmpty())
        {
            return Fail(OutError, Path, TEXT("unsupported target branch"));
        }
        return false;
    }
    if (OutState.Evidence.Provenance != EDeferredTeleopProvenance::OperatorAsserted)
    {
        return Fail(
            OutError,
            Path + TEXT(".evidence.provenance"),
            TEXT("expected OPERATOR_ASSERTED"));
    }
    OutState.bAvailable = true;
    return true;
}

bool ParseTrajectorySample(
    const FJsonObjectPtr& Object,
    FDeferredTeleopTimedTrajectorySample& OutSample,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {
            TEXT("sample_time"),
            TEXT("timestamp_basis"),
            TEXT("pose"),
            TEXT("source"),
            TEXT("provenance"),
        },
        Path,
        OutError))
    {
        return false;
    }
    FJsonObjectPtr Pose;
    FString Source;
    FString Provenance;
    if (!ReadDateTime(Object, TEXT("sample_time"), Path, OutSample.SampleTime, OutError)
        || !ReadString(Object, TEXT("timestamp_basis"), Path, OutSample.TimestampBasis, OutError)
        || OutSample.TimestampBasis != TEXT("WALL_CLOCK_UTC")
        || !ReadObject(Object, TEXT("pose"), Path, Pose, OutError)
        || !ParsePose(Pose, OutSample.Pose, Path + TEXT(".pose"), OutError)
        || !ReadString(Object, TEXT("source"), Path, Source, OutError)
        || !ReadString(Object, TEXT("provenance"), Path, Provenance, OutError)
        || !ParseProvenance(Provenance, OutSample.Provenance, Path + TEXT(".provenance"), OutError))
    {
        if (OutError.IsEmpty())
        {
            return Fail(OutError, Path, TEXT("unsupported trajectory sample"));
        }
        return false;
    }
    if (Source == TEXT("CONFIRMED_STATE"))
    {
        OutSample.Source = EDeferredTeleopTrajectorySource::ConfirmedState;
    }
    else if (Source == TEXT("ARRIVAL_BELIEF"))
    {
        OutSample.Source = EDeferredTeleopTrajectorySource::ArrivalBelief;
    }
    else
    {
        return Fail(OutError, Path + TEXT(".source"), TEXT("unsupported trajectory source"));
    }
    return true;
}

bool ParseManifest(
    const FJsonObjectPtr& Object,
    FDeferredTeleopPredictionManifestSummary& OutManifest,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {
            TEXT("manifest_id"),
            TEXT("site_id"),
            TEXT("forecast_ids"),
            TEXT("generated_for_world_revision"),
            TEXT("evidence"),
        },
        Path,
        OutError))
    {
        return false;
    }
    const TArray<TSharedPtr<FJsonValue>>* ForecastValues = nullptr;
    FJsonObjectPtr Evidence;
    if (!ReadGuidString(Object, TEXT("manifest_id"), Path, OutManifest.ManifestId, OutError)
        || !ReadString(Object, TEXT("site_id"), Path, OutManifest.SiteId, OutError)
        || !Object->TryGetArrayField(TEXT("forecast_ids"), ForecastValues)
        || ForecastValues == nullptr
        || ForecastValues->IsEmpty()
        || !ReadInteger(
            Object,
            TEXT("generated_for_world_revision"),
            1,
            Path,
            OutManifest.GeneratedForWorldRevision,
            OutError)
        || !ReadObject(Object, TEXT("evidence"), Path, Evidence, OutError)
        || !ParseEvidence(Evidence, OutManifest.Evidence, Path + TEXT(".evidence"), OutError))
    {
        if (OutError.IsEmpty())
        {
            return Fail(OutError, Path + TEXT(".forecast_ids"), TEXT("expected UUID array"));
        }
        return false;
    }
    OutManifest.ForecastIds.Reset();
    for (const TSharedPtr<FJsonValue>& Value : *ForecastValues)
    {
        FString ForecastId;
        FGuid Parsed;
        if (!Value.IsValid() || !Value->TryGetString(ForecastId) || !FGuid::Parse(ForecastId, Parsed))
        {
            return Fail(OutError, Path + TEXT(".forecast_ids"), TEXT("expected UUID array"));
        }
        OutManifest.ForecastIds.Add(ForecastId);
    }
    if (OutManifest.Evidence.Provenance != EDeferredTeleopProvenance::Predicted)
    {
        return Fail(OutError, Path + TEXT(".evidence.provenance"), TEXT("expected PREDICTED"));
    }
    return true;
}

bool ParseConnection(
    const FJsonObjectPtr& Object,
    FDeferredTeleopMissionViewState& OutState,
    const FString& Path,
    FString& OutError)
{
    if (!RequireExactFields(
        Object,
        {TEXT("mission_to_field"), TEXT("changed_at"), TEXT("detail")},
        Path,
        OutError)
        || !ReadLiteral(Object, TEXT("detail"), TEXT("delayed-link"), Path, OutError)
        || !ReadDateTime(
            Object,
            TEXT("changed_at"),
            Path,
            OutState.MissionConnectionChangedAt,
            OutError))
    {
        return false;
    }
    FString State;
    if (!ReadString(Object, TEXT("mission_to_field"), Path, State, OutError))
    {
        return false;
    }
    if (State == TEXT("DISCONNECTED"))
    {
        OutState.MissionToField = EDeferredTeleopConnectionState::Disconnected;
    }
    else if (State == TEXT("CONNECTING"))
    {
        OutState.MissionToField = EDeferredTeleopConnectionState::Connecting;
    }
    else if (State == TEXT("CONNECTED"))
    {
        OutState.MissionToField = EDeferredTeleopConnectionState::Connected;
    }
    else
    {
        return Fail(OutError, Path + TEXT(".mission_to_field"), TEXT("unsupported state"));
    }
    return true;
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
        || !ReadNullableGuidString(
            Object,
            TEXT("terminal_contract_id"),
            Path,
            OutStatus.TerminalContractId,
            OutError)
        || !ReadInteger(
            Object,
            TEXT("received_message_count"),
            0,
            Path,
            OutStatus.ReceivedMessageCount,
            OutError))
    {
        return false;
    }
    if (!OutStatus.TerminalState.IsEmpty()
        && OutStatus.TerminalState != TEXT("SUCCEEDED")
        && OutStatus.TerminalState != TEXT("FAILED")
        && OutStatus.TerminalState != TEXT("HELD")
        && OutStatus.TerminalState != TEXT("CANCELLED"))
    {
        return Fail(OutError, Path + TEXT(".terminal_state"), TEXT("unsupported terminal state"));
    }
    return true;
}
}

namespace DeferredTeleop::MissionView
{
bool Parse(const FString& Json, FDeferredTeleopMissionViewState& OutState, FString& OutError)
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
            TEXT("confirmed_state"),
            TEXT("arrival_belief"),
            TEXT("target_branch"),
            TEXT("trajectory_forecasts"),
            TEXT("prediction_manifests"),
            TEXT("status"),
        },
        TEXT("$"),
        OutError))
    {
        return false;
    }

    FDeferredTeleopMissionViewState Parsed;
    FJsonObjectPtr Connection;
    FJsonObjectPtr Status;
    if (!ReadLiteral(Root, TEXT("protocol_version"), TEXT("dtt/0"), TEXT("$"), OutError)
        || !ReadLiteral(Root, TEXT("message_type"), TEXT("mission.view_state"), TEXT("$"), OutError)
        || !ReadString(Root, TEXT("protocol_version"), TEXT("$"), Parsed.ProtocolVersion, OutError)
        || !ReadString(Root, TEXT("source_id"), TEXT("$"), Parsed.SourceId, OutError)
        || !ReadInteger(Root, TEXT("source_sequence"), 1, TEXT("$"), Parsed.SourceSequence, OutError)
        || !ReadDateTime(Root, TEXT("produced_at"), TEXT("$"), Parsed.ProducedAt, OutError)
        || !ReadObject(Root, TEXT("connection"), TEXT("$"), Connection, OutError)
        || !ParseConnection(Connection, Parsed, TEXT("$.connection"), OutError)
        || !ReadObject(Root, TEXT("status"), TEXT("$"), Status, OutError)
        || !ParseStatus(Status, Parsed.Status, TEXT("$.status"), OutError))
    {
        return false;
    }

    FJsonObjectPtr Optional;
    bool bPresent = false;
    if (!ReadOptionalObject(Root, TEXT("confirmed_state"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent
        && !ParseConfirmed(Optional, Parsed.ConfirmedState, TEXT("$.confirmed_state"), OutError))
    {
        return false;
    }
    if (!ReadOptionalObject(Root, TEXT("arrival_belief"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent
        && !ParseArrival(Optional, Parsed.ArrivalBelief, TEXT("$.arrival_belief"), OutError))
    {
        return false;
    }
    if (!ReadOptionalObject(Root, TEXT("target_branch"), TEXT("$"), bPresent, Optional, OutError))
    {
        return false;
    }
    if (bPresent
        && !ParseTarget(Optional, Parsed.TargetBranch, TEXT("$.target_branch"), OutError))
    {
        return false;
    }

    const TArray<TSharedPtr<FJsonValue>>* TrajectoryValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("trajectory_forecasts"), TrajectoryValues)
        || TrajectoryValues == nullptr)
    {
        return Fail(OutError, TEXT("$.trajectory_forecasts"), TEXT("expected array"));
    }
    for (int32 Index = 0; Index < TrajectoryValues->Num(); ++Index)
    {
        const TSharedPtr<FJsonValue>& Value = (*TrajectoryValues)[Index];
        if (!Value.IsValid() || Value->Type != EJson::Object)
        {
            return Fail(OutError, TEXT("$.trajectory_forecasts"), TEXT("expected object array"));
        }
        FDeferredTeleopTimedTrajectorySample Sample;
        if (!ParseTrajectorySample(
            Value->AsObject(),
            Sample,
            FString::Printf(TEXT("$.trajectory_forecasts[%d]"), Index),
            OutError))
        {
            return false;
        }
        Parsed.TrajectoryForecasts.Add(MoveTemp(Sample));
    }

    const TArray<TSharedPtr<FJsonValue>>* ManifestValues = nullptr;
    if (!Root->TryGetArrayField(TEXT("prediction_manifests"), ManifestValues)
        || ManifestValues == nullptr)
    {
        return Fail(OutError, TEXT("$.prediction_manifests"), TEXT("expected array"));
    }
    for (int32 Index = 0; Index < ManifestValues->Num(); ++Index)
    {
        const TSharedPtr<FJsonValue>& Value = (*ManifestValues)[Index];
        if (!Value.IsValid() || Value->Type != EJson::Object)
        {
            return Fail(OutError, TEXT("$.prediction_manifests"), TEXT("expected object array"));
        }
        FDeferredTeleopPredictionManifestSummary Manifest;
        if (!ParseManifest(
            Value->AsObject(),
            Manifest,
            FString::Printf(TEXT("$.prediction_manifests[%d]"), Index),
            OutError))
        {
            return false;
        }
        Parsed.PredictionManifests.Add(MoveTemp(Manifest));
    }

    OutState = MoveTemp(Parsed);
    return true;
}
}
