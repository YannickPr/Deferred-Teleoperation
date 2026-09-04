#include "RobotModel/DeferredTeleopRobotModelTypes.h"

#include "Kinematics/DeferredTeleopKinematicsLibrary.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace DeferredTeleop::RobotModel::Private
{
using FJsonObjectPtr = TSharedPtr<FJsonObject>;
using FJsonValuePtr = TSharedPtr<FJsonValue>;

bool Fail(FString& OutError, const FString& Path, const FString& Detail)
{
    OutError = FString::Printf(TEXT("%s: %s"), *Path, *Detail);
    return false;
}

bool RequireExactFields(
    const FJsonObjectPtr& Object,
    std::initializer_list<const TCHAR*> Names,
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
        return Fail(
            OutError,
            Path + TEXT(".") + Name,
            TEXT("expected non-empty string"));
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

bool ReadNumberValue(
    const FJsonValuePtr& Value,
    const FString& Path,
    double& OutValue,
    FString& OutError)
{
    if (!Value.IsValid() || Value->Type != EJson::Number)
    {
        return Fail(OutError, Path, TEXT("expected finite number"));
    }
    OutValue = Value->AsNumber();
    if (!FMath::IsFinite(OutValue))
    {
        return Fail(OutError, Path, TEXT("expected finite number"));
    }
    return true;
}

bool ReadBoolean(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    bool& OutValue,
    FString& OutError)
{
    if (!Object->TryGetBoolField(FStringView(Name), OutValue))
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected boolean"));
    }
    return true;
}

bool ReadObject(
    const FJsonObjectPtr& Parent,
    const TCHAR* Name,
    const FString& Path,
    FJsonObjectPtr& OutObject,
    FString& OutError)
{
    const FJsonValuePtr Value = Parent->TryGetField(FStringView(Name));
    if (!Value.IsValid() || Value->Type != EJson::Object || !Value->AsObject().IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected object"));
    }
    OutObject = Value->AsObject();
    return true;
}

bool ReadVectorArray(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    FDttCanonicalVector& OutVector,
    FString& OutError)
{
    const TArray<FJsonValuePtr>* Values = nullptr;
    if (!Object->TryGetArrayField(FStringView(Name), Values) || Values == nullptr)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected three-number array"));
    }
    if (Values->Num() != 3)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected exactly three numbers"));
    }

    double Components[3] = {0.0, 0.0, 0.0};
    for (int32 Index = 0; Index < 3; ++Index)
    {
        if (!ReadNumberValue(
                (*Values)[Index],
                FString::Printf(TEXT("%s.%s[%d]"), *Path, Name, Index),
                Components[Index],
                OutError))
        {
            return false;
        }
    }
    OutVector.X = Components[0];
    OutVector.Y = Components[1];
    OutVector.Z = Components[2];
    return true;
}

bool ReadNullableVectorArray(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    bool& bOutPresent,
    FDttCanonicalVector& OutVector,
    FString& OutError)
{
    const FJsonValuePtr Value = Object->TryGetField(FStringView(Name));
    if (!Value.IsValid())
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("missing field"));
    }
    if (Value->Type == EJson::Null)
    {
        bOutPresent = false;
        OutVector = FDttCanonicalVector();
        return true;
    }
    bOutPresent = true;
    return ReadVectorArray(Object, Name, Path, OutVector, OutError);
}

bool ReadCanonicalTransform(
    const FJsonObjectPtr& Object,
    const FString& Path,
    FDttCanonicalTransform& OutTransform,
    FString& OutError)
{
    if (!RequireExactFields(
            Object,
            {TEXT("translation_m"), TEXT("rotation_xyzw")},
            Path,
            OutError))
    {
        return false;
    }

    FDttCanonicalVector Translation;
    if (!ReadVectorArray(Object, TEXT("translation_m"), Path, Translation, OutError))
    {
        return false;
    }
    OutTransform.TranslationMetres = Translation;

    const TArray<FJsonValuePtr>* RotationValues = nullptr;
    if (!Object->TryGetArrayField(FStringView(TEXT("rotation_xyzw")), RotationValues)
        || RotationValues == nullptr
        || RotationValues->Num() != 4)
    {
        return Fail(
            OutError,
            Path + TEXT(".rotation_xyzw"),
            TEXT("expected exactly four numbers"));
    }
    double QuaternionComponents[4] = {0.0, 0.0, 0.0, 0.0};
    for (int32 Index = 0; Index < 4; ++Index)
    {
        if (!ReadNumberValue(
                (*RotationValues)[Index],
                FString::Printf(TEXT("%s.rotation_xyzw[%d]"), *Path, Index),
                QuaternionComponents[Index],
                OutError))
        {
            return false;
        }
    }
    OutTransform.Rotation.X = QuaternionComponents[0];
    OutTransform.Rotation.Y = QuaternionComponents[1];
    OutTransform.Rotation.Z = QuaternionComponents[2];
    OutTransform.Rotation.W = QuaternionComponents[3];
    if (!OutTransform.IsRigid(1.0e-6))
    {
        return Fail(
            OutError,
            Path,
            TEXT("transform must contain finite translation and a unit quaternion"));
    }
    return true;
}

bool ReadArrayField(
    const FJsonObjectPtr& Object,
    const TCHAR* Name,
    const FString& Path,
    const TArray<FJsonValuePtr>*& OutValues,
    FString& OutError)
{
    if (!Object->TryGetArrayField(FStringView(Name), OutValues) || OutValues == nullptr)
    {
        return Fail(OutError, Path + TEXT(".") + Name, TEXT("expected array"));
    }
    return true;
}

bool ValidateObjectArray(
    const TArray<FJsonValuePtr>& Values,
    const FString& Path,
    FString& OutError)
{
    for (int32 Index = 0; Index < Values.Num(); ++Index)
    {
        if (!Values[Index].IsValid() || Values[Index]->Type != EJson::Object)
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("%s[%d]"), *Path, Index),
                TEXT("expected object"));
        }
    }
    return true;
}
} // namespace DeferredTeleop::RobotModel::Private

namespace DeferredTeleop::RobotModel
{
using namespace Private;

bool ParseRobotDescriptionJson(
    const FString& Json,
    FDttRobotDescription& OutDescription,
    FString& OutError)
{
    OutError.Reset();
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        return Fail(OutError, TEXT("$"), TEXT("malformed JSON object"));
    }
    if (!RequireExactFields(
            Root,
            {
                TEXT("schema_version"),
                TEXT("model_id"),
                TEXT("model_revision"),
                TEXT("source"),
                TEXT("coordinate_convention"),
                TEXT("root_link"),
                TEXT("links"),
                TEXT("joints"),
                TEXT("joint_groups"),
                TEXT("tool_frames"),
                TEXT("known_limitations"),
            },
            TEXT("$"),
            OutError))
    {
        return false;
    }

    if (!ReadLiteral(
            Root,
            TEXT("schema_version"),
            TEXT("dtt.robot-description/0"),
            TEXT("$"),
            OutError))
    {
        return false;
    }

    FDttRobotDescription Parsed;
    if (!ReadString(Root, TEXT("model_id"), TEXT("$"), Parsed.ModelId, OutError)
        || !ReadString(Root, TEXT("model_revision"), TEXT("$"), Parsed.ModelRevision, OutError))
    {
        return false;
    }

    // FName cannot be written through FString::ToString().  Re-read root_link
    // into a temporary after validating it as a non-empty string.
    FString RootLink;
    if (!ReadString(Root, TEXT("root_link"), TEXT("$"), RootLink, OutError))
    {
        return false;
    }
    Parsed.RootLinkName = FName(*RootLink);

    FJsonObjectPtr Source;
    FJsonObjectPtr Convention;
    if (!ReadObject(Root, TEXT("source"), TEXT("$"), Source, OutError)
        || !ReadObject(Root, TEXT("coordinate_convention"), TEXT("$"), Convention, OutError))
    {
        return false;
    }
    if (!RequireExactFields(
            Source,
            {
                TEXT("repository"),
                TEXT("commit"),
                TEXT("path"),
                TEXT("git_blob_sha1"),
                TEXT("licence"),
                TEXT("vendor_modified"),
            },
            TEXT("$.source"),
            OutError)
        || !RequireExactFields(
            Convention,
            {
                TEXT("handedness"),
                TEXT("up_axis"),
                TEXT("length_unit"),
                TEXT("angle_unit"),
                TEXT("rotation_representation"),
                TEXT("transform_notation"),
            },
            TEXT("$.coordinate_convention"),
            OutError))
    {
        return false;
    }

    // Source metadata is checked for the fields and scalar types required by
    // the generated contract, but it is deliberately not retained in
    // FDttRobotDescription or used by FK.
    bool bVendorModified = false;
    FString SourceMetadata;
    if (!ReadString(Source, TEXT("repository"), TEXT("$.source"), SourceMetadata, OutError)
        || !ReadString(Source, TEXT("commit"), TEXT("$.source"), SourceMetadata, OutError)
        || !ReadString(Source, TEXT("path"), TEXT("$.source"), SourceMetadata, OutError)
        || !ReadString(Source, TEXT("git_blob_sha1"), TEXT("$.source"), SourceMetadata, OutError)
        || !ReadString(Source, TEXT("licence"), TEXT("$.source"), SourceMetadata, OutError)
        || !ReadBoolean(Source, TEXT("vendor_modified"), TEXT("$.source"), bVendorModified, OutError)
        || !ReadLiteral(Convention, TEXT("handedness"), TEXT("RIGHT_HANDED"), TEXT("$.coordinate_convention"), OutError)
        || !ReadLiteral(Convention, TEXT("up_axis"), TEXT("Z"), TEXT("$.coordinate_convention"), OutError)
        || !ReadLiteral(Convention, TEXT("length_unit"), TEXT("metre"), TEXT("$.coordinate_convention"), OutError)
        || !ReadLiteral(Convention, TEXT("angle_unit"), TEXT("radian"), TEXT("$.coordinate_convention"), OutError)
        || !ReadLiteral(Convention, TEXT("rotation_representation"), TEXT("quaternion_xyzw"), TEXT("$.coordinate_convention"), OutError)
        || !ReadLiteral(Convention, TEXT("transform_notation"), TEXT("parent_T_child"), TEXT("$.coordinate_convention"), OutError))
    {
        return false;
    }
    (void)SourceMetadata;
    (void)bVendorModified;

    const TArray<FJsonValuePtr>* LinkValues = nullptr;
    const TArray<FJsonValuePtr>* JointValues = nullptr;
    const TArray<FJsonValuePtr>* GroupValues = nullptr;
    const TArray<FJsonValuePtr>* ToolValues = nullptr;
    const TArray<FJsonValuePtr>* LimitationValues = nullptr;
    if (!ReadArrayField(Root, TEXT("links"), TEXT("$"), LinkValues, OutError)
        || !ReadArrayField(Root, TEXT("joints"), TEXT("$"), JointValues, OutError)
        || !ReadArrayField(Root, TEXT("joint_groups"), TEXT("$"), GroupValues, OutError)
        || !ReadArrayField(Root, TEXT("tool_frames"), TEXT("$"), ToolValues, OutError)
        || !ReadArrayField(Root, TEXT("known_limitations"), TEXT("$"), LimitationValues, OutError)
        || !ValidateObjectArray(*LinkValues, TEXT("$.links"), OutError)
        || !ValidateObjectArray(*JointValues, TEXT("$.joints"), OutError)
        || !ValidateObjectArray(*GroupValues, TEXT("$.joint_groups"), OutError)
        || !ValidateObjectArray(*ToolValues, TEXT("$.tool_frames"), OutError))
    {
        return false;
    }

    for (int32 Index = 0; Index < LimitationValues->Num(); ++Index)
    {
        FString Limitation;
        if (!(*LimitationValues)[Index].IsValid()
            || !(*LimitationValues)[Index]->TryGetString(Limitation)
            || Limitation.IsEmpty())
        {
            return Fail(
                OutError,
                FString::Printf(TEXT("$.known_limitations[%d]"), Index),
                TEXT("expected non-empty string"));
        }
    }

    Parsed.Links.Reserve(LinkValues->Num());
    for (int32 Index = 0; Index < LinkValues->Num(); ++Index)
    {
        const FJsonObjectPtr Link = (*LinkValues)[Index]->AsObject();
        if (!RequireExactFields(
                Link,
                {TEXT("name"), TEXT("visuals")},
                FString::Printf(TEXT("$.links[%d]"), Index),
                OutError))
        {
            return false;
        }
        FDttRobotLinkDescription ParsedLink;
        FString Name;
        if (!ReadString(
                Link,
                TEXT("name"),
                FString::Printf(TEXT("$.links[%d]"), Index),
                Name,
                OutError))
        {
            return false;
        }
        const TArray<FJsonValuePtr>* VisualValues = nullptr;
        // Visual metadata is deliberately bounded to an array of JSON
        // objects.  Its fields are not part of this kinematics slice and are
        // neither retained nor used by FK.
        if (!ReadArrayField(
                Link,
                TEXT("visuals"),
                FString::Printf(TEXT("$.links[%d]"), Index),
                VisualValues,
                OutError)
            || !ValidateObjectArray(
                *VisualValues,
                FString::Printf(TEXT("$.links[%d].visuals"), Index),
                OutError))
        {
            return false;
        }
        ParsedLink.Name = FName(*Name);
        Parsed.Links.Add(MoveTemp(ParsedLink));
    }

    Parsed.Joints.Reserve(JointValues->Num());
    for (int32 Index = 0; Index < JointValues->Num(); ++Index)
    {
        const FString Path = FString::Printf(TEXT("$.joints[%d]"), Index);
        const FJsonObjectPtr Joint = (*JointValues)[Index]->AsObject();
        if (!RequireExactFields(
                Joint,
                {
                    TEXT("name"),
                    TEXT("type"),
                    TEXT("parent_link"),
                    TEXT("child_link"),
                    TEXT("parent_to_joint"),
                    TEXT("axis_joint_frame"),
                    TEXT("position_limits_rad"),
                },
                Path,
                OutError))
        {
            return false;
        }

        FDttRobotJointDescription ParsedJoint;
        FString Name;
        FString Type;
        FString ParentLink;
        FString ChildLink;
        if (!ReadString(Joint, TEXT("name"), Path, Name, OutError)
            || !ReadString(Joint, TEXT("type"), Path, Type, OutError)
            || !ReadString(Joint, TEXT("parent_link"), Path, ParentLink, OutError)
            || !ReadString(Joint, TEXT("child_link"), Path, ChildLink, OutError))
        {
            return false;
        }
        FJsonObjectPtr ParentToJoint;
        if (!ReadObject(Joint, TEXT("parent_to_joint"), Path, ParentToJoint, OutError)
            || !ReadCanonicalTransform(ParentToJoint, Path + TEXT(".parent_to_joint"), ParsedJoint.ParentToJoint, OutError))
        {
            return false;
        }
        bool bHasAxis = false;
        if (!ReadNullableVectorArray(
                Joint,
                TEXT("axis_joint_frame"),
                Path,
                bHasAxis,
                ParsedJoint.AxisJointFrame,
                OutError))
        {
            return false;
        }
        const FJsonValuePtr LimitValue = Joint->TryGetField(FStringView(TEXT("position_limits_rad")));
        if (!LimitValue.IsValid())
        {
            return Fail(OutError, Path + TEXT(".position_limits_rad"), TEXT("missing field"));
        }
        if (LimitValue->Type == EJson::Null)
        {
            ParsedJoint.bHasPositionLimits = false;
        }
        else
        {
            if (LimitValue->Type != EJson::Object)
            {
                return Fail(
                    OutError,
                    Path + TEXT(".position_limits_rad"),
                    TEXT("expected object or null"));
            }
            const FJsonObjectPtr Limit = LimitValue->AsObject();
            if (!Limit.IsValid()
                || !RequireExactFields(
                    Limit,
                    {TEXT("lower"), TEXT("upper")},
                    Path + TEXT(".position_limits_rad"),
                    OutError))
            {
                return Fail(
                    OutError,
                    Path + TEXT(".position_limits_rad"),
                    TEXT("expected object or null"));
            }
            if (!Limit->TryGetNumberField(FStringView(TEXT("lower")), ParsedJoint.LowerPositionRadians)
                || !FMath::IsFinite(ParsedJoint.LowerPositionRadians)
                || !Limit->TryGetNumberField(FStringView(TEXT("upper")), ParsedJoint.UpperPositionRadians)
                || !FMath::IsFinite(ParsedJoint.UpperPositionRadians))
            {
                return Fail(
                    OutError,
                    Path + TEXT(".position_limits_rad"),
                    TEXT("limits must be finite numbers"));
            }
            ParsedJoint.bHasPositionLimits = true;
        }

        ParsedJoint.Name = FName(*Name);
        ParsedJoint.ParentLink = FName(*ParentLink);
        ParsedJoint.ChildLink = FName(*ChildLink);
        if (Type == TEXT("fixed"))
        {
            if (bHasAxis || ParsedJoint.bHasPositionLimits)
            {
                return Fail(
                    OutError,
                    Path,
                    TEXT("fixed joint must have null axis and limits"));
            }
            ParsedJoint.Type = EDttRobotJointType::Fixed;
        }
        else if (Type == TEXT("revolute"))
        {
            if (!bHasAxis)
            {
                return Fail(OutError, Path + TEXT(".axis_joint_frame"), TEXT("required for revolute joint"));
            }
            ParsedJoint.Type = EDttRobotJointType::Revolute;
        }
        else
        {
            return Fail(
                OutError,
                Path + TEXT(".type"),
                TEXT("expected 'fixed' or 'revolute'"));
        }
        Parsed.Joints.Add(MoveTemp(ParsedJoint));
    }

    Parsed.JointGroups.Reserve(GroupValues->Num());
    for (int32 Index = 0; Index < GroupValues->Num(); ++Index)
    {
        const FString Path = FString::Printf(TEXT("$.joint_groups[%d]"), Index);
        const FJsonObjectPtr Group = (*GroupValues)[Index]->AsObject();
        if (!RequireExactFields(Group, {TEXT("name"), TEXT("joints")}, Path, OutError))
        {
            return false;
        }

        FString Name;
        if (!ReadString(Group, TEXT("name"), Path, Name, OutError))
        {
            return false;
        }
        const TArray<FJsonValuePtr>* JointNameValues = nullptr;
        if (!ReadArrayField(Group, TEXT("joints"), Path, JointNameValues, OutError))
        {
            return false;
        }

        FDttRobotJointGroupDescription ParsedGroup;
        ParsedGroup.Name = FName(*Name);
        ParsedGroup.JointNames.Reserve(JointNameValues->Num());
        for (int32 JointNameIndex = 0; JointNameIndex < JointNameValues->Num(); ++JointNameIndex)
        {
            FString JointName;
            if (!(*JointNameValues)[JointNameIndex].IsValid()
                || !(*JointNameValues)[JointNameIndex]->TryGetString(JointName)
                || JointName.IsEmpty())
            {
                return Fail(
                    OutError,
                    FString::Printf(TEXT("%s.joints[%d]"), *Path, JointNameIndex),
                    TEXT("expected non-empty string"));
            }
            ParsedGroup.JointNames.Add(FName(*JointName));
        }
        Parsed.JointGroups.Add(MoveTemp(ParsedGroup));
    }

    Parsed.ToolFrames.Reserve(ToolValues->Num());
    for (int32 Index = 0; Index < ToolValues->Num(); ++Index)
    {
        const FString Path = FString::Printf(TEXT("$.tool_frames[%d]"), Index);
        const FJsonObjectPtr Tool = (*ToolValues)[Index]->AsObject();
        if (!RequireExactFields(Tool, {TEXT("name"), TEXT("link")}, Path, OutError))
        {
            return false;
        }
        FString Name;
        FString Link;
        if (!ReadString(Tool, TEXT("name"), Path, Name, OutError)
            || !ReadString(Tool, TEXT("link"), Path, Link, OutError))
        {
            return false;
        }
        FDttRobotToolFrameDescription ParsedTool;
        ParsedTool.Name = FName(*Name);
        ParsedTool.LinkName = FName(*Link);
        ParsedTool.LinkToTool = FDttCanonicalTransform::Identity();
        Parsed.ToolFrames.Add(MoveTemp(ParsedTool));
    }

    FDttValidatedRobotModel Validated;
    if (!DeferredTeleop::Kinematics::ValidateRobotDescription(Parsed, Validated, OutError))
    {
        return false;
    }
    OutDescription = MoveTemp(Parsed);
    return true;
}
} // namespace DeferredTeleop::RobotModel
